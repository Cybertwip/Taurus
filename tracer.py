#!/usr/bin/env python3
"""Generate low-level chip schematics with explicit wiring and bit-slice layout."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from taurus import schematic

GRID_MM = 1.27

LOW_LEVEL_GATE_SPECS = {
    "NAND": {"library_id": "74xGxx:74LVC1G00", "inputs": ("1", "2"), "output": "4", "vcc": "5", "gnd": "3"},
    "NOR": {"library_id": "74xGxx:74LVC1G02", "inputs": ("1", "2"), "output": "4", "vcc": "5", "gnd": "3"},
    "NOT": {"library_id": "74xGxx:74LVC1G04", "inputs": ("2",), "output": "4", "vcc": "5", "gnd": "3"},
    "AND": {"library_id": "74xGxx:74LVC1G08", "inputs": ("1", "2"), "output": "4", "vcc": "5", "gnd": "3"},
    "OR": {"library_id": "74xGxx:74LVC1G32", "inputs": ("1", "2"), "output": "4", "vcc": "5", "gnd": "3"},
    "XOR": {"library_id": "74xGxx:74LVC1G86", "inputs": ("1", "2"), "output": "4", "vcc": "5", "gnd": "3"},
}


@dataclass
class GateChip:
    instance: schematic.Instance
    gate_type: str
    input_pins: tuple[str, ...]
    output_pin: str
    vcc_pin: str
    gnd_pin: str

    def label_input(self, index: int, net_name: str,
                    label_type: str = "label", length: float = 7.62):
        self.instance.schematic.label_pin(
            self.instance,
            self.input_pins[index],
            net_name,
            length=length,
            label_type=label_type,
        )

    def label_output(self, net_name: str,
                     label_type: str = "label", length: float = 7.62):
        self.instance.schematic.label_pin(
            self.instance,
            self.output_pin,
            net_name,
            length=length,
            label_type=label_type,
        )

    def wire_to(self, target: "GateChip", target_input_index: int):
        self.instance.wire(self.output_pin, target.instance, target.input_pins[target_input_index])


@dataclass
class ExportGate:
    chips: list[GateChip]
    inputs: list[tuple[GateChip, int]]
    output: GateChip


@dataclass
class AdderSlice:
    xor_ab: GateChip
    and_ab: GateChip
    xor_sum: GateChip
    and_carry: GateChip
    or_carry: GateChip


@dataclass
class AluSlice:
    xor_ab: GateChip
    and_ab: GateChip
    or_ab: GateChip
    xor_sum: GateChip
    and_carry: GateChip
    or_carry: GateChip
    inv_op0: GateChip
    inv_op1: GateChip
    dec_and: GateChip
    dec_or: GateChip
    dec_xor: GateChip
    dec_sum: GateChip
    gate_and: GateChip
    gate_or: GateChip
    gate_xor: GateChip
    gate_sum: GateChip
    mix_ab: GateChip
    mix_cd: GateChip
    out: GateChip


def _snap(value: float) -> float:
    return round(value / GRID_MM) * GRID_MM


def _choose_paper(width_mm: float, height_mm: float) -> str:
    if width_mm <= 210 and height_mm <= 297:
        return "A4"
    if width_mm <= 297 and height_mm <= 420:
        return "A3"
    if width_mm <= 420 and height_mm <= 594:
        return "A2"
    return "A1"


def _new_page_schematic(paper: str = "A3") -> schematic.Schematic:
    return schematic.Schematic(paper=paper)


def _new_sch() -> schematic.Schematic:
    return _new_page_schematic()


def _device_set_name(library_id: str) -> str:
    return library_id.replace(":", "_") + "_"


def _add_real_device(sch: schematic.Schematic, library_id: str,
                     value: str, prefix: str = "U") -> schematic.Instance:
    ds_name = _device_set_name(library_id)
    if ds_name not in sch.device_sets:
        ds = sch.init_device_set(ds_name, prefix, library_id=library_id)
        sch.init_device(ds, value)
    return sch.add_instance(ds_name, value, prefix)


def _build_gate_chip(sch: schematic.Schematic, gate_type: str,
                     x: float, y: float) -> GateChip:
    spec = LOW_LEVEL_GATE_SPECS[gate_type]
    value = spec["library_id"].split(":", 1)[1]
    inst = _add_real_device(sch, spec["library_id"], value)
    sch.place(inst, _snap(x), _snap(y))
    chip = GateChip(
        instance=inst,
        gate_type=gate_type,
        input_pins=tuple(spec["inputs"]),
        output_pin=spec["output"],
        vcc_pin=spec["vcc"],
        gnd_pin=spec["gnd"],
    )
    return chip


def _connect_power_rails(chips: list[GateChip]):
    """Create explicit +5V/GND rail connectivity for a slice of gates."""
    if not chips:
        return

    ordered = sorted(chips, key=lambda c: (c.instance.component.y, c.instance.component.x))
    for prev, curr in zip(ordered, ordered[1:]):
        prev.instance.wire(prev.vcc_pin, curr.instance, curr.vcc_pin)
        prev.instance.wire(prev.gnd_pin, curr.instance, curr.gnd_pin)

    root = ordered[0]
    return root


def _label_power_root(root: GateChip, length: float = 5.08):
    sch = root.instance.schematic
    sch.label_pin(root.instance, root.vcc_pin, "+5V", length=length, label_type="global_output")
    sch.label_pin(root.instance, root.gnd_pin, "GND", length=length, label_type="global_output")


def _fanout(source: GateChip, *targets: tuple[GateChip, int]):
    for target, input_index in targets:
        source.wire_to(target, input_index)


def _stage_box(sch: schematic.Schematic, left: float, top: float,
               right: float, bottom: float, title: str,
               stroke_type: str = "dash"):
    sch.add_box(left, top, right, bottom, stroke_width=0.2, stroke_type=stroke_type)
    sch.add_text(title, left + 1.27, top - 1.27, size=1.5)


def build_nand(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
               input_nets: tuple[Optional[str], Optional[str]] = (None, None),
               output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "NAND", x, y)
    for idx, net in enumerate(input_nets):
        if net:
            chip.label_input(idx, net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_nor(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
              input_nets: tuple[Optional[str], Optional[str]] = (None, None),
              output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "NOR", x, y)
    for idx, net in enumerate(input_nets):
        if net:
            chip.label_input(idx, net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_not(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
              input_net: Optional[str] = None,
              output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "NOT", x, y)
    if input_net:
        chip.label_input(0, input_net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_and(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
              input_nets: tuple[Optional[str], Optional[str]] = (None, None),
              output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "AND", x, y)
    for idx, net in enumerate(input_nets):
        if net:
            chip.label_input(idx, net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_or(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
             input_nets: tuple[Optional[str], Optional[str]] = (None, None),
             output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "OR", x, y)
    for idx, net in enumerate(input_nets):
        if net:
            chip.label_input(idx, net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_xor(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
              input_nets: tuple[Optional[str], Optional[str]] = (None, None),
              output_net: Optional[str] = None) -> GateChip:
    chip = _build_gate_chip(sch, "XOR", x, y)
    for idx, net in enumerate(input_nets):
        if net:
            chip.label_input(idx, net)
    if output_net:
        chip.label_output(output_net)
    return chip


def build_xnor(sch: schematic.Schematic, x: float = 30.48, y: float = 30.48,
               input_nets: tuple[Optional[str], Optional[str]] = (None, None),
               output_net: Optional[str] = None,
               net_prefix: str = "XNOR") -> tuple[GateChip, GateChip]:
    xor_net = f"{net_prefix}_XOR"
    xor_chip = build_xor(sch, x, y, input_nets, None)
    not_chip = build_not(sch, x, y + 20.32, None, output_net)
    xor_chip.label_output(xor_net)
    xor_chip.wire_to(not_chip, 0)
    return xor_chip, not_chip


def _make_export_gate(sch: schematic.Schematic, gate_type: str, x: float, y: float,
                      tag: str) -> ExportGate:
    if gate_type == "NOT":
        chip = _build_gate_chip(sch, "NOT", x, y)
        return ExportGate([chip], [(chip, 0)], chip)
    if gate_type == "XNOR":
        xor_chip = _build_gate_chip(sch, "XOR", x, y)
        not_chip = _build_gate_chip(sch, "NOT", x, y + 17.78)
        xor_chip.wire_to(not_chip, 0)
        return ExportGate([xor_chip, not_chip], [(xor_chip, 0), (xor_chip, 1)], not_chip)
    chip = _build_gate_chip(sch, gate_type, x, y)
    return ExportGate([chip], [(chip, idx) for idx in range(len(chip.input_pins))], chip)


def _build_adder_slice(sch: schematic.Schematic, bit: int,
                       left: float, top: float) -> AdderSlice:
    sch.add_box(left, top, left + 68.58, top + 109.22, stroke_width=0.2, stroke_type="solid")
    sch.add_text(f"BIT {bit}", left + 1.27, top - 2.54, size=1.8)
    _stage_box(sch, left + 2.54, top + 10.16, left + 30.48, top + 63.50, "Logic")
    _stage_box(sch, left + 35.56, top + 10.16, left + 66.04, top + 95.25, "Carry")

    xor_ab = _build_gate_chip(sch, "XOR", left + 16.51, top + 27.94)
    and_ab = _build_gate_chip(sch, "AND", left + 16.51, top + 50.80)
    xor_sum = _build_gate_chip(sch, "XOR", left + 49.53, top + 27.94)
    and_carry = _build_gate_chip(sch, "AND", left + 49.53, top + 50.80)
    or_carry = _build_gate_chip(sch, "OR", left + 49.53, top + 73.66)
    _connect_power_rails([xor_ab, and_ab, xor_sum, and_carry, or_carry])

    xor_ab.label_input(0, f"A{bit}", label_type="global_output", length=5.08)
    xor_ab.label_input(1, f"B{bit}", label_type="global_output", length=5.08)
    and_ab.label_input(0, f"A{bit}")
    and_ab.label_input(1, f"B{bit}")

    _fanout(xor_ab, (xor_sum, 0), (and_carry, 1))
    and_ab.wire_to(or_carry, 0)
    and_carry.wire_to(or_carry, 1)
    xor_sum.label_output(f"S{bit}", label_type="global_input", length=5.08)

    return AdderSlice(xor_ab, and_ab, xor_sum, and_carry, or_carry)


def build_4bit_adder() -> schematic.Schematic:
    sch = _new_page_schematic("A2")
    slices = []
    left = 17.78
    top = 25.40
    step = 88.90
    for bit in range(4):
        slices.append(_build_adder_slice(sch, bit, left + bit * step, top))

    for idx in range(3):
        slices[idx].xor_ab.instance.wire(slices[idx].xor_ab.vcc_pin, slices[idx + 1].xor_ab.instance, slices[idx + 1].xor_ab.vcc_pin)
        slices[idx].xor_ab.instance.wire(slices[idx].xor_ab.gnd_pin, slices[idx + 1].xor_ab.instance, slices[idx + 1].xor_ab.gnd_pin)
    _label_power_root(slices[0].xor_ab, length=5.08)

    slices[0].xor_sum.label_input(1, "CIN", label_type="global_output", length=5.08)
    slices[0].and_carry.label_input(0, "CIN")

    for idx in range(3):
        carry_source = slices[idx].or_carry
        _fanout(carry_source, (slices[idx + 1].xor_sum, 1), (slices[idx + 1].and_carry, 0))
    slices[-1].or_carry.label_output("COUT", label_type="global_input", length=5.08)
    return sch


def _build_alu_slice(sch: schematic.Schematic, bit: int,
                     left: float, top: float) -> AluSlice:
    sch.add_box(left, top, left + 86.36, top + 300.99, stroke_width=0.2, stroke_type="solid")
    sch.add_text(f"BIT {bit}", left + 1.27, top - 2.54, size=1.8)
    _stage_box(sch, left + 2.54, top + 10.16, left + 25.40, top + 91.44, "Logic")
    _stage_box(sch, left + 30.48, top + 10.16, left + 53.34, top + 121.92, "Carry")
    _stage_box(sch, left + 58.42, top + 10.16, left + 83.82, top + 287.02, "Select")

    xor_ab = _build_gate_chip(sch, "XOR", left + 13.97, top + 27.94)
    and_ab = _build_gate_chip(sch, "AND", left + 13.97, top + 50.80)
    or_ab = _build_gate_chip(sch, "OR", left + 13.97, top + 73.66)

    xor_sum = _build_gate_chip(sch, "XOR", left + 41.91, top + 27.94)
    and_carry = _build_gate_chip(sch, "AND", left + 41.91, top + 50.80)
    or_carry = _build_gate_chip(sch, "OR", left + 41.91, top + 73.66)

    inv_op0 = _build_gate_chip(sch, "NOT", left + 71.12, top + 27.94)
    inv_op1 = _build_gate_chip(sch, "NOT", left + 71.12, top + 43.18)
    dec_and = _build_gate_chip(sch, "AND", left + 71.12, top + 66.04)
    dec_or = _build_gate_chip(sch, "AND", left + 71.12, top + 81.28)
    dec_xor = _build_gate_chip(sch, "AND", left + 71.12, top + 104.14)
    dec_sum = _build_gate_chip(sch, "AND", left + 71.12, top + 119.38)
    gate_and = _build_gate_chip(sch, "AND", left + 71.12, top + 142.24)
    gate_or = _build_gate_chip(sch, "AND", left + 71.12, top + 157.48)
    gate_xor = _build_gate_chip(sch, "AND", left + 71.12, top + 180.34)
    gate_sum = _build_gate_chip(sch, "AND", left + 71.12, top + 195.58)
    mix_ab = _build_gate_chip(sch, "OR", left + 71.12, top + 218.44)
    mix_cd = _build_gate_chip(sch, "OR", left + 71.12, top + 233.68)
    out = _build_gate_chip(sch, "OR", left + 71.12, top + 259.08)
    _connect_power_rails([
        xor_ab, and_ab, or_ab,
        xor_sum, and_carry, or_carry,
        inv_op0, inv_op1,
        dec_and, dec_or, dec_xor, dec_sum,
        gate_and, gate_or, gate_xor, gate_sum,
        mix_ab, mix_cd, out,
    ])

    xor_ab.label_input(0, f"A{bit}", label_type="global_output", length=5.08)
    xor_ab.label_input(1, f"B{bit}", label_type="global_output", length=5.08)
    for chip in (and_ab, or_ab):
        chip.label_input(0, f"A{bit}")
        chip.label_input(1, f"B{bit}")

    op_label_type = "global_output" if bit == 0 else "label"
    op_label_length = 5.08 if bit == 0 else 7.62
    inv_op0.label_input(0, "OP0", label_type=op_label_type, length=op_label_length)
    inv_op1.label_input(0, "OP1", label_type=op_label_type, length=op_label_length)
    dec_or.label_input (1, "OP0")
    dec_sum.label_input(1, "OP0")
    dec_xor.label_input(1, "OP1")
    dec_sum.label_input(0, "OP1")

    _fanout(xor_ab, (xor_sum, 0), (and_carry, 1), (gate_xor, 0))
    and_ab.wire_to(or_carry, 0)
    and_ab.wire_to(gate_and, 0)
    or_ab.wire_to(gate_or, 0)
    xor_sum.wire_to(gate_sum, 0)
    and_carry.wire_to(or_carry, 1)

    _fanout(inv_op0, (dec_and, 1), (dec_xor, 0))
    _fanout(inv_op1, (dec_and, 0), (dec_or, 0))

    dec_and.wire_to(gate_and, 1)
    dec_or.wire_to(gate_or, 1)
    dec_xor.wire_to(gate_xor, 1)
    dec_sum.wire_to(gate_sum, 1)

    gate_and.wire_to(mix_ab, 0)
    gate_or.wire_to(mix_ab, 1)
    gate_xor.wire_to(mix_cd, 0)
    gate_sum.wire_to(mix_cd, 1)
    mix_ab.wire_to(out, 0)
    mix_cd.wire_to(out, 1)
    out.label_output(f"F{bit}", label_type="global_input", length=5.08)

    return AluSlice(
        xor_ab, and_ab, or_ab,
        xor_sum, and_carry, or_carry,
        inv_op0, inv_op1,
        dec_and, dec_or, dec_xor, dec_sum,
        gate_and, gate_or, gate_xor, gate_sum,
        mix_ab, mix_cd, out,
    )


def build_4bit_alu() -> schematic.Schematic:
    sch = _new_page_schematic("A2")
    slices = []
    left = 12.70
    top = 20.32
    step = 96.52
    for bit in range(4):
        slices.append(_build_alu_slice(sch, bit, left + bit * step, top))

    for idx in range(3):
        # Stitch power rails between neighboring slices.
        slices[idx].xor_ab.instance.wire(slices[idx].xor_ab.vcc_pin, slices[idx + 1].xor_ab.instance, slices[idx + 1].xor_ab.vcc_pin)
        slices[idx].xor_ab.instance.wire(slices[idx].xor_ab.gnd_pin, slices[idx + 1].xor_ab.instance, slices[idx + 1].xor_ab.gnd_pin)
        # Explicitly chain ALU operation controls between slices.
        slices[idx].inv_op0.instance.wire(slices[idx].inv_op0.input_pins[0], slices[idx + 1].inv_op0.instance, slices[idx + 1].inv_op0.input_pins[0])
        slices[idx].inv_op1.instance.wire(slices[idx].inv_op1.input_pins[0], slices[idx + 1].inv_op1.instance, slices[idx + 1].inv_op1.input_pins[0])
    _label_power_root(slices[0].xor_ab, length=5.08)

    slices[0].xor_sum.label_input(1, "CIN", label_type="global_output", length=5.08)
    slices[0].and_carry.label_input(0, "CIN")

    for idx in range(3):
        _fanout(slices[idx].or_carry, (slices[idx + 1].xor_sum, 1), (slices[idx + 1].and_carry, 0))
    slices[-1].or_carry.label_output("COUT", label_type="global_input", length=5.08)
    return sch


def build_riscv_alu_slice() -> schematic.Schematic:
    return build_4bit_alu()


def _normalize_project_position(x: float, y: float,
                                min_x: float, min_y: float,
                                scale: float = 0.05,
                                margin: float = 25.40) -> tuple[float, float]:
    return _snap(margin + (x - min_x) * scale), _snap(margin + (y - min_y) * scale)


def _canonical_net_names(project: dict) -> tuple[dict[str, str], dict[str, str]]:
    input_names: dict[str, str] = {}
    output_names: dict[str, str] = {}
    for idx, input_data in enumerate(project.get("inputs", [])):
        input_names[input_data["port"]["uuid"]] = f"IN{idx}"
    for idx, output_data in enumerate(project.get("outputs", [])):
        source_uuid = output_data["port"].get("connected_from")
        if source_uuid:
            output_names[source_uuid] = f"OUT{idx}"
    return input_names, output_names


def export_project_to_low_level(project_path: str, output_path: str,
                                backend: str = "kicad") -> schematic.Schematic:
    project = json.loads(Path(project_path).read_text(encoding="utf-8"))
    gate_positions = [(gate["x"], gate["y"]) for gate in project.get("gates", [])]
    if gate_positions:
        xs = [pos[0] for pos in gate_positions]
        ys = [pos[1] for pos in gate_positions]
        width_mm = (max(xs) - min(xs)) * 0.05 + 50.8
        height_mm = (max(ys) - min(ys)) * 0.05 + 50.8
        paper = _choose_paper(width_mm, height_mm)
        min_x = min(xs)
        min_y = min(ys)
    else:
        paper = "A4"
        min_x = 0.0
        min_y = 0.0

    sch = _new_page_schematic(paper)
    input_names, output_names = _canonical_net_names(project)
    gate_map: dict[str, ExportGate] = {}
    port_to_gate: dict[str, ExportGate] = {}
    seen_input_nets: set[str] = set()

    for idx, gate_data in enumerate(project.get("gates", [])):
        gate_type = gate_data.get("type", gate_data.get("gate_type", "AND"))
        x, y = _normalize_project_position(gate_data["x"], gate_data["y"], min_x, min_y)
        exported = _make_export_gate(sch, gate_type, x, y, f"G{idx}")
        gate_map[gate_data["uuid"]] = exported
        port_to_gate[gate_data["output"]["uuid"]] = exported

    for gate_data in project.get("gates", []):
        exported = gate_map[gate_data["uuid"]]
        for idx, input_port in enumerate(gate_data.get("input", [])):
            if idx >= len(exported.inputs):
                continue
            target_chip, target_input = exported.inputs[idx]
            source_uuid = input_port.get("connected_from")
            if not source_uuid:
                continue
            if source_uuid in port_to_gate:
                port_to_gate[source_uuid].output.wire_to(target_chip, target_input)
            elif source_uuid in input_names:
                net_name = input_names[source_uuid]
                if net_name in seen_input_nets:
                    target_chip.label_input(target_input, net_name)
                else:
                    target_chip.label_input(target_input, net_name,
                                            label_type="global_output", length=0.0)
                    seen_input_nets.add(net_name)

    for source_uuid, out_name in output_names.items():
        if source_uuid in port_to_gate:
            port_to_gate[source_uuid].output.label_output(
                out_name,
                label_type="global_input",
                length=0.0,
            )

    sch.save(output_path, backend=backend)
    return sch


def roundtrip_test(src_path: str) -> bool:
    print(f"Roundtrip test: {src_path}")
    sch1 = schematic.Schematic.load(src_path)
    tmp = src_path.replace(".kicad_sch", "_rt.kicad_sch")
    sch1.save(tmp, backend="kicad")
    sch2 = schematic.Schematic.load(tmp)
    n1 = len(sch1.instances)
    n2 = len(sch2.instances)
    ok = n1 == n2
    print(f"  Original: {n1} components, Roundtrip: {n2} components -> {'PASS' if ok else 'FAIL'}")
    return ok


def _export_pair(sch: schematic.Schematic, stem: str):
    sch.save(f"{stem}.kicad_sch", backend="kicad")
    sch.save(f"{stem}.sch", backend="eagle")
    roundtrip_test(f"{stem}.kicad_sch")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("adder", "all"):
        print("=== 4-bit Adder ===")
        _export_pair(build_4bit_adder(), "4bit_adder")

    if target in ("alu", "riscv", "all"):
        print("\n=== 4-bit ALU ===")
        _export_pair(build_4bit_alu(), "4bit_alu")

    print("\nDone.")


if __name__ == "__main__":
    main()
