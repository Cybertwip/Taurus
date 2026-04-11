#!/usr/bin/env python3
"""Remove specific crossing wires from the schematic to test connectivity."""
import re, sys
sys.path.insert(0, '.')
from taurus.schematic import _extract_sym_block
from pathlib import Path

content = Path('4bit_adder.kicad_sch').read_text()

# Each wire block looks like:
# \t(wire\n\t\t(pts\n\t\t\t(xy X1 Y1) (xy X2 Y2)\n\t\t)\n\t\t(stroke ...)\n\t\t(uuid "...")\n\t)
# Remove wire blocks containing specific coordinate patterns

# These are the CIN wires crossing the backbone in slice 0
targets = ['52.07 55.88', '52.07 73.66']

# Use regex to find and remove complete wire blocks
for target in targets:
    pattern = r'\t\(wire\n\t\t\(pts\n\t\t\t\(xy ' + re.escape(target) + r'[^\)]*\)\n\t\t\)\n\t\t\(stroke[^\)]*\)\n\t\t\(uuid "[^"]*"\)\n\t\)\n'
    matches = re.findall(pattern, content)
    if matches:
        content = content.replace(matches[0], '')
        print(f'Removed wire with {target}')
    else:
        print(f'No match for {target}, trying alternative...')
        # Try to find the wire line and extract surrounding block
        idx = content.find(target)
        if idx >= 0:
            # Find the enclosing (wire ... ) block
            wire_start = content.rfind('\t(wire', 0, idx)
            # Find closing ) by counting parens
            depth = 0
            i = wire_start
            while i < len(content):
                if content[i] == '(':
                    depth += 1
                elif content[i] == ')':
                    depth -= 1
                    if depth == 0:
                        wire_end = i + 1
                        # Include trailing newline
                        if wire_end < len(content) and content[wire_end] == '\n':
                            wire_end += 1
                        block = content[wire_start:wire_end]
                        content = content[:wire_start] + content[wire_end:]
                        print(f'Removed wire block ({len(block)} chars) with {target}')
                        break
                i += 1

Path('4bit_adder_no_cross.kicad_sch').write_text(content)
print('Created 4bit_adder_no_cross.kicad_sch')
