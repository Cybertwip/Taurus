"""
Taurus Schematic – KiCad-native backend with Eagle fallback.

Public API:
    Schematic: init_libraries, init_device_set, init_device, add_instance, wire_up, save, load
    Instance:  wire(pin, target_instance, target_pin)
    Symbol / Descriptor: hierarchical symbol helpers
"""
from __future__ import annotations

import math
import os
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import sexp as S

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------
KICAD_SCHEMATIC_VERSION = "20250114"
DEFAULT_KICAD_SHARED = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport")

SUPPORTED_LIBRARY_ALIASES = {
    "transistor-npn": {"prefixes": {"Q"}},
    "resistor-power": {"prefixes": {"R"}},
}
REQUIRED_LIBRARY_ALIAS = {"Q": "transistor-npn", "R": "resistor-power"}

# ---------------------------------------------------------------------------
#  KiCad symbol specs – positions in **schematic** coords (Y-down)
#  Library .kicad_sym files use Y-up; we negate Y and swap 90/270.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KiCadSymbolSpec:
    library_id: str
    library_file: str
    symbol_name: str
    description: str
    pin_order: Tuple[str, ...]
    pin_positions: Dict[str, Tuple[float, float]]  # schematic Y-down
    pin_orientations: Dict[str, int]                # schematic orientations
    reference_offset: Tuple[float, float, int]      # schematic Y-down
    value_offset: Tuple[float, float, int]


SYMBOL_SPECS: Dict[str, KiCadSymbolSpec] = {
    "Q": KiCadSymbolSpec(
        library_id="Device:Q_NPN",
        library_file="Device.kicad_sym",
        symbol_name="Q_NPN",
        description="NPN bipolar junction transistor",
        pin_order=("B", "C", "E"),
        pin_positions={
            "B": (-5.08, 0.0),   # base: left
            "C": (2.54, -5.08),  # collector: top  (lib Y=5.08 → sch Y=-5.08)
            "E": (2.54, 5.08),   # emitter: bottom (lib Y=-5.08 → sch Y=5.08)
        },
        pin_orientations={
            "B": 0,    # pin extends right → outward left
            "C": 90,   # pin extends down  → outward up   (lib 270→sch 90)
            "E": 270,  # pin extends up    → outward down  (lib 90→sch 270)
        },
        reference_offset=(5.08, -1.27, 0),
        value_offset=(5.08, 1.27, 0),
    ),
    "R": KiCadSymbolSpec(
        library_id="Device:R",
        library_file="Device.kicad_sym",
        symbol_name="R",
        description="Resistor",
        pin_order=("1", "2"),
        pin_positions={
            "1": (0.0, -3.81),  # top pin  (lib Y=3.81 → sch Y=-3.81)
            "2": (0.0, 3.81),   # bottom pin (lib Y=-3.81 → sch Y=3.81)
        },
        pin_orientations={
            "1": 90,   # pin down → outward up  (lib 270→sch 90)
            "2": 270,  # pin up   → outward down (lib 90→sch 270)
        },
        reference_offset=(2.032, 0.0, 90),
        value_offset=(0.0, 0.0, 90),
    ),
}

_DYNAMIC_SYMBOL_CACHE: Dict[Tuple[str, str, str], KiCadSymbolSpec] = {}

# ---------------------------------------------------------------------------
#  Geometry helpers
# ---------------------------------------------------------------------------
def _fc(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _uid(*parts: object) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "taurus::" + "::".join(str(p) for p in parts)))


def _rot(pt: Tuple[float, float], deg: int) -> Tuple[float, float]:
    x, y = pt
    d = deg % 360
    if d == 0:   return (x, y)
    if d == 90:  return (y, -x)
    if d == 180: return (-x, -y)
    if d == 270: return (-y, x)
    raise ValueError(deg)


def _outward(orientation: int) -> Tuple[float, float]:
    d = orientation % 360
    if d == 0:   return (-1.0, 0.0)
    if d == 90:  return (0.0, -1.0)
    if d == 180: return (1.0, 0.0)
    if d == 270: return (0.0, 1.0)
    raise ValueError(orientation)


def _lib_angle_to_schematic(angle: int) -> int:
    return {0: 0, 90: 270, 180: 180, 270: 90}[angle % 360]


def _pin_label_geometry(x: float, y: float, orientation: int,
                        length: float) -> Tuple[float, float, int, str]:
    dx, dy = _outward(orientation)
    lx, ly = x + dx * length, y + dy * length
    if dx > 0:
        return lx, ly, 180, "right bottom"
    if dx < 0:
        return lx, ly, 0, "left bottom"
    if dy < 0:
        return lx, ly, 90, "left bottom"
    return lx, ly, 270, "right bottom"


def _find_property_node(symbol_node, property_name: str):
    for item in symbol_node[1:]:
        if isinstance(item, list) and item and item[0] == "property" and len(item) >= 3:
            if item[1] == property_name:
                return item
    return None


def _property_offset(symbol_node, property_name: str,
                     default: Tuple[float, float, int] = (0.0, 0.0, 0)) -> Tuple[float, float, int]:
    prop = _find_property_node(symbol_node, property_name)
    if prop is None:
        return default
    at_node = S.find(prop, "at")
    if not at_node or len(at_node) < 3:
        return default
    angle = int(float(at_node[3])) if len(at_node) >= 4 else 0
    return (
        float(at_node[1]),
        -float(at_node[2]),
        _lib_angle_to_schematic(angle),
    )


def _load_kicad_symbol_spec(shared: Path, library_id: str,
                            library_file: Optional[str] = None,
                            symbol_name: Optional[str] = None) -> KiCadSymbolSpec:
    library_name, local_name = library_id.split(":", 1)
    library_file = library_file or f"{library_name}.kicad_sym"
    symbol_name = symbol_name or local_name
    cache_key = (str(shared), library_file, library_id)
    if cache_key in _DYNAMIC_SYMBOL_CACHE:
        return _DYNAMIC_SYMBOL_CACHE[cache_key]

    text = (shared / "symbols" / library_file).read_text(encoding="utf-8")
    block = _extract_sym_block(text, f'(symbol "{symbol_name}"')
    symbol_node = S.parse(block)[0]

    pin_order: List[str] = []
    pin_positions: Dict[str, Tuple[float, float]] = {}
    pin_orientations: Dict[str, int] = {}
    for pin_node in S.find_all(symbol_node, "pin"):
        name_node = S.find(pin_node, "name")
        number_node = S.find(pin_node, "number")
        at_node = S.find(pin_node, "at")
        if not at_node or len(at_node) < 3:
            continue
        pin_name = str(name_node[1]) if name_node and len(name_node) >= 2 else ""
        pin_number = str(number_node[1]) if number_node and len(number_node) >= 2 else pin_name
        canonical_pin = pin_number or pin_name
        if not canonical_pin or canonical_pin in pin_positions:
            continue
        angle = int(float(at_node[3])) if len(at_node) >= 4 else 0
        pin_order.append(canonical_pin)
        pin_positions[canonical_pin] = (float(at_node[1]), -float(at_node[2]))
        pin_orientations[canonical_pin] = _lib_angle_to_schematic(angle)
        if pin_name and pin_name != "~" and pin_name not in pin_positions:
            pin_positions[pin_name] = pin_positions[canonical_pin]
            pin_orientations[pin_name] = pin_orientations[canonical_pin]

    spec = KiCadSymbolSpec(
        library_id=library_id,
        library_file=library_file,
        symbol_name=symbol_name,
        description=S.get_property(symbol_node, "Description") or local_name,
        pin_order=tuple(pin_order),
        pin_positions=pin_positions,
        pin_orientations=pin_orientations,
        reference_offset=_property_offset(symbol_node, "Reference"),
        value_offset=_property_offset(symbol_node, "Value"),
    )
    _DYNAMIC_SYMBOL_CACHE[cache_key] = spec
    return spec


