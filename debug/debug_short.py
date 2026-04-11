#!/usr/bin/env python3
"""Find +5V/GND short path in ALU schematic using full wire graph with T-junctions."""
import re, sys
from pathlib import Path
from collections import defaultdict, deque

alu = Path('4bit_alu.kicad_sch').read_text()

wire_segs = [(float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))
             for m in re.finditer(r'\(xy\s+([\d.]+)\s+([\d.]+)\)\s+\(xy\s+([\d.]+)\s+([\d.]+)\)', alu)]
print(f'Wire segments: {len(wire_segs)}', flush=True)

label_pattern = re.compile(r'\(label\s+"([^"]+)"\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)')
labels = {}
for m in label_pattern.finditer(alu):
    name, x, y = m.group(1), float(m.group(2)), float(m.group(3))
    labels[(round(x,4), round(y,4))] = name

graph = defaultdict(set)
for x1, y1, x2, y2 in wire_segs:
    p1 = (round(x1,4), round(y1,4))
    p2 = (round(x2,4), round(y2,4))
    graph[p1].add(p2)
    graph[p2].add(p1)

print(f'Graph after endpoints: {len(graph)} nodes', flush=True)

# T-junctions
t_count = 0
for i, (x1, y1, x2, y2) in enumerate(wire_segs):
    for px, py in [(round(x1,4),round(y1,4)), (round(x2,4),round(y2,4))]:
        for j, (ox1, oy1, ox2, oy2) in enumerate(wire_segs):
            if i == j:
                continue
            op1 = (round(ox1,4), round(oy1,4))
            op2 = (round(ox2,4), round(oy2,4))
            if abs(ox1 - ox2) < 0.001:
                if abs(px - ox1) < 0.001:
                    lo, hi = min(oy1, oy2), max(oy1, oy2)
                    if lo + 0.001 < py < hi - 0.001:
                        t_count += 1
                        graph[(px,py)].add(op1)
                        graph[(px,py)].add(op2)
                        graph[op1].add((px,py))
                        graph[op2].add((px,py))
            elif abs(oy1 - oy2) < 0.001:
                if abs(py - oy1) < 0.001:
                    lo, hi = min(ox1, ox2), max(ox1, ox2)
                    if lo + 0.001 < px < hi - 0.001:
                        t_count += 1
                        graph[(px,py)].add(op1)
                        graph[(px,py)].add(op2)
                        graph[op1].add((px,py))
                        graph[op2].add((px,py))

print(f'T-junctions: {t_count}', flush=True)
print(f'Graph after T-junctions: {len(graph)} nodes', flush=True)

start = (21.59, 30.48)
print(f'Start neighbors: {sorted(graph.get(start, set()))}', flush=True)

parent = {start: None}
queue = deque([start])
while queue:
    pt = queue.popleft()
    for nb in graph.get(pt, []):
        if nb not in parent:
            parent[nb] = pt
            queue.append(nb)

print(f'Reachable nodes: {len(parent)}', flush=True)

reachable_labels = [(p, labels[p]) for p in parent if p in labels]
gnd_in_reach = [p for p, n in reachable_labels if n == 'GND']
v5_in_reach = [p for p, n in reachable_labels if n == '+5V']
print(f'Reachable +5V labels: {len(v5_in_reach)}', flush=True)
print(f'Reachable GND labels: {len(gnd_in_reach)}', flush=True)

other_labels = [(p, n) for p, n in reachable_labels if n not in ('+5V', 'GND')]
print(f'Other reachable labels ({len(other_labels)}):', flush=True)
for p, n in sorted(other_labels, key=lambda x: x[1])[:20]:
    print(f'  {n} at {p}', flush=True)

if gnd_in_reach:
    # Show path to first GND
    target = gnd_in_reach[0]
    path = []
    cur = target
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    print(f'\nPath to GND ({len(path)} points):', flush=True)
    for p in path:
        lbl = labels.get(p, '')
        if lbl: lbl = f' [{lbl}]'
        print(f'  {p}{lbl}', flush=True)
