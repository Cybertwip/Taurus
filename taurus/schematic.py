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
from collections import defaultdict
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


class Instance:
    def __init__(self, component: PlacedPart, schematic: "Schematic",
                 part_name: str, device_set: str, prefix: str):
        self.component = component
        self.schematic = schematic
        self.connections: Dict[str, Tuple["Instance", str]] = {}
        self.device_set = device_set
        self.part = part_name
        self.prefix = prefix

    def wire(self, pin_name: str, target: "Instance", target_pin: str):
        self.connections[pin_name] = (target, target_pin)

    def __getitem__(self, pin_name: str):
        return (self.component.reference, pin_name)


# ---------------------------------------------------------------------------
#  Routing engine
# ---------------------------------------------------------------------------
class _Router:
    def __init__(self, schematic: "Schematic"):
        self.sch = schematic
        self.nets: List[Tuple[str, Set[Tuple[str, str]]]] = []
        self.wires: List[Tuple[float, float, float, float]] = []
        self.labels: List[Tuple[str, float, float, int, str]] = []

    def run(self):
        self._build_nets()
        self._route()

    def _build_nets(self):
        uf = UnionFind()
        nodes: Set[Tuple[str, str]] = set()
        for inst in self.sch.instances:
            for pin, (tgt, tgt_pin) in inst.connections.items():
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

    def _route(self):
        stub = 2.54
        for net_name, members in self.nets:
            member_list = sorted(members)
            if len(member_list) == 2:
                a, b = member_list
                ax, ay = self._pin_pos(*a)
                bx, by = self._pin_pos(*b)
                ao, bo = self._pin_orient(*a), self._pin_orient(*b)
                adx, ady = _outward(ao)
                bdx, bdy = _outward(bo)
                aex, aey = ax + adx * stub, ay + ady * stub
                bex, bey = bx + bdx * stub, by + bdy * stub
                dist = abs(aex - bex) + abs(aey - bey)
                if dist < 150:
                    self.wires.append((ax, ay, aex, aey))
                    self.wires.append((bx, by, bex, bey))
                    mx = (aex + bex) / 2.0
                    # route: stub-a → mid-x → mid-y → stub-b
                    self.wires.append((aex, aey, mx, aey))
                    self.wires.append((mx, aey, mx, bey))
                    self.wires.append((mx, bey, bex, bey))
                    continue

            # Fallback: net labels for multi-pin nets or long distances
            for ref, pin in member_list:
                px, py = self._pin_pos(ref, pin)
                orient = self._pin_orient(ref, pin)
                dx, dy = _outward(orient)
                lx, ly = px + dx * stub, py + dy * stub
                if dx > 0:
                    rot, jst = 180, "right bottom"
                elif dx < 0:
                    rot, jst = 0, "left bottom"
                elif dy < 0:
                    rot, jst = 90, "left bottom"
                else:
                    rot, jst = 270, "right bottom"
                self.wires.append((px, py, lx, ly))
                self.labels.append((net_name, lx, ly, rot, jst))


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
#  KiCad writer
# ---------------------------------------------------------------------------
class _KiCadWriter:
    def __init__(self, sch: "Schematic"):
        self.sch = sch
        self._sym_cache: Dict[str, str] = {}

    def write(self, path: Path):
        proj = path.stem
        root = _uid(proj, "root")
        L = self._header(proj, root)
        L += self._lib_symbols()
        L += self._wires_and_labels(proj)
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
            '\t(paper "A1")',
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
                # Rename top-level and all sub-symbols: "R" → "Device:R", "R_0_1" → "Device:R_0_1"
                block = block.replace(
                    f'(symbol "{spec.symbol_name}_',
                    f'(symbol "{spec.library_id}_',
                )
                block = block.replace(
                    f'(symbol "{spec.symbol_name}"',
                    f'(symbol "{spec.library_id}"',
                )
                self._sym_cache[spec.library_id] = block
            for ln in block.splitlines():
                lines.append(f"\t\t{ln.lstrip()}" if ln.strip() else "")
        lines.append("\t)")
        return lines

    def _wires_and_labels(self, proj: str) -> List[str]:
        router = _Router(self.sch)
        router.run()
        lines: List[str] = []
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
        for idx, (name, lx, ly, rot, jst) in enumerate(router.labels):
            lu = _uid(proj, "label", idx)
            lines += [
                f'\t(label "{name}"',
                f"\t\t(at {_fc(lx)} {_fc(ly)} {rot})",
                "\t\t(effects (font (size 1.27 1.27))",
                f"\t\t\t(justify {jst}))",
                f'\t\t(uuid "{lu}")',
                "\t)",
            ]
        return lines

    def _symbols(self, proj: str, root: str) -> List[str]:
        lines: List[str] = []
        for inst in self.sch.instances:
            lines += self._one_sym(inst.component, proj, root)
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
            if prefix not in SYMBOL_SPECS:
                continue
            alias = REQUIRED_LIBRARY_ALIAS.get(prefix)
            if alias and alias not in sch.libraries:
                sch.init_libraries(alias)
            ds_name = f"{prefix}_imported"
            if ds_name not in sch.device_sets:
                ds = sch.init_device_set(ds_name, prefix)
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

        Eagle(drawing=drawing).save(path)


# ---------------------------------------------------------------------------
#  Schematic (main public class)
# ---------------------------------------------------------------------------
class Schematic:
    def __init__(self, kicad_shared_path: Optional[Path] = None):
        self.kicad_shared = Path(
            kicad_shared_path
            or os.environ.get("TAURUS_KICAD_SHARED_PATH", DEFAULT_KICAD_SHARED)
        )
        self.libraries: Dict[str, dict] = {}
        self.device_sets: Dict[str, DeviceSet] = {}
        self.devices: Dict[tuple, Device] = {}
        self.parts: Dict[str, PlacedPart] = {}
        self.instances: List[Instance] = []
        self.part_counters: Dict[str, int] = defaultdict(int)

    def init_libraries(self, *names: str):
        for name in names:
            if name not in SUPPORTED_LIBRARY_ALIASES:
                raise ValueError(f"Unsupported library alias: {name}")
            self.libraries[name] = SUPPORTED_LIBRARY_ALIASES[name]

    def init_device_set(self, name: str, prefix: str) -> DeviceSet:
        alias = REQUIRED_LIBRARY_ALIAS.get(prefix)
        if alias and alias not in self.libraries:
            raise ValueError(f"Library {alias} not initialized")
        ds = DeviceSet(name=name, prefix=prefix, symbol_spec=SYMBOL_SPECS[prefix])
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