# ---------------------------------------------------------------------------
#  Internal data model
# ---------------------------------------------------------------------------
class UnionFind:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)


@dataclass
class Device:
    name: str
    package: Optional[str] = None


@dataclass
class DeviceSet:
    name: str
    prefix: str
    symbol_spec: KiCadSymbolSpec
    devices: Dict[str, Device] = field(default_factory=dict)


@dataclass
class PlacedPart:
    reference: str
    device_set: DeviceSet
    device: Device
    prefix: str
    x: float = 0.0
    y: float = 0.0
    rotation: int = 0


@dataclass
class PinLabel:
    reference: str
    pin_name: str
    net_name: str
    length: float = 5.08
    label_type: str = "label"  # "label", "global_input", "global_output"


@dataclass
class GraphicText:
    text: str
    x: float
    y: float
    rotation: int = 0
    justify: str = "left bottom"
    size: float = 1.27


@dataclass
class GraphicPolyline:
    points: List[Tuple[float, float]]
    stroke_width: float = 0.1524
    stroke_type: str = "dash"


class Instance:
    def __init__(self, component: PlacedPart, schematic: "Schematic",
                 part_name: str, device_set: str, prefix: str):
        self.component = component
        self.schematic = schematic
        self.connections: Dict[str, List[Tuple["Instance", str]]] = defaultdict(list)
        self.device_set = device_set
        self.part = part_name
        self.prefix = prefix

    def wire(self, pin_name: str, target: "Instance", target_pin: str):
        pair = (target, target_pin)
        if pair not in self.connections[pin_name]:
            self.connections[pin_name].append(pair)

    def __getitem__(self, pin_name: str):
        return (self.component.reference, pin_name)


# ---------------------------------------------------------------------------
#  Routing engine – MST + L-shape
# ---------------------------------------------------------------------------
class _Router:
    """Route explicit wire connections using minimum-spanning-tree with
    L-shaped (one-bend) segments.  No grid-based BFS – always succeeds."""
    GRID_MM = 1.27

    def __init__(self, schematic: "Schematic"):
        self.sch = schematic
        self.nets: List[Tuple[str, Set[Tuple[str, str]]]] = []
        self.wires: List[Tuple[float, float, float, float]] = []
        self.junctions: Set[Tuple[float, float]] = set()
        self.labels: List[Tuple[str, float, float, int, str]] = []

    def run(self):
        self._build_nets()
        self._route()

    # -- net discovery -------------------------------------------------------
    def _build_nets(self):
        uf = UnionFind()
        nodes: Set[Tuple[str, str]] = set()
        for inst in self.sch.instances:
            for pin, targets in inst.connections.items():
                for tgt, tgt_pin in targets:
                    a = (inst.component.reference, pin)
                    b = (tgt.component.reference, tgt_pin)
                    uf.union(a, b)
                    nodes |= {a, b}
        groups: Dict[object, Set[Tuple[str, str]]] = defaultdict(set)
        for n in nodes:
            groups[uf.find(n)].add(n)
        self.nets = []
        for idx, members in enumerate(sorted(sorted(g) for g in groups.values()), 1):
            self.nets.append((f"N{idx}", set(members)))

    # -- pin helpers ---------------------------------------------------------
    def _pin_pos(self, ref: str, pin: str) -> Tuple[float, float]:
        comp = self.sch.parts[ref]
        spec = comp.device_set.symbol_spec
        px, py = spec.pin_positions[pin]
        rx, ry = _rot((px, py), comp.rotation)
        return comp.x + rx, comp.y + ry

    def _pin_orient(self, ref: str, pin: str) -> int:
        comp = self.sch.parts[ref]
        spec = comp.device_set.symbol_spec
        return (spec.pin_orientations[pin] + comp.rotation) % 360

    # -- routing core --------------------------------------------------------
    @staticmethod
    def _label_geo(dx: float, dy: float) -> Tuple[int, str]:
        """Label rotation & justification for a given outward direction."""
        if dx > 0:
            return 180, "right bottom"
        if dx < 0:
            return 0, "left bottom"
        if dy < 0:
            return 90, "left bottom"
        return 270, "right bottom"

    def _route(self):
        """Route nets using straight wires for aligned stubs, labels for the rest.

        *Aligned* means every pin's stub endpoint shares the same grid
        column (or row).  A straight wire through that column connects them
        directly.  When two aligned nets would occupy overlapping segments
        of the same column the second one falls back to labels.

        Non-aligned nets (stubs at different X *and* Y) always get labels
        so no L-shaped wire ever crosses a component body.
        """
        STUB = 2.54
        # Map of pins that already have a label_pin entry
        labeled_pins: Dict[Tuple[str, str], str] = {
            (pl.reference, pl.pin_name): pl.net_name
            for pl in self.sch.pin_labels
        }

        # Phase 1: compute stubs and classify each net ----------------------
        net_data: List[Tuple[str, List[Tuple[str, str, float, float,
                                             float, float, float, float]],
                             Optional[str], str]] = []
        for net_name, members in self.nets:
            ml = sorted(members)
            if len(ml) < 2:
                continue
            pins: List[Tuple[str, str, float, float,
                             float, float, float, float]] = []
            for ref, pin in ml:
                px, py = self._pin_pos(ref, pin)
                orient = self._pin_orient(ref, pin)
                dx, dy = _outward(orient)
                sx = _snap(px + dx * STUB)
                sy = _snap(py + dy * STUB)
                pins.append((ref, pin, px, py, sx, sy, dx, dy))

            stub_xs = set(round(p[4], 2) for p in pins)
            stub_ys = set(round(p[5], 2) for p in pins)
            if len(stub_xs) == 1:
                axis: Optional[str] = "x"
            elif len(stub_ys) == 1:
                axis = "y"
            else:
                axis = None

            # Pick a label name: reuse an existing label_pin name when one
            # of this net's pins already carries one; otherwise auto-name.
            lbl_name = net_name
            for ref, pin, *_ in pins:
                existing = labeled_pins.get((ref, pin))
                if existing:
                    lbl_name = existing
                    break

            net_data.append((net_name, pins, axis, lbl_name))

        # Phase 2: route aligned nets (more pins first, shorter span) ------
        aligned = [d for d in net_data if d[2] is not None]
        others  = [d for d in net_data if d[2] is None]

        def _perp(pins, axis):
            return ([p[5] for p in pins] if axis == "x"
                    else [p[4] for p in pins])

        aligned.sort(key=lambda d: (-len(d[1]),
                                    max(_perp(d[1], d[2])) - min(_perp(d[1], d[2]))))

        # Track occupied channel segments: (axis, axis_val) → [(min, max)]
        occupied: Dict[Tuple[str, float], List[Tuple[float, float]]] = defaultdict(list)
        # Track backbone wire segments for cross-net collision detection:
        # list of (x1, y1, x2, y2) for backbone wires only (not stubs)
        backbone_segs: List[Tuple[float, float, float, float]] = []

        for net_name, pins, axis, lbl_name in aligned:
            val_idx = 4 if axis == "x" else 5      # sx or sy (shared)
            perp_idx = 5 if axis == "x" else 4      # sy or sx (varies)
            axis_val = round(pins[0][val_idx], 2)
            perps = [p[perp_idx] for p in pins]
            seg_min, seg_max = min(perps), max(perps)

            key = (axis, axis_val)
            conflict = any(seg_min < exmax and seg_max > exmin
                           for exmin, exmax in occupied[key])
            if not conflict:
                # Emit stub wires (pin → stub endpoint)
                for _ref, _pin, px, py, sx, sy, _dx, _dy in pins:
                    if (px, py) != (sx, sy):
                        self.wires.append((px, py, sx, sy))
                # Emit backbone as separate segments between consecutive
                # stub endpoints.  KiCad junctions on a monolithic wire's
                # interior break endpoint connectivity at the far end;
                # pre-splitting avoids this.
                sorted_pins = sorted(pins, key=lambda p: p[perp_idx])
                first_shared = sorted_pins[0][val_idx]
                for i in range(len(sorted_pins) - 1):
                    p_a = sorted_pins[i][perp_idx]
                    p_b = sorted_pins[i + 1][perp_idx]
                    if abs(p_a - p_b) < 0.001:
                        continue
                    if axis == "x":
                        seg = (first_shared, p_a,
                               first_shared, p_b)
                    else:
                        seg = (p_a, first_shared,
                               p_b, first_shared)
                    self.wires.append(seg)
                    backbone_segs.append(seg)
                # Junctions at interior stub endpoints (visual only now,
                # connectivity is already established by shared endpoints)
                for p in sorted_pins[1:-1]:
                    self.junctions.add((p[4], p[5]))

                occupied[key].append((seg_min, seg_max))
            else:
                self._emit_net_labels(lbl_name, pins, labeled_pins,
                                      backbone_segs)

        # Phase 3: non-aligned nets → labels --------------------------------
        for net_name, pins, _axis, lbl_name in others:
            self._emit_net_labels(lbl_name, pins, labeled_pins,
                                  backbone_segs)

    def _emit_net_labels(self, lbl_name: str,
                         pins: List[Tuple[str, str, float, float,
                                          float, float, float, float]],
                         labeled_pins: Dict[Tuple[str, str], str],
                         backbone_segs: List[Tuple[float, float, float, float]]):
        """Emit a stub wire + net label for each pin in the net.

        If a pin already has a ``label_pin`` entry with a matching name,
        the label is skipped (the pin_label system will place it).

        If a stub endpoint would land on the interior of an existing
        backbone wire, the stub is omitted and the label is placed
        directly at the pin position to avoid creating a cross-net
        T-junction.
        """
        for ref, pin, px, py, sx, sy, dx, dy in pins:
            # Check if stub endpoint would T-junction on a backbone
            skip_stub = False
            if (px, py) != (sx, sy):
                if self._point_on_backbone_interior(sx, sy, backbone_segs):
                    skip_stub = True

            if not skip_stub and (px, py) != (sx, sy):
                self.wires.append((px, py, sx, sy))

            existing = labeled_pins.get((ref, pin))
            if existing == lbl_name:
                continue  # label_pin will handle this pin
            rot, jst = self._label_geo(dx, dy)
            if skip_stub:
                # Place label at pin position instead of stub endpoint
                self.labels.append((lbl_name, px, py, rot, jst))
            else:
                self.labels.append((lbl_name, sx, sy, rot, jst))

    @staticmethod
    def _point_on_backbone_interior(x: float, y: float,
                                     backbone_segs: List[Tuple[float, float, float, float]],
                                     eps: float = 0.01) -> bool:
        """Return True if (x, y) is strictly inside a backbone wire segment."""
        for x1, y1, x2, y2 in backbone_segs:
            if abs(x1 - x2) < eps:
                # Vertical backbone at X = x1
                if abs(x - x1) < eps:
                    lo, hi = min(y1, y2), max(y1, y2)
                    if lo + eps < y < hi - eps:
                        return True
            elif abs(y1 - y2) < eps:
                # Horizontal backbone at Y = y1
                if abs(y - y1) < eps:
                    lo, hi = min(x1, x2), max(x1, x2)
                    if lo + eps < x < hi - eps:
                        return True
        return False


