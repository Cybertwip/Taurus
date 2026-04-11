#!/usr/bin/env python3
"""Full BFS with pin positions to find +5V/GND short."""
import re, sys
sys.path.insert(0, '.')
from tracer import build_4bit_alu
from pathlib import Path
from collections import defaultdict, deque

sch = build_4bit_alu()
alu = Path('4bit_alu.kicad_sch').read_text()

# Parse actual wires (wire blocks only)
wire_segs = [(float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))
             for m in re.finditer(r'\(wire\s+\(pts\s+\(xy\s+([\d.]+)\s+([\d.]+)\)\s+\(xy\s+([\d.]+)\s+([\d.]+)\)\s*\)', alu, re.DOTALL)]
print(f'Wire segments: {len(wire_segs)}')

# Get KiCad-computed pin positions from lib_symbols (NO y-negation)
# Parse lib_symbol pin positions directly
def get_kicad_pin_positions(alu_text, lib_id, at_x, at_y, rotation):
    """Compute global pin positions as KiCad would."""
    sym_name = lib_id  # e.g. "74xGxx:74LVC1G04"
    start = alu_text.find(f'(symbol "{sym_name}"')
    if start < 0:
        return {}
    # Find the _1_1 subsymbol with pins
    sub_start = alu_text.find('_1_1"', start)
    if sub_start < 0:
        return {}
    end = alu_text.find('(embedded_fonts', sub_start)
    block = alu_text[sub_start:end] if end > 0 else alu_text[sub_start:sub_start+5000]
    
    positions = {}
    for m in re.finditer(r'\(pin\s+\w+\s+\w+\s*\n\s*\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)', block):
        px, py, angle = float(m.group(1)), float(m.group(2)), int(m.group(3))
        # Find pin number
        num_m = re.search(r'\(number\s+"(\d+)"', block[m.start():m.start()+500])
        if num_m:
            pin_num = num_m.group(1)
            # Apply rotation (0 degrees for now)
            import math
            rad = math.radians(rotation)
            rx = px * math.cos(rad) + py * math.sin(rad)
            ry = -px * math.sin(rad) + py * math.cos(rad)
            gx = at_x + rx
            gy = at_y + ry
            positions[pin_num] = (round(gx, 4), round(gy, 4))
    return positions

# Build wire graph
labels = {}
for m in re.finditer(r'\(label\s+"([^"]+)"\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)', alu):
    labels[(round(float(m.group(2)),4), round(float(m.group(3)),4))] = m.group(1)

graph = defaultdict(set)
for x1, y1, x2, y2 in wire_segs:
    p1 = (round(x1,4), round(y1,4))
    p2 = (round(x2,4), round(y2,4))
    graph[p1].add(p2)
    graph[p2].add(p1)

# Add pin-based connections: wire endpoints touching KiCad pin positions
# A pin at position P connects all wires that touch P
pin_positions = {}
for ref, part in sch.parts.items():
    spec = part.device_set.symbol_spec
    kicad_pins = get_kicad_pin_positions(alu, spec.library_id, part.x, part.y, part.rotation)
    for pn, pos in kicad_pins.items():
        pin_positions[(ref, pn)] = pos

# Group wire endpoints by pin position
pin_point_map = defaultdict(list)  # pin_pos -> list of (ref, pin)
for (ref, pn), pos in pin_positions.items():
    pin_point_map[pos].append((ref, pn))

# For each pin position, connect all wire endpoints at that position
for pos, pins in pin_point_map.items():
    if pos in graph:
        # This pin position has wires. All wires at this point are connected through the pin.
        # (Already handled by shared endpoints in graph)
        pass

# Check: do any pin positions coincide?
from collections import Counter
pos_counter = Counter(pin_positions.values())
shared = {p: c for p, c in pos_counter.items() if c > 1}
if shared:
    print(f'Shared pin positions ({len(shared)}):')
    for pos, count in sorted(shared.items()):
        pins_at = [f'{ref}.{pn}' for (ref, pn), p in pin_positions.items() if p == pos]
        # Check if any have different power nets
        vcc_pins = [p for p in pins_at if '.5' in p]
        gnd_pins = [p for p in pins_at if '.3' in p]
        if vcc_pins and gnd_pins:
            print(f'  *** VCC+GND at {pos}: {pins_at}')
        elif len(set(p.split('.')[1] for p in pins_at)) > 1:
            print(f'  Mixed pins at {pos}: {pins_at}')

# Also check: our code's pin positions vs KiCad's
print()
print('Pin position comparison (our code vs KiCad):')
for ref in ['U64', 'U65', 'U66']:
    part = sch.parts[ref]
    spec = part.device_set.symbol_spec
    kicad_pins = get_kicad_pin_positions(alu, spec.library_id, part.x, part.y, part.rotation)
    for pn in ['3', '5']:
        our_pos = sch.pin_position(ref, pn)
        kicad_pos = kicad_pins.get(pn, 'N/A')
        match = 'OK' if our_pos == kicad_pos else 'MISMATCH'
        print(f'  {ref} pin {pn}: ours=({our_pos[0]:.2f},{our_pos[1]:.2f}) kicad={kicad_pos} {match}')
