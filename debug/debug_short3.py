#!/usr/bin/env python3
import re
from pathlib import Path
from collections import defaultdict, deque

alu = Path('4bit_alu.kicad_sch').read_text()

wire_segs = [(float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4)))
             for m in re.finditer(r'\(wire\s+\(pts\s+\(xy\s+([\d.]+)\s+([\d.]+)\)\s+\(xy\s+([\d.]+)\s+([\d.]+)\)\s*\)', alu, re.DOTALL)]
print(f'Wire segments: {len(wire_segs)}')

labels = {}
for m in re.finditer(r'\(label\s+"([^"]+)"\s*\n\s*\(at\s+([\d.]+)\s+([\d.]+)', alu):
    labels[(round(float(m.group(2)),4), round(float(m.group(3)),4))] = m.group(1)

graph = defaultdict(set)
for x1, y1, x2, y2 in wire_segs:
    p1 = (round(x1,4), round(y1,4))
    p2 = (round(x2,4), round(y2,4))
    graph[p1].add(p2)
    graph[p2].add(p1)

for i, (x1, y1, x2, y2) in enumerate(wire_segs):
    for px, py in [(round(x1,4),round(y1,4)), (round(x2,4),round(y2,4))]:
        for j, (ox1, oy1, ox2, oy2) in enumerate(wire_segs):
            if i == j: continue
            op1 = (round(ox1,4), round(oy1,4))
            op2 = (round(ox2,4), round(oy2,4))
            if abs(ox1-ox2)<0.001 and abs(px-ox1)<0.001:
                lo, hi = min(oy1,oy2), max(oy1,oy2)
                if lo+0.001 < py < hi-0.001:
                    graph[(px,py)].update([op1,op2])
                    graph[op1].add((px,py))
                    graph[op2].add((px,py))
            elif abs(oy1-oy2)<0.001 and abs(py-oy1)<0.001:
                lo, hi = min(ox1,ox2), max(ox1,ox2)
                if lo+0.001 < px < hi-0.001:
                    graph[(px,py)].update([op1,op2])
                    graph[op1].add((px,py))
                    graph[op2].add((px,py))

start = (363.22, 30.48)
print(f'Start neighbors: {sorted(graph.get(start, set()))}')

parent = {start: None}
queue = deque([start])
while queue:
    pt = queue.popleft()
    for nb in graph.get(pt, []):
        if nb not in parent:
            parent[nb] = pt
            queue.append(nb)

gnd_reached = [p for p in parent if labels.get(p) == 'GND']
print(f'Reachable: {len(parent)} nodes, GND labels: {len(gnd_reached)}')

if gnd_reached:
    target = gnd_reached[0]
    path = []
    cur = target
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    print(f'Path ({len(path)} pts):')
    for p in path:
        lbl = labels.get(p, '')
        lbl = f' [{lbl}]' if lbl else ''
        print(f'  {p}{lbl}')
else:
    print('No GND reached. All reachable:')
    for p in sorted(parent):
        lbl = labels.get(p, '')
        lbl = f' [{lbl}]' if lbl else ''
        print(f'  {p}{lbl}')