# ---------------------------------------------------------------------------
#  Layout engine
# ---------------------------------------------------------------------------
def _snap(v: float, grid: float = 1.27) -> float:
    return round(v / grid) * grid


def _layout(instances: List[Instance]):
    margin = _snap(30.48)    # 24 × 1.27
    gap = _snap(25.4)        # 20 × 1.27
    columns = 8
    y_cursor = margin

    for prefix in ("R", "Q"):
        group = sorted(
            [i for i in instances if i.prefix == prefix],
            key=lambda i: int(i.component.reference[1:]),
        )
        if not group:
            continue
        cell_w = _snap(20.32) if prefix == "R" else _snap(25.4)   # 16 / 20 grid units
        cell_h = _snap(20.32) if prefix == "R" else _snap(25.4)
        for idx, inst in enumerate(group):
            col = idx % columns
            row = idx // columns
            inst.component.x = _snap(margin + col * cell_w)
            inst.component.y = _snap(y_cursor + row * cell_h)
        rows = (len(group) - 1) // columns + 1
        y_cursor = _snap(y_cursor + rows * cell_h + gap)


# ---------------------------------------------------------------------------
#  Extract one top-level symbol block from a .kicad_sym file
# ---------------------------------------------------------------------------
def _extract_sym_block(text: str, token: str) -> str:
    start = text.find(token)
    if start == -1:
        raise ValueError(token)
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        c = text[i]
        if in_string:
            if c == "\\" and i + 1 < len(text):
                i += 2
                continue
            if c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return text[start: i + 1]
        i += 1
    raise ValueError(f"Unterminated: {token}")


