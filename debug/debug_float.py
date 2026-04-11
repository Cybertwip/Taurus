#!/usr/bin/env python3
"""Check floating-point precision of pin position vs wire endpoint."""

# Wire endpoint parsed from '52.07'
wire_x = float('52.07')
wire_y = float('78.74')

# Pin position computed from symbol position + transformed pin
sym_x = float('67.31')
sym_y = float('76.2')
pin_lib_x = float('-15.24')
pin_lib_y = float('-2.54')

# Transform: x1=1, y1=0, x2=0, y2=-1
pin_global_x = sym_x + pin_lib_x
pin_global_y = sym_y + (-1) * pin_lib_y  # 76.2 + 2.54 = 78.74

print(f"Wire X: {wire_x:.20f}")
print(f"Pin X:  {pin_global_x:.20f}")
print(f"X match: {wire_x == pin_global_x}")
print(f"X diff nm: {(wire_x - pin_global_x) * 1e6:.6f}")
print()
print(f"Wire Y: {wire_y:.20f}")
print(f"Pin Y:  {pin_global_y:.20f}")
print(f"Y match: {wire_y == pin_global_y}")
print(f"Y diff nm: {(wire_y - pin_global_y) * 1e6:.6f}")
print()

# Integer conversions (KiCad uses nanometers internally)
wire_x_iu = round(wire_x * 1e6)
wire_y_iu = round(wire_y * 1e6)
pin_x_iu = round(pin_global_x * 1e6)
pin_y_iu = round(pin_global_y * 1e6)
print(f"Wire X IU: {wire_x_iu}, Pin X IU: {pin_x_iu}, Match: {wire_x_iu == pin_x_iu}")
print(f"Wire Y IU: {wire_y_iu}, Pin Y IU: {pin_y_iu}, Match: {wire_y_iu == pin_y_iu}")

# Check U3 pin 1 (WORKS)
print("\n=== U3 Pin 1 (WORKS) ===")
sym3_y = float('53.34')
pin3_lib_y = float('2.54')
pin3_global_y = sym3_y + (-1) * pin3_lib_y  # 53.34 - 2.54 = 50.80
wire3_y = float('50.8')
print(f"Wire Y: {wire3_y:.20f}")
print(f"Pin Y:  {pin3_global_y:.20f}")
print(f"Y match: {wire3_y == pin3_global_y}")
print(f"Y diff nm: {(wire3_y - pin3_global_y) * 1e6:.6f}")
w3_iu = round(wire3_y * 1e6)
p3_iu = round(pin3_global_y * 1e6)
print(f"Wire Y IU: {w3_iu}, Pin Y IU: {p3_iu}, Match: {w3_iu == p3_iu}")

# Check U4 pin 1 at (52.07, 73.66) (WORKS via CIN label)
print("\n=== U4 Pin 1 (WORKS) ===")
sym4_y = float('76.2')
pin4_1_lib_y = float('2.54')  # pin 1 lib y
pin4_1_global_y = sym4_y + (-1) * pin4_1_lib_y  # 76.2 - 2.54 = 73.66
wire4_1_y = float('73.66')
print(f"Wire Y: {wire4_1_y:.20f}")
print(f"Pin Y:  {pin4_1_global_y:.20f}")
print(f"Y match: {wire4_1_y == pin4_1_global_y}")
print(f"Y diff nm: {(wire4_1_y - pin4_1_global_y) * 1e6:.6f}")
w4_1_iu = round(wire4_1_y * 1e6)
p4_1_iu = round(pin4_1_global_y * 1e6)
print(f"Wire Y IU: {w4_1_iu}, Pin Y IU: {p4_1_iu}, Match: {w4_1_iu == p4_1_iu}")

# KiCad schematic IU might be different. Let's also check mils conversion
# 1 mil = 0.0254 mm, so 1 mm = 39.3701 mils
# KiCad schematic uses IU = 1/1000 inch = 25.4 um = 25400 nm
# Actually in KiCad 9, schematic IU = nm (1e6 per mm)
# But let's also check 10nm units
print("\n=== Check with different IU scales ===")
for scale_name, scale in [("nm (1e6)", 1e6), ("10nm (1e5)", 1e5), ("um (1e3)", 1e3), ("mil*1000 (39370.1)", 39370.078740157)]:
    w_iu = round(wire_y * scale)
    p_iu = round(pin_global_y * scale)
    print(f"  {scale_name}: Wire={w_iu}, Pin={p_iu}, Match={w_iu == p_iu}, Diff={w_iu - p_iu}")
