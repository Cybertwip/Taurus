#!/usr/bin/env python3
"""Create a minimal test using a Conn_01x02 connector (known working in KiCad templates)."""
import sys, re
sys.path.insert(0, '.')
from taurus.schematic import _extract_sym_block
from pathlib import Path

# Get a simple 2-pin connector from KiCad lib
lib_path = Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/Connector_Generic.kicad_sym')
lib_text = lib_path.read_text()
block = _extract_sym_block(lib_text, '(symbol "Conn_01x02"')
# Rename parent only
block = block.replace('(symbol "Conn_01x02"', '(symbol "Connector_Generic:Conn_01x02"', 1)

lib_lines = []
for ln in block.splitlines():
    lib_lines.append('\t\t' + ln.lstrip() if ln.strip() else '')
lib_section = '\n'.join(lib_lines)

# Find pin positions for Conn_01x02_Pin
pins_found = re.findall(r'\(pin\s+\w+\s+\w+\s+\(at\s+([-\d.]+)\s+([-\d.]+)\s+(\d+)\)\s+\(length\s+([\d.]+)\).*?\(number\s+"(\d+)"', block, re.DOTALL)
print("Connector pins:")
for p in pins_found:
    x, y, angle, length, num = p
    print(f"  Pin {num}: at=({x}, {y}) angle={angle} length={length}")

# Place at (100, 100) angle=0
# Pin positions: computed from (at) coordinates
# Pin 1: at (-3.81, 1.27) angle=0 -> global (100 + (-3.81), 100 - 1.27) = (96.19, 98.73)
# Pin 2: at (-3.81, -1.27) angle=0 -> global (100 + (-3.81), 100 + 1.27) = (96.19, 101.27)

for p in pins_found:
    x, y, angle, length, num = p
    gx = 100.0 + float(x)
    gy = 100.0 + (-1)*float(y)
    print(f"  Pin {num} global: ({gx}, {gy})")

# Now also get the AND gate for comparison
and_lib_path = Path('/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols/74xGxx.kicad_sym')
and_lib_text = and_lib_path.read_text()
and_block = _extract_sym_block(and_lib_text, '(symbol "74LVC1G08"')
and_block = and_block.replace('(symbol "74LVC1G08"', '(symbol "74xGxx:74LVC1G08"', 1)
and_lib_lines = []
for ln in and_block.splitlines():
    and_lib_lines.append('\t\t' + ln.lstrip() if ln.strip() else '')
and_lib_section = '\n'.join(and_lib_lines)

# Create schematic with BOTH components: connector + AND gate
# Put AND gate at (67.31, 76.2) as usual
sch = f"""(kicad_sch
\t(version 20250114)
\t(generator "Taurus")
\t(generator_version "1.0")
\t(uuid "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
\t(paper "A4")
\t(lib_symbols
{lib_section}
{and_lib_section}
\t)
\t(wire
\t\t(pts
\t\t\t(xy 94.92 100) (xy 88.57 100)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "11111111-1111-4111-1111-111111111111")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 94.92 102.54) (xy 88.57 102.54)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "22222222-2222-4222-2222-222222222222")
\t)
\t(wire
\t\t(pts
\t\t\t(xy 52.07 78.74) (xy 44.45 78.74)
\t\t)
\t\t(stroke (width 0) (type solid))
\t\t(uuid "33333333-3333-4333-3333-333333333333")
\t)
\t(symbol
\t\t(lib_id "Connector_Generic:Conn_01x02")
\t\t(at 100 100 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "cccccccc-cccc-4ccc-cccc-cccccccccccc")
\t\t(property "Reference" "J1"
\t\t\t(at 100 93.38 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Value" "Conn_01x02_Pin"
\t\t\t(at 100 107.62 0)
\t\t\t(effects (font (size 1.27 1.27)))
\t\t)
\t\t(property "Footprint" ""
\t\t\t(at 100 100 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Datasheet" "~"
\t\t\t(at 100 100 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(property "Description" ""
\t\t\t(at 100 100 0)
\t\t\t(effects (font (size 1.27 1.27)) (hide yes))
\t\t)
\t\t(pin "1"
\t\t\t(uuid "ee111111-1111-4111-1111-111111111111")
\t\t)
\t\t(pin "2"
\t\t\t(uuid "ee222222-2222-4222-2222-222222222222")
\t\t)
\t\t(instances
\t\t\t(project "conn_test"
\t\t\t\t(path "/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
\t\t\t\t\t(reference "J1")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
\t(symbol
\t\t(lib_id "74xGxx:74LVC1G08")
\t\t(at 67.31 76.2 0)
\t\t(unit 1)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(dnp no)
\t\t(uuid "dddddddd-dddd-4ddd-dddd-dddddddddddd")
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
\t\t\t(uuid "ff111111-1111-4111-1111-111111111111")
\t\t)
\t\t(pin "2"
\t\t\t(uuid "ff222222-2222-4222-2222-222222222222")
\t\t)
\t\t(pin "5"
\t\t\t(uuid "ff555555-5555-4555-5555-555555555555")
\t\t)
\t\t(pin "3"
\t\t\t(uuid "ff333333-3333-4333-3333-333333333333")
\t\t)
\t\t(pin "4"
\t\t\t(uuid "ff444444-4444-4444-4444-444444444444")
\t\t)
\t\t(instances
\t\t\t(project "conn_test"
\t\t\t\t(path "/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
\t\t\t\t\t(reference "U1")
\t\t\t\t\t(unit 1)
\t\t\t\t)
\t\t\t)
\t\t)
\t)
\t(sheet_instances
\t\t(path "/"
\t\t\t(page "1")
\t\t)
\t)
)
"""

Path('conn_test.kicad_sch').write_text(sch)
print('Created conn_test.kicad_sch')