# ---------------------------------------------------------------------------
#  Embedded power symbol lib_symbols (KiCad 8 format)
# ---------------------------------------------------------------------------
_POWER_LIB_SYMS: Dict[str, List[str]] = {
    "+5V": [
        '\t\t(symbol "power:+5V"',
        "\t\t\t(power)",
        "\t\t\t(pin_numbers hide)",
        "\t\t\t(pin_names (offset 0) hide)",
        "\t\t\t(exclude_from_sim no)",
        "\t\t\t(in_bom no)",
        "\t\t\t(on_board yes)",
        '\t\t\t(property "Reference" "#PWR" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Value" "+5V" (at 0 -1.016 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27))))",
        '\t\t\t(property "Footprint" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Datasheet" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Description" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(symbol "+5V_0_1"',
        "\t\t\t\t(polyline",
        "\t\t\t\t\t(pts (xy -0.762 1.27) (xy 0 2.54))",
        "\t\t\t\t\t(stroke (width 0) (type default))",
        "\t\t\t\t\t(fill (type none)))",
        "\t\t\t\t(polyline",
        "\t\t\t\t\t(pts (xy 0 0) (xy 0 2.54))",
        "\t\t\t\t\t(stroke (width 0) (type default))",
        "\t\t\t\t\t(fill (type none)))",
        "\t\t\t\t(polyline",
        "\t\t\t\t\t(pts (xy 0 2.54) (xy 0.762 1.27))",
        "\t\t\t\t\t(stroke (width 0) (type default))",
        "\t\t\t\t\t(fill (type none)))",
        "\t\t\t)",
        '\t\t\t(symbol "+5V_1_1"',
        "\t\t\t\t(pin power_out line (at 0 0 90) (length 0)",
        '\t\t\t\t\t(name "+5V" (effects (font (size 1.27 1.27))))',
        '\t\t\t\t\t(number "1" (effects (font (size 1.27 1.27)))))',
        "\t\t\t)",
        "\t\t\t(embedded_fonts no)",
        "\t\t)",
    ],
    "GND": [
        '\t\t(symbol "power:GND"',
        "\t\t\t(power)",
        "\t\t\t(pin_numbers hide)",
        "\t\t\t(pin_names (offset 0) hide)",
        "\t\t\t(exclude_from_sim no)",
        "\t\t\t(in_bom no)",
        "\t\t\t(on_board yes)",
        '\t\t\t(property "Reference" "#PWR" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Value" "GND" (at 0 -1.27 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27))))",
        '\t\t\t(property "Footprint" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Datasheet" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(property "Description" "" (at 0 0 0)',
        "\t\t\t\t(effects (font (size 1.27 1.27)) (hide yes)))",
        '\t\t\t(symbol "GND_0_1"',
        "\t\t\t\t(polyline",
        "\t\t\t\t\t(pts (xy 0 0) (xy 0 -1.27) (xy 1.27 -1.27) (xy 0 -2.54) (xy -1.27 -1.27) (xy 0 -1.27))",
        "\t\t\t\t\t(stroke (width 0) (type default))",
        "\t\t\t\t\t(fill (type none)))",
        "\t\t\t)",
        '\t\t\t(symbol "GND_1_1"',
        "\t\t\t\t(pin power_out line (at 0 0 270) (length 0)",
        '\t\t\t\t\t(name "GND" (effects (font (size 1.27 1.27))))',
        '\t\t\t\t\t(number "1" (effects (font (size 1.27 1.27)))))',
        "\t\t\t)",
        "\t\t\t(embedded_fonts no)",
        "\t\t)",
    ],
}


