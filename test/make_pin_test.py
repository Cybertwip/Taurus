#!/usr/bin/env python3
"""Create a minimal test schematic to verify pin connectivity."""
import sys
sys.path.insert(0, '.')
from taurus.schematic import _extract_sym_block
from pathlib import Path

# Get the AND gate symbol from the library
lib_path = Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/74xGxx.kicad_sym')
lib_text = lib_path.read_text()
block = _extract_sym_block(lib_text, '(symbol "74LVC1G08"')
# Only rename the parent symbol (first occurrence)
block = block.replace('(symbol "74LVC1G08"', '(symbol "74xGxx:74LVC1G08"', 1)

# Format the lib_symbols section
lib_lines = []
for ln in block.splitlines():
    lib_lines.append('\t\t' + ln.lstrip() if ln.strip() else '')
lib_section = '\n'.join(lib_lines)

# Place symbol at (67.31, 76.2) angle 0
# Pin 1: lib (-15.24, 2.54) -> global (52.07, 73.66)
# Pin 2: lib (-15.24, -2.54) -> global (52.07, 78.74)
# Pin 4: lib (12.7, 0) -> global (80.01, 76.2) [output, angle 180]

# Wire from pin 2 position to the left
sch = f"""(kicad_sch
\t(version 20250114)
\t(generator "Taurus")
\t(generator_version "1.0")
\t(uuid "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
\t(paper "A4")
\t(lib_symbols
{lib_section}
\t)
\t(wire
\t\t(pts
\t\t\t(xy 52.07 78.74) (xy 44.45 78.74)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "bbbbbbbb-bbb1-4bbb-bbbb-bbbbbbbbbbbb")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 52.07 73.66) (xy 44.45 73.66)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "bbbbbbbb-bbb2-4bbb-bbbb-bbbbbbbbbbbb")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 80.01 76.2) (xy 87.63 76.2)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "bbbbbbbb-bbb3-4bbb-bbbb-bbbbbbbbbbbb")
\t)
\t(symbol
\t\t(lib_id "74xGxx:74LVC1G08")
\t\t(at 67.31 76.2 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "cccccccc-cccc-4ccc-cccc-cccccccccccc")
\t\t(property "Reference" "U1"
\t\t\t(at 62.23 68.58 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "74LVC1G08"
\t\t\t(at 74.93 83.82 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at 67.31 76.2 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Datasheet" "~"
\t\t\t(at 67.31 76.2 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Description" "Single AND Gate, Low-Voltage CMOS"
\t\t\t(at 67.31 76.2 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(pin "1"
\t\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddd01")
\t\t)
\t\t(pin "2"
\t\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddd02")
\t\t)
\t\t(pin "5"
\t\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddd05")
\t\t)
\t\t(pin "3"
\t\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddd03")
\t\t)
\t\t(pin "4"
\t\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddd04")
\t\t)
\t\t(instances
\t\t\t(project "pin_test"
\t\t\t\t(path "/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
\t\t\t\t\t(reference "U1")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
)
"""

Path('pin_test_v2.kicad_sch').write_text(sch)
print('Created pin_test_v2.kicad_sch')