# ---------------------------------------------------------------------------
#  KiCad writer
# ---------------------------------------------------------------------------
class _KiCadWriter:
    def __init__(self, sch: "Schematic"):
        self.sch = sch
        self._sym_cache: Dict[str, str] = {}
        self._power_positions: Dict[str, Tuple[float, float]] = {}

    @staticmethod
    def _grid_point(x: float, y: float) -> Tuple[int, int]:
        return int(round(x / _Router.GRID_MM)), int(round(y / _Router.GRID_MM))

    @staticmethod
    def _edge_key(a: Tuple[int, int], b: Tuple[int, int]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        return (a, b) if a <= b else (b, a)

    @staticmethod
    def _iter_unit_nodes(a: Tuple[float, float], b: Tuple[float, float]) -> Iterable[Tuple[int, int]]:
        ga = _KiCadWriter._grid_point(*a)
        gb = _KiCadWriter._grid_point(*b)
        if ga == gb:
            return [ga]
        if ga[0] == gb[0]:
            step = 1 if gb[1] > ga[1] else -1
            return [(ga[0], y) for y in range(ga[1], gb[1] + step, step)]
        step = 1 if gb[0] > ga[0] else -1
        return [(x, ga[1]) for x in range(ga[0], gb[0] + step, step)]

    @staticmethod
    def _iter_unit_edges(a: Tuple[float, float], b: Tuple[float, float]) -> Iterable[Tuple[Tuple[int, int], Tuple[int, int]]]:
        nodes = list(_KiCadWriter._iter_unit_nodes(a, b))
        return [_KiCadWriter._edge_key(left, right) for left, right in zip(nodes, nodes[1:])]

    @staticmethod
    def _reserve_path(points: List[Tuple[float, float]],
                      occupied_edges: Set[Tuple[Tuple[int, int], Tuple[int, int]]],
                      occupied_nodes: Set[Tuple[int, int]]):
        for a, b in zip(points, points[1:]):
            occupied_edges.update(_KiCadWriter._iter_unit_edges(a, b))
            occupied_nodes.update(_KiCadWriter._iter_unit_nodes(a, b))

    @staticmethod
    def _path_is_clear(points: List[Tuple[float, float]],
                       occupied_edges: Set[Tuple[Tuple[int, int], Tuple[int, int]]],
                       occupied_nodes: Set[Tuple[int, int]]) -> bool:
        for segment_idx, (a, b) in enumerate(zip(points, points[1:])):
            for edge in _KiCadWriter._iter_unit_edges(a, b):
                if edge in occupied_edges:
                    return False
            nodes = list(_KiCadWriter._iter_unit_nodes(a, b))
            node_slice = nodes[1:] if segment_idx == 0 else nodes
            for node in node_slice:
                if node in occupied_nodes:
                    return False
        return True

    @staticmethod
    def _label_style(dx: float, dy: float) -> Tuple[int, str]:
        if dx > 0:
            return 180, "right bottom"
        if dx < 0:
            return 0, "left bottom"
        if dy < 0:
            return 90, "left bottom"
        return 270, "right bottom"

    @staticmethod
    def _dedupe_points(points: Iterable[Tuple[float, float]]) -> List[Tuple[float, float]]:
        deduped: List[Tuple[float, float]] = []
        for x, y in points:
            point = (_snap(x), _snap(y))
            if deduped and deduped[-1] == point:
                continue
            deduped.append(point)
        return deduped

    def _pin_label_paths(self, x: float, y: float, orientation: int,
                         length: float, net_name: str = "") -> List[Tuple[List[Tuple[float, float]], int, str]]:
        dx, dy = _outward(orientation)
        rot, jst = self._label_style(dx, dy)
        end_x = x + dx * length
        end_y = y + dy * length
        candidates: List[List[Tuple[float, float]]] = []

        escape = 2.54
        default_detours = (5.08, -5.08, 10.16, -10.16)
        if net_name == "+5V" and orientation in (90, 270):
            detours = (-5.08, -10.16, 5.08, 10.16)
        elif net_name == "GND" and orientation in (90, 270):
            detours = (5.08, 10.16, -5.08, -10.16)
        else:
            candidates.append([(x, y), (end_x, end_y)])
            detours = default_detours

        for detour in detours:
            if dx:
                off_x, off_y = 0.0, detour
            else:
                off_x, off_y = detour, 0.0
            candidates.append([
                (x, y),
                (x + off_x, y + off_y),
                (end_x + off_x, end_y + off_y),
            ])
            candidates.append([
                (x, y),
                (x + dx * escape, y + dy * escape),
                (x + dx * escape + off_x, y + dy * escape + off_y),
                (end_x + off_x, end_y + off_y),
            ])

        if not candidates or candidates[0] != [(x, y), (end_x, end_y)]:
            candidates.append([(x, y), (end_x, end_y)])

        return [
            (self._dedupe_points(points), rot, jst)
            for points in candidates
        ]

    def _auto_paper(self, router: _Router) -> str:
        """Pick the smallest standard paper that snugly contains all content."""
        xs: List[float] = []
        ys: List[float] = []
        for comp in self.sch.parts.values():
            spec = comp.device_set.symbol_spec
            for pin_name in spec.pin_positions:
                px, py = spec.pin_positions[pin_name]
                rx, ry = _rot((px, py), comp.rotation)
                xs.append(comp.x + rx)
                ys.append(comp.y + ry)
        for poly in self.sch.graphic_polylines:
            for x, y in poly.points:
                xs.append(x)
                ys.append(y)
        for text in self.sch.graphic_texts:
            xs.append(text.x)
            ys.append(text.y)
        for x1, y1, x2, y2 in router.wires:
            xs.extend([x1, x2])
            ys.extend([y1, y2])
        for pl in self.sch.pin_labels:
            px, py = self.sch.pin_position(pl.reference, pl.pin_name)
            orient = self.sch.pin_orientation(pl.reference, pl.pin_name)
            dx, dy = _outward(orient)
            xs.extend([px, px + dx * pl.length])
            ys.extend([py, py + dy * pl.length])
        if not xs:
            return "A4"
        margin = 15.0
        need_w = max(xs) - min(min(xs), 0) + margin * 2
        need_h = max(ys) - min(min(ys), 0) + margin * 2
        for name, w, h in [("A4", 297, 210), ("A3", 420, 297),
                           ("A2", 594, 420), ("A1", 841, 594)]:
            if need_w <= w and need_h <= h:
                return name
        return "A1"

    def write(self, path: Path):
        proj = path.stem
        root = _uid(proj, "root")
        router = _Router(self.sch)
        router.run()
        self.sch.paper = self._auto_paper(router)
        L = self._header(proj, root)
        L += self._lib_symbols()
        L += self._graphics(proj)
        L += self._wires_and_labels(proj, router)
        L += self._symbols(proj, root)
        L += self._footer()
        path.write_text("\n".join(L) + "\n", encoding="utf-8")

    def _header(self, proj: str, root: str) -> List[str]:
        return [
            "(kicad_sch",
            f"\t(version {KICAD_SCHEMATIC_VERSION})",
            '\t(generator "Taurus")',
            '\t(generator_version "1.0")',
            f'\t(uuid "{root}")',
            f'\t(paper "{self.sch.paper}")',
            "\t(lib_symbols",
        ]

    def _lib_symbols(self) -> List[str]:
        lines: List[str] = []
        seen: Set[str] = set()
        shared = self.sch.kicad_shared
        for inst in self.sch.instances:
            spec = inst.component.device_set.symbol_spec
            if spec.library_id in seen:
                continue
            seen.add(spec.library_id)
            block = self._sym_cache.get(spec.library_id)
            if block is None:
                p = shared / "symbols" / spec.library_file
                txt = p.read_text(encoding="utf-8")
                block = _extract_sym_block(txt, f'(symbol "{spec.symbol_name}"')
                # Only the top-level symbol name gets the library prefix;
                # subsymbol names (e.g. 74LVC1G08_0_1) keep their
                # original un-prefixed names.
                block = block.replace(
                    f'(symbol "{spec.symbol_name}"',
                    f'(symbol "{spec.library_id}"',
                    1,  # first occurrence only — the parent symbol
                )
                self._sym_cache[spec.library_id] = block
            for ln in block.splitlines():
                lines.append(f"\t\t{ln.lstrip()}" if ln.strip() else "")
        # Add power symbol lib definitions for any +5V/GND nets
        power_nets = {pl.net_name for pl in self.sch.pin_labels
                      if pl.net_name in _POWER_LIB_SYMS}
        for net in sorted(power_nets):
            lines += _POWER_LIB_SYMS[net]
        lines.append("\t)")
        return lines

    def _wires_and_labels(self, proj: str, router: _Router) -> List[str]:
        lines: List[str] = []

        def _emit_label(name: str, x: float, y: float, rot: int, jst: str,
                        uid: str, label_type: str = "label") -> List[str]:
            if label_type in ("global_input", "global_output"):
                shape = "input" if label_type == "global_input" else "output"
                return [
                    f'\t(global_label "{name}"',
                    f"\t\t(shape {shape})",
                    f"\t\t(at {_fc(x)} {_fc(y)} {rot})",
                    "\t\t(fields_autoplaced yes)",
                    "\t\t(effects (font (size 1.27 1.27))",
                    f"\t\t\t(justify {jst}))",
                    f'\t\t(uuid "{uid}")',
                    "\t)",
                ]
            return [
                f'\t(label "{name}"',
                f"\t\t(at {_fc(x)} {_fc(y)} {rot})",
                "\t\t(effects (font (size 1.27 1.27))",
                f"\t\t\t(justify {jst}))",
                f'\t\t(uuid "{uid}")',
                "\t)",
            ]

        occupied_edges: Set[Tuple[Tuple[int, int], Tuple[int, int]]] = set()
        occupied_nodes: Set[Tuple[int, int]] = set()
        for idx, (x1, y1, x2, y2) in enumerate(router.wires):
            wu = _uid(proj, "wire", idx)
            lines += [
                "\t(wire",
                "\t\t(pts",
                f"\t\t\t(xy {_fc(x1)} {_fc(y1)}) (xy {_fc(x2)} {_fc(y2)})",
                "\t\t)",
                "\t\t(stroke (width 0) (type solid))",
                f'\t\t(uuid "{wu}")',
                "\t)",
            ]
            self._reserve_path([(x1, y1), (x2, y2)], occupied_edges, occupied_nodes)
        for idx, (name, lx, ly, rot, jst) in enumerate(router.labels):
            lu = _uid(proj, "label", idx)
            lines += _emit_label(name, lx, ly, rot, jst, lu)
        for idx, (jx, jy) in enumerate(sorted(router.junctions)):
            ju = _uid(proj, "junction", idx)
            lines += [
                "\t(junction",
                f"\t\t(at {_fc(jx)} {_fc(jy)})",
                "\t\t(diameter 0)",
                "\t\t(color 0 0 0 0)",
                f'\t\t(uuid "{ju}")',
                "\t)",
            ]
        for idx, pin_label in enumerate(self.sch.pin_labels):
            px, py = self.sch.pin_position(pin_label.reference, pin_label.pin_name)
            orient = self.sch.pin_orientation(pin_label.reference, pin_label.pin_name)
            dx, dy = _outward(orient)

            # Always start the label wire from the pin position.
            # (Previously tried to skip the first stub when the grid edge
            # was already occupied, but that false-triggered when an
            # *unrelated* net's wire coincidentally shared the edge.)
            lbl_x, lbl_y = px, py
            lbl_length = pin_label.length

            path, rot, jst = self._pin_label_paths(lbl_x, lbl_y, orient, lbl_length, pin_label.net_name)[0]
            for candidate, cand_rot, cand_jst in self._pin_label_paths(lbl_x, lbl_y, orient, lbl_length, pin_label.net_name):
                if self._path_is_clear(candidate, occupied_edges, occupied_nodes):
                    path, rot, jst = candidate, cand_rot, cand_jst
                    break
            lx, ly = path[-1]
            lu = _uid(proj, pin_label.reference, pin_label.pin_name, "label")
            for seg_idx, (start, end) in enumerate(zip(path, path[1:])):
                wu = _uid(proj, pin_label.reference, pin_label.pin_name, "label-wire", seg_idx)
                lines += [
                    "\t(wire",
                    "\t\t(pts",
                    f"\t\t\t(xy {_fc(start[0])} {_fc(start[1])}) (xy {_fc(end[0])} {_fc(end[1])})",
                    "\t\t)",
                    "\t\t(stroke (width 0) (type solid))",
                    f'\t\t(uuid "{wu}")',
                    "\t)",
                ]
            self._reserve_path(path, occupied_edges, occupied_nodes)
            lines += _emit_label(pin_label.net_name, lx, ly, rot, jst, lu, pin_label.label_type)
        return lines

    def _graphics(self, proj: str) -> List[str]:
        lines: List[str] = []
        for idx, polyline in enumerate(self.sch.graphic_polylines):
            pu = _uid(proj, "polyline", idx)
            pts = " ".join(f"(xy {_fc(x)} {_fc(y)})" for x, y in polyline.points)
            lines += [
                "\t(polyline",
                "\t\t(pts",
                f"\t\t\t{pts}",
                "\t\t)",
                f"\t\t(stroke (width {_fc(polyline.stroke_width)}) (type {polyline.stroke_type}))",
                f'\t\t(uuid "{pu}")',
                "\t)",
            ]

        for idx, text in enumerate(self.sch.graphic_texts):
            tu = _uid(proj, "text", idx)
            lines += [
                f'\t(text "{text.text}"',
                "\t\t(exclude_from_sim no)",
                f"\t\t(at {_fc(text.x)} {_fc(text.y)} {text.rotation})",
                "\t\t(effects",
                "\t\t\t(font",
                f"\t\t\t\t(size {_fc(text.size)} {_fc(text.size)})",
                "\t\t\t)",
                f"\t\t\t(justify {text.justify})",
                "\t\t)",
                f'\t\t(uuid "{tu}")',
                "\t)",
            ]
        return lines

    def _symbols(self, proj: str, root: str) -> List[str]:
        lines: List[str] = []
        for inst in self.sch.instances:
            lines += self._one_sym(inst.component, proj, root)
        lines += self._power_symbols(proj, root)
        return lines

    def _power_symbols(self, proj: str, root: str) -> List[str]:
        lines: List[str] = []
        power_nets = sorted({pl.net_name for pl in self.sch.pin_labels
                             if pl.net_name in _POWER_LIB_SYMS})
        for idx, net in enumerate(power_nets):
            lib_id = f"power:{net}"
            ref = f"#PWR{idx + 1}"
            x = _snap(7.62 + idx * 12.70)
            y = _snap(7.62)
            su = _uid(proj, ref, "sym")
            pu = _uid(proj, ref, "pin", "1")
            lines += [
                "\t(symbol",
                f'\t\t(lib_id "{lib_id}")',
                f"\t\t(at {_fc(x)} {_fc(y)} 0)",
                "\t\t(unit 1)",
                "\t\t(exclude_from_sim no)",
                "\t\t(in_bom no)",
                "\t\t(on_board yes)",
                "\t\t(dnp no)",
                f'\t\t(uuid "{su}")',
            ]
            lines += self._prop("Reference", ref, x, y, 0, True)
            lines += self._prop("Value", net, x, y - 1.27, 0)
            lines += self._prop("Footprint", "", x, y, 0, True)
            lines += self._prop("Datasheet", "", x, y, 0, True)
            lines += self._prop("Description", "", x, y, 0, True)
            lines += [f'\t\t(pin "1"', f'\t\t\t(uuid "{pu}")', "\t\t)"]
            lines += [
                "\t\t(instances",
                f'\t\t\t(project "{proj}"',
                f'\t\t\t\t(path "/{root}"',
                f'\t\t\t\t\t(reference "{ref}")',
                "\t\t\t\t\t(unit 1)",
                "\t\t\t\t)",
                "\t\t\t)",
                "\t\t)",
                "\t)",
            ]
        return lines

    def _one_sym(self, c: PlacedPart, proj: str, root: str) -> List[str]:
        spec = c.device_set.symbol_spec
        su = _uid(proj, c.reference, "sym")
        rdx, rdy = _rot(spec.reference_offset[:2], c.rotation)
        vdx, vdy = _rot(spec.value_offset[:2], c.rotation)
        rr = (spec.reference_offset[2] + c.rotation) % 360
        vr = (spec.value_offset[2] + c.rotation) % 360
        L = [
            "\t(symbol",
            f'\t\t(lib_id "{spec.library_id}")',
            f"\t\t(at {_fc(c.x)} {_fc(c.y)} {c.rotation})",
            "\t\t(unit 1)",
            "\t\t(exclude_from_sim no)",
            "\t\t(in_bom yes)",
            "\t\t(on_board yes)",
            "\t\t(dnp no)",
            f'\t\t(uuid "{su}")',
        ]
        L += self._prop("Reference", c.reference, c.x + rdx, c.y + rdy, rr)
        L += self._prop("Value", c.device.name, c.x + vdx, c.y + vdy, vr)
        L += self._prop("Footprint", c.device.package or "", c.x, c.y, c.rotation, True)
        L += self._prop("Datasheet", "~", c.x, c.y, c.rotation, True)
        L += self._prop("Description", spec.description, c.x, c.y, c.rotation, True)
        for pn in spec.pin_order:
            pu = _uid(proj, c.reference, "pin", pn)
            L += [f'\t\t(pin "{pn}"', f'\t\t\t(uuid "{pu}")', "\t\t)"]
        L += [
            "\t\t(instances",
            f'\t\t\t(project "{proj}"',
            f'\t\t\t\t(path "/{root}"',
            f'\t\t\t\t\t(reference "{c.reference}")',
            "\t\t\t\t\t(unit 1)", "\t\t\t\t)", "\t\t\t)", "\t\t)",
            "\t)",
        ]
        return L

    @staticmethod
    def _prop(name, val, x, y, rot, hidden=False) -> List[str]:
        L = [
            f'\t\t(property "{name}" "{val}"',
            f"\t\t\t(at {_fc(x)} {_fc(y)} {rot})",
            "\t\t\t(effects (font (size 1.27 1.27))",
        ]
        if hidden:
            L[-1] += " (hide yes)"
        L += ["\t\t\t)", "\t\t)"]
        return L

    def _footer(self) -> List[str]:
        return [
            "\t(sheet_instances",
            '\t\t(path "/"', '\t\t\t(page "1")', "\t\t)", "\t)",
            "\t(embedded_fonts no)",
            ")",
        ]


# ---------------------------------------------------------------------------
#  KiCad reader – roundtrip
# ---------------------------------------------------------------------------
class _KiCadReader:
    def __init__(self, path: Path, kicad_shared: Path):
        self.path = path
        self.kicad_shared = kicad_shared

    def read(self) -> "Schematic":
        tree = S.parse(self.path.read_text(encoding="utf-8"))
        root = tree[0]
        sch = Schematic(kicad_shared_path=self.kicad_shared)
        for sym_node in S.find_all(root, "symbol"):
            lib_id = S.get_value(sym_node, "lib_id")
            if lib_id is None:
                continue
            ref = S.get_property(sym_node, "Reference") or "?"
            value = S.get_property(sym_node, "Value") or ""
            prefix = ""
            for ch in ref:
                if ch.isalpha():
                    prefix += ch
                else:
                    break
            if not prefix:
                continue
            ds_name = f"{prefix}_{lib_id.replace(':', '_')}_imported"
            if ds_name not in sch.device_sets:
                if prefix in SYMBOL_SPECS and lib_id == SYMBOL_SPECS[prefix].library_id:
                    alias = REQUIRED_LIBRARY_ALIAS.get(prefix)
                    if alias and alias not in sch.libraries:
                        sch.init_libraries(alias)
                    ds = sch.init_device_set(ds_name, prefix)
                else:
                    ds = sch.init_device_set(ds_name, prefix, library_id=lib_id)
                sch.init_device(ds, value or prefix)
            dev_name = value or prefix
            if dev_name not in sch.device_sets[ds_name].devices:
                sch.init_device(sch.device_sets[ds_name], dev_name)
            inst = sch.add_instance(ds_name, dev_name, prefix)
            at_node = S.find(sym_node, "at")
            if at_node and len(at_node) >= 3:
                inst.component.x = float(at_node[1])
                inst.component.y = float(at_node[2])
                if len(at_node) >= 4:
                    inst.component.rotation = int(float(at_node[3]))
            inst.component.reference = ref
            sch.parts[ref] = inst.component
        return sch


# ---------------------------------------------------------------------------
#  Eagle writer – fallback backend via eaglepy
# ---------------------------------------------------------------------------
class _EagleWriter:
    def __init__(self, sch: "Schematic"):
        self.sch = sch

    def write(self, path: Path):
        from .eaglepy.eagle import (
            Eagle, Drawing, Grid, Schematic as ES, Sheet, Net,
            Part as EP, Library, Device_Set, Device as ED,
            Symbol as ESym, Segment, Instance as EI, Gate,
            attributes,
        )
        from .eaglepy import default_layers
        from .eaglepy.primitives import Wire, Pin, Pin_Ref as PinRef

        grid = Grid(distance=0.1, unit_dist="inch", unit="inch",
                     style="lines", multiple=1, display=False)
        layers = default_layers.get_layers()
        sheet = Sheet()
        es = ES(sheets=[sheet])
        drawing = Drawing(grid=grid, layers=layers, document=es)

        elibs: Dict[str, Library] = {}
        eds: Dict[str, Device_Set] = {}

        for inst in self.sch.instances:
            spec = inst.component.device_set.symbol_spec
            pf = inst.prefix
            if pf in elibs:
                continue
            ln = {"Q": "transistor-npn", "R": "resistor-power"}.get(pf, pf)
            lib = Library(name=ln)
            pins = []
            for pn in spec.pin_order:
                px, py = spec.pin_positions[pn]
                pins.append(Pin(name=pn, x=px, y=-py, visible="off",
                                length="short", direction="pas",
                                rotation=attributes.Rotation(0)))
            esym = ESym(name=pf, items=pins)
            lib.symbols.append(esym)
            ds = Device_Set(name=f"{pf}_", prefix=pf,
                            gates=[Gate(name="G$1", symbol=esym, x=0, y=0)])
            lib.device_sets.append(ds)
            elibs[pf] = lib
            eds[pf] = ds
            es.libraries.append(lib)

        for inst in self.sch.instances:
            ds = eds[inst.prefix]
            dn = inst.component.device.name
            if not any(d.name == dn for d in ds.devices):
                ds.devices.append(ED(name=dn, package=None))

        for inst in self.sch.instances:
            ds = eds[inst.prefix]
            lib = elibs[inst.prefix]
            dev = next(d for d in ds.devices
                       if d.name == inst.component.device.name)
            part = EP(name=inst.component.reference, library=lib,
                      device_set=ds, device=dev)
            es.parts.append(part)
            ei = EI(part=part, x=inst.component.x, y=-inst.component.y,
                     gate=ds.gates[0],
                     rotation=attributes.Rotation(inst.component.rotation))
            sheet.instances.append(ei)

        router = _Router(self.sch)
        router.run()
        for net_name, members in router.nets:
            pin_refs = set()
            wires = []
            for ref, pin in members:
                pin_refs.add(PinRef(part=ref, gate="G$1", pin=pin))
            for ref, pin in members:
                comp = self.sch.parts[ref]
                spec = comp.device_set.symbol_spec
                px, py = spec.pin_positions[pin]
                rx, ry = _rot((px, py), comp.rotation)
                ax, ay = comp.x + rx, comp.y + ry
                o = (spec.pin_orientations[pin] + comp.rotation) % 360
                dx, dy = _outward(o)
                bx, by = ax + dx * 2.54, ay + dy * 2.54
                wires.append(Wire(x1=ax, y1=-ay, x2=bx, y2=-by, width=0.2))
            seg = Segment(items=list(pin_refs) + wires)
            net = Net(name=net_name, net_class=0)
            net.segments.append(seg)
            es.sheets[0].nets.append(net)

        for pin_label in self.sch.pin_labels:
            comp = self.sch.parts[pin_label.reference]
            spec = comp.device_set.symbol_spec
            px, py = spec.pin_positions[pin_label.pin_name]
            rx, ry = _rot((px, py), comp.rotation)
            ax, ay = comp.x + rx, comp.y + ry
            orient = (spec.pin_orientations[pin_label.pin_name] + comp.rotation) % 360
            dx, dy = _outward(orient)
            bx, by = ax + dx * pin_label.length, ay + dy * pin_label.length
            seg = Segment(items=[
                PinRef(part=pin_label.reference, gate="G$1", pin=pin_label.pin_name),
                Wire(x1=ax, y1=-ay, x2=bx, y2=-by, width=0.2),
            ])
            net = Net(name=pin_label.net_name, net_class=0)
            net.segments.append(seg)
            es.sheets[0].nets.append(net)

        Eagle(drawing=drawing).save(path)


# ---------------------------------------------------------------------------
#  Schematic (main public class)
# ---------------------------------------------------------------------------
class Schematic:
    def __init__(self, kicad_shared_path: Optional[Path] = None,
                 paper: str = "A4"):
        self.kicad_shared = Path(
            kicad_shared_path
            or os.environ.get("TAURUS_KICAD_SHARED_PATH", DEFAULT_KICAD_SHARED)
        )
        self.paper = paper
        self.libraries: Dict[str, dict] = {}
        self.device_sets: Dict[str, DeviceSet] = {}
        self.devices: Dict[tuple, Device] = {}
        self.parts: Dict[str, PlacedPart] = {}
        self.instances: List[Instance] = []
        self.pin_labels: List[PinLabel] = []
        self.graphic_texts: List[GraphicText] = []
        self.graphic_polylines: List[GraphicPolyline] = []
        self.part_counters: Dict[str, int] = defaultdict(int)

    def init_libraries(self, *names: str):
        for name in names:
            if name not in SUPPORTED_LIBRARY_ALIASES:
                raise ValueError(f"Unsupported library alias: {name}")
            self.libraries[name] = SUPPORTED_LIBRARY_ALIASES[name]

    def init_device_set(self, name: str, prefix: str,
                        library_id: Optional[str] = None,
                        library_file: Optional[str] = None,
                        symbol_name: Optional[str] = None) -> DeviceSet:
        if library_id is None:
            alias = REQUIRED_LIBRARY_ALIAS.get(prefix)
            if alias and alias not in self.libraries:
                raise ValueError(f"Library {alias} not initialized")
            if prefix not in SYMBOL_SPECS:
                raise ValueError(f"No built-in symbol spec for prefix {prefix}")
            spec = SYMBOL_SPECS[prefix]
        else:
            spec = _load_kicad_symbol_spec(
                self.kicad_shared,
                library_id=library_id,
                library_file=library_file,
                symbol_name=symbol_name,
            )
            self.libraries.setdefault(library_id.split(":", 1)[0], {"prefixes": {prefix}})
        ds = DeviceSet(name=name, prefix=prefix, symbol_spec=spec)
        self.device_sets[name] = ds
        return ds

    def init_device(self, ds: DeviceSet, name: str, package: str | None = None) -> Device:
        dev = Device(name=name, package=package)
        ds.devices[name] = dev
        self.devices[(ds.name, name)] = dev
        return dev

    def add_instance(self, device_set_name: str, part_name: str, prefix: str) -> Instance:
        ds = self.device_sets[device_set_name]
        if prefix != ds.prefix:
            raise ValueError(f"Prefix mismatch: {prefix} vs {ds.prefix}")
        if part_name not in ds.devices:
            raise ValueError(f"Device {part_name} not in {device_set_name}")
        self.part_counters[prefix] += 1
        ref = f"{prefix}{self.part_counters[prefix]}"
        comp = PlacedPart(reference=ref, device_set=ds,
                          device=ds.devices[part_name], prefix=prefix)
        self.parts[ref] = comp
        inst = Instance(comp, self, part_name, device_set_name, prefix)
        self.instances.append(inst)
        _layout(self.instances)
        return inst

    def place(self, instance: Instance, x: float, y: float, rotation: int = 0):
        instance.component.x = x
        instance.component.y = y
        instance.component.rotation = rotation % 360

    def pin_position(self, instance_or_ref: Instance | str, pin_name: str) -> Tuple[float, float]:
        ref = instance_or_ref.component.reference if isinstance(instance_or_ref, Instance) else instance_or_ref
        comp = self.parts[ref]
        spec = comp.device_set.symbol_spec
        px, py = spec.pin_positions[pin_name]
        rx, ry = _rot((px, py), comp.rotation)
        return comp.x + rx, comp.y + ry

    def pin_orientation(self, instance_or_ref: Instance | str, pin_name: str) -> int:
        ref = instance_or_ref.component.reference if isinstance(instance_or_ref, Instance) else instance_or_ref
        comp = self.parts[ref]
        spec = comp.device_set.symbol_spec
        return (spec.pin_orientations[pin_name] + comp.rotation) % 360

    def label_pin(self, instance_or_ref: Instance | str, pin_name: str,
                  net_name: str, length: float = 7.62, label_type: str = "label"):
        ref = instance_or_ref.component.reference if isinstance(instance_or_ref, Instance) else instance_or_ref
        self.pin_labels.append(PinLabel(ref, pin_name, net_name, length, label_type))

    def add_text(self, text: str, x: float, y: float, rotation: int = 0,
                 justify: str = "left bottom", size: float = 1.27):
        self.graphic_texts.append(GraphicText(text, _snap(x), _snap(y), rotation, justify, size))

    def add_box(self, left: float, top: float, right: float, bottom: float,
                stroke_width: float = 0.1524, stroke_type: str = "dash"):
        self.graphic_polylines.append(
            GraphicPolyline(
                [
                    (_snap(left), _snap(top)),
                    (_snap(right), _snap(top)),
                    (_snap(right), _snap(bottom)),
                    (_snap(left), _snap(bottom)),
                    (_snap(left), _snap(top)),
                ],
                stroke_width=stroke_width,
                stroke_type=stroke_type,
            )
        )

    def wire_up(self):
        _layout(self.instances)

    def save(self, filename: str, backend: str = "kicad"):
        path = Path(filename)
        if backend == "kicad":
            if path.suffix != ".kicad_sch":
                path = path.with_suffix(".kicad_sch")
            _KiCadWriter(self).write(path)
        elif backend == "eagle":
            if path.suffix != ".sch":
                path = path.with_suffix(".sch")
            _EagleWriter(self).write(path)
        else:
            raise ValueError(f"Unknown backend: {backend}")
        print(f"Schematic saved to {path} ({backend})")

    @classmethod
    def load(cls, filename: str, kicad_shared_path: Optional[Path] = None) -> "Schematic":
        path = Path(filename)
        shared = Path(kicad_shared_path or DEFAULT_KICAD_SHARED)
        if path.suffix == ".kicad_sch":
            return _KiCadReader(path, shared).read()
        raise ValueError(f"Unsupported format: {path.suffix}")


# ---------------------------------------------------------------------------
#  Symbol / Descriptor helpers
# ---------------------------------------------------------------------------
class Descriptor:
    def __init__(self, identifier, device_set, part, prefix):
        self.identifier = identifier
        self.device_set = device_set
        self.part = part
        self.prefix = prefix


class Symbol:
    def __init__(self, name, parts=None, descriptors=None, connections=None):
        self.name = name
        self.parts = parts or []
        self.descriptors = descriptors or []
        self.descriptor_counters: Dict[str, int] = {}
        self.connections = connections or {}

    def add_descriptor(self, device_set, part, prefix):
        self.descriptor_counters[prefix] = self.descriptor_counters.get(prefix, 0) + 1
        d = Descriptor(self.descriptor_counters[prefix], device_set, part, prefix)
        self.descriptors.append(d)
        return d

    def add_connection(self, pin_name, source_instance, target):
        self.connections[pin_name] = (source_instance, target)

    def to_xml(self):
        root = ET.Element("symbol", {"name": self.name})
        inst_el = ET.SubElement(root, "instances")
        for d in self.descriptors:
            ET.SubElement(inst_el, "instance", {
                "identifier": str(d.identifier), "device_set": d.device_set,
                "part": d.part, "prefix": d.prefix,
            })
        conn_el = ET.SubElement(root, "connections")
        for pn, (src, (tgt, tp)) in self.connections.items():
            sv = f"{src.identifier}:{src.device_set}:{src.part}:{src.prefix}:{pn}"
            tv = f"{tgt.identifier}:{tgt.device_set}:{tgt.part}:{tgt.prefix}:{tp}"
            ET.SubElement(conn_el, "connection",
                          {"source_instance": sv, "target_instance": tv})
        return ET.tostring(root, encoding="unicode")

    def save(self, filename):
        Path(filename).write_text(self.to_xml(), encoding="utf-8")
        print(f"Symbol saved to {filename}")

    @staticmethod
    def load(filename, schematic):
        return Symbol.from_xml(Path(filename).read_text("utf-8"), schematic)

    @staticmethod
    def from_xml(xml_string, schematic):
        root = ET.fromstring(xml_string)
        sym = Symbol(root.attrib["name"])
        for inst in root.find("instances").findall("instance"):
            sym.descriptors.append(Descriptor(
                int(inst.attrib["identifier"]), inst.attrib["device_set"],
                inst.attrib["part"], inst.attrib["prefix"],
            ))
        for conn in root.find("connections").findall("connection"):
            sp = conn.attrib["source_instance"].split(":")
            tp = conn.attrib["target_instance"].split(":")
            sd = next(d for d in sym.descriptors if d.identifier == int(sp[0]))
            td = next(d for d in sym.descriptors if d.identifier == int(tp[0]))
            sym.add_connection(sp[4], sd, (td, tp[4]))
        return sym
