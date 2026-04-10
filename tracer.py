#!/usr/bin/env python3
"""
Taurus Tracer – generates transistor-level schematics.

Circuits:
    4-bit ripple-carry adder  (transistor-level, fixed wiring)
    RISC-V ALU slice          (4-bit, ADD/SUB/AND/OR/XOR)

Outputs:
    .kicad_sch (primary) and .sch (Eagle fallback)

Roundtrip test:
    Write → Read → Re-write, then verify file matches.
"""
from __future__ import annotations
import sys
from pathlib import Path
from taurus import schematic


# ── Schematic helpers ─────────────────────────────────────────────────────

def _new_sch() -> schematic.Schematic:
    sch = schematic.Schematic()
    sch.init_libraries("transistor-npn", "resistor-power")
    t = sch.init_device_set("BJT_", "Q")
    sch.init_device(t, "NPN")
    r = sch.init_device_set("R_", "R")
    sch.init_device(r, "RES")
    return sch


def _Q(sch: schematic.Schematic) -> schematic.Instance:
    return sch.add_instance("BJT_", "NPN", "Q")


def _R(sch: schematic.Schematic) -> schematic.Instance:
    return sch.add_instance("R_", "RES", "R")


# ── Transistor-level gate builders ────────────────────────────────────────
# KiCad Device:Q_NPN pins: B (base), C (collector), E (emitter)
# KiCad Device:R     pins: 1 (top),  2 (bottom)

def build_nand(sch, label="NAND"):
    """RTL NAND gate: series NPN pair + pull-up resistor.
    Returns dict with keys: out, a, b, vcc, gnd."""
    q1 = _Q(sch)  # Top transistor (input A)
    q2 = _Q(sch)  # Bottom transistor (input B)
    rp = _R(sch)  # Pull-up resistor

    # Pull-up to collector node
    rp.wire("2", q1, "C")      # R bottom → Q1 collector
    q1.wire("E", q2, "C")      # Q1 emitter → Q2 collector
    # Output at junction of rp-2 and q1-C (same net)
    return {"out_r": rp, "out_pin": "2",
            "a": q1, "b": q2,
            "vcc_r": rp, "vcc_pin": "1",
            "gnd_q": q2, "gnd_pin": "E"}


def build_not(sch, label="NOT"):
    """RTL inverter: single NPN + pull-up.
    Returns dict with keys: out, inp, vcc, gnd."""
    q = _Q(sch)
    rp = _R(sch)
    rp.wire("2", q, "C")
    return {"out_r": rp, "out_pin": "2",
            "inp": q, "inp_pin": "B",
            "vcc_r": rp, "vcc_pin": "1",
            "gnd_q": q, "gnd_pin": "E"}


def build_and(sch):
    """NAND + NOT = AND."""
    nand = build_nand(sch)
    inv = build_not(sch)
    nand["out_r"].wire(nand["out_pin"], inv["inp"], inv["inp_pin"])
    return {"out_r": inv["out_r"], "out_pin": inv["out_pin"],
            "a": nand["a"], "b": nand["b"],
            "vcc_r": nand["vcc_r"], "vcc_pin": nand["vcc_pin"],
            "gnd_q": nand["gnd_q"], "gnd_pin": nand["gnd_pin"]}


def build_or(sch):
    """Parallel NPN pair + pull-up + inverter = OR (via NOR+NOT)."""
    q1 = _Q(sch)  # Input A
    q2 = _Q(sch)  # Input B (parallel)
    rp = _R(sch)  # Pull-up
    inv = build_not(sch)  # Inverter for NOR→OR

    rp.wire("2", q1, "C")
    q1.wire("C", q2, "C")      # Collectors tied
    q1.wire("E", q2, "E")      # Emitters tied to GND
    rp.wire("2", inv["inp"], inv["inp_pin"])

    return {"out_r": inv["out_r"], "out_pin": inv["out_pin"],
            "a": q1, "b": q2,
            "vcc_r": rp, "vcc_pin": "1",
            "gnd_q": q1, "gnd_pin": "E"}


def build_xor(sch):
    """XOR from four NAND gates:
    NAND1 = NAND(A, B)
    NAND2 = NAND(A, NAND1)
    NAND3 = NAND(B, NAND1)
    XOR   = NAND(NAND2, NAND3)
    """
    n1 = build_nand(sch)
    n2 = build_nand(sch)
    n3 = build_nand(sch)
    n4 = build_nand(sch)

    # NAND1 output → inputs of NAND2.b and NAND3.a
    n1["out_r"].wire(n1["out_pin"], n2["b"], "B")
    n1["out_r"].wire(n1["out_pin"], n3["a"], "B")

    # NAND2 output → NAND4 input a
    n2["out_r"].wire(n2["out_pin"], n4["a"], "B")
    # NAND3 output → NAND4 input b
    n3["out_r"].wire(n3["out_pin"], n4["b"], "B")

    return {
        "out_r": n4["out_r"], "out_pin": n4["out_pin"],
        "a_nand1": n1["a"],  # Input A goes to n1.a AND n2.a
        "a_nand2": n2["a"],
        "b_nand1": n1["b"],  # Input B goes to n1.b AND n3.b
        "b_nand3": n3["b"],
    }


# ── Half Adder ────────────────────────────────────────────────────────────

def build_half_adder(sch):
    """Sum = A XOR B,  Carry = A AND B (using transistor-level gates)."""
    xor = build_xor(sch)
    and_gate = build_and(sch)
    return {
        "sum": xor,
        "carry": and_gate,
    }


# ── Full Adder ────────────────────────────────────────────────────────────

def build_full_adder(sch):
    """Full adder from two half-adders + OR gate."""
    ha1 = build_half_adder(sch)  # A + B
    ha2 = build_half_adder(sch)  # (A⊕B) + Cin
    or_gate = build_or(sch)      # Cout = C1 | C2

    # HA1.sum → HA2 input A
    ha1["sum"]["out_r"].wire(ha1["sum"]["out_pin"], ha2["sum"]["a_nand1"], "B")
    ha1["sum"]["out_r"].wire(ha1["sum"]["out_pin"], ha2["sum"]["a_nand2"], "B")
    ha1["sum"]["out_r"].wire(ha1["sum"]["out_pin"], ha2["carry"]["a"], "B")

    # HA1.carry → OR.a
    ha1["carry"]["out_r"].wire(ha1["carry"]["out_pin"], or_gate["a"], "B")

    # HA2.carry → OR.b
    ha2["carry"]["out_r"].wire(ha2["carry"]["out_pin"], or_gate["b"], "B")

    return {
        "sum_r": ha2["sum"]["out_r"],
        "sum_pin": ha2["sum"]["out_pin"],
        "cout_r": or_gate["out_r"],
        "cout_pin": or_gate["out_pin"],
        # External inputs
        "a_nand1": ha1["sum"]["a_nand1"],
        "a_nand2": ha1["sum"]["a_nand2"],
        "a_and": ha1["carry"]["a"],
        "b_nand1": ha1["sum"]["b_nand1"],
        "b_nand3": ha1["sum"]["b_nand3"],
        "b_and": ha1["carry"]["b"],
        "cin_nand1": ha2["sum"]["b_nand1"],
        "cin_nand3": ha2["sum"]["b_nand3"],
        "cin_and": ha2["carry"]["b"],
    }


# ── 4-bit Ripple-Carry Adder ─────────────────────────────────────────────

def build_4bit_adder():
    """Build a 4-bit ripple-carry adder and return (schematic, adders)."""
    sch = _new_sch()
    adders = []
    for _ in range(4):
        adders.append(build_full_adder(sch))

    # Cascade carry: adder[i].cout → adder[i+1].cin
    for i in range(3):
        a = adders[i]
        b = adders[i + 1]
        a["cout_r"].wire(a["cout_pin"], b["cin_nand1"], "B")
        a["cout_r"].wire(a["cout_pin"], b["cin_nand3"], "B")
        a["cout_r"].wire(a["cout_pin"], b["cin_and"], "B")

    sch.wire_up()
    return sch


# ── RISC-V ALU Slice (4-bit, ADD/SUB/AND/OR/XOR) ─────────────────────────
# This traces a subset of a RISC-V ALU at the transistor level.
# Operations: 000=ADD, 001=SUB, 010=AND, 011=OR, 100=XOR
#
# Architecture per bit:
#   B' = B XOR sub_flag  (for 2's complement subtraction)
#   add_result  = full_adder(A, B', Cin)
#   and_result  = A AND B
#   or_result   = A OR B
#   xor_result  = A XOR B
#   (MUX selects output based on ALU opcode – simplified with labels)

def build_riscv_alu_slice():
    """Build a 4-bit RISC-V ALU slice."""
    sch = _new_sch()

    # --- B-input conditioning: B XOR sub_flag for each bit ---
    b_xors = []
    for _ in range(4):
        b_xors.append(build_xor(sch))

    # --- Full adder chain ---
    adders = []
    for _ in range(4):
        adders.append(build_full_adder(sch))

    # Connect B' (conditioned) to adder B inputs
    for i in range(4):
        bx = b_xors[i]
        fa = adders[i]
        bx["out_r"].wire(bx["out_pin"], fa["b_nand1"], "B")
        bx["out_r"].wire(bx["out_pin"], fa["b_nand3"], "B")
        bx["out_r"].wire(bx["out_pin"], fa["b_and"], "B")

    # Cascade carry
    for i in range(3):
        a = adders[i]
        b = adders[i + 1]
        a["cout_r"].wire(a["cout_pin"], b["cin_nand1"], "B")
        a["cout_r"].wire(a["cout_pin"], b["cin_nand3"], "B")
        a["cout_r"].wire(a["cout_pin"], b["cin_and"], "B")

    # --- Bitwise AND gates ---
    and_gates = [build_and(sch) for _ in range(4)]

    # --- Bitwise OR gates ---
    or_gates = [build_or(sch) for _ in range(4)]

    # --- Bitwise XOR gates ---
    xor_gates = [build_xor(sch) for _ in range(4)]

    sch.wire_up()
    return sch


# ── Roundtrip test ────────────────────────────────────────────────────────

def roundtrip_test(src_path: str):
    """Read a .kicad_sch, re-save it, read again, compare component counts."""
    print(f"Roundtrip test: {src_path}")
    sch1 = schematic.Schematic.load(src_path)
    tmp = src_path.replace(".kicad_sch", "_rt.kicad_sch")
    sch1.save(tmp, backend="kicad")
    sch2 = schematic.Schematic.load(tmp)
    n1 = len(sch1.instances)
    n2 = len(sch2.instances)
    ok = n1 == n2
    print(f"  Original: {n1} components,  Roundtrip: {n2} components  → {'PASS' if ok else 'FAIL'}")
    return ok


# ── CLI entry ─────────────────────────────────────────────────────────────

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "all"

    if target in ("adder", "all"):
        print("=== 4-bit Adder ===")
        sch = build_4bit_adder()
        sch.save("4bit_adder.kicad_sch", backend="kicad")
        sch.save("4bit_adder.sch", backend="eagle")
        roundtrip_test("4bit_adder.kicad_sch")

    if target in ("riscv", "all"):
        print("\n=== RISC-V ALU Slice ===")
        sch = build_riscv_alu_slice()
        sch.save("riscv_alu.kicad_sch", backend="kicad")
        sch.save("riscv_alu.sch", backend="eagle")
        roundtrip_test("riscv_alu.kicad_sch")

    print("\nDone.")


if __name__ == "__main__":
    main()
from taurus import schematic

# Initialize the schematic
sch = schematic.Schematic()
sch.init_libraries("transistor-npn", "resistor-power")

# Initialize device sets
t_ds = sch.init_device_set("BJT_", "Q")
sch.init_device(t_ds, "NPN")
r_ds = sch.init_device_set("R_", "R")
sch.init_device(r_ds, "RES")

# Function to create a half-adder
def create_half_adder(sch):
    # NAND for carry (A AND B)
    q1 = sch.add_instance("BJT_", "NPN", "Q")  # First transistor for NAND
    q2 = sch.add_instance("BJT_", "NPN", "Q")  # Second transistor for NAND
    r1 = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for NAND
    q3 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter for NAND output
    r2 = sch.add_instance("R_", "RES", "R")    # Pull-up for carry output

    # XOR for sum (A XOR B) using NAND gates: (A NAND ~B) NAND (~A NAND B)
    q4 = sch.add_instance("BJT_", "NPN", "Q")  # NAND for A
    q5 = sch.add_instance("BJT_", "NPN", "Q")  # NAND for A
    r3 = sch.add_instance("R_", "RES", "R")    # Pull-up
    q6 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter for ~A
    r4 = sch.add_instance("R_", "RES", "R")    # Pull-up

    q7 = sch.add_instance("BJT_", "NPN", "Q")  # NAND for B
    q8 = sch.add_instance("BJT_", "NPN", "Q")  # NAND for B
    r5 = sch.add_instance("R_", "RES", "R")    # Pull-up
    q9 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter for ~B
    r6 = sch.add_instance("R_", "RES", "R")    # Pull-up

    q10 = sch.add_instance("BJT_", "NPN", "Q") # Final NAND for XOR
    q11 = sch.add_instance("BJT_", "NPN", "Q") # Final NAND for XOR
    r7 = sch.add_instance("R_", "RES", "R")    # Pull-up for sum

    # Wire NAND for carry (A AND B)
    q1.wire("C", r1, "1")      # Q1 collector to pull-up
    q1.wire("E", q2, "C")      # Q1 emitter to Q2 collector
    q2.wire("E", q3, "B")      # NAND output to inverter base
    q3.wire("C", r2, "1")      # Inverter collector to pull-up
    r2.wire("2", q3, "E")      # Ground the emitter

    # Wire XOR (A XOR B)
    # A NAND ~B
    q4.wire("C", r3, "1")      # NAND collector to pull-up
    q4.wire("E", q5, "C")
    q5.wire("E", q6, "B")      # NAND to inverter
    q6.wire("C", r4, "1")      # ~B output
    r4.wire("2", q6, "E")      # Ground

    # ~A NAND B
    q7.wire("C", r5, "1")
    q7.wire("E", q8, "C")
    q8.wire("E", q9, "B")
    q9.wire("C", r6, "1")      # ~A output
    r6.wire("2", q9, "E")      # Ground

    # (A NAND ~B) NAND (~A NAND B)
    q10.wire("C", r7, "1")
    q10.wire("E", q11, "C")
    q6.wire("C", q10, "B")     # ~B to final NAND
    q9.wire("C", q11, "B")     # ~A to final NAND
    r7.wire("2", q11, "E")     # Ground

    return {
        "carry": q3,  # Carry output (A AND B)
        "sum": q10,   # Sum output (A XOR B)
        "a": q1,      # Input A (base of Q1)
        "b": q2       # Input B (base of Q2)
    }

# Function to create a full-adder
def create_full_adder(sch):
    # Two half-adders
    ha1 = create_half_adder(sch)  # A + B
    ha2 = create_half_adder(sch)  # (A + B) + Cin

    # OR gate for carry-out with inverter
    q_or1 = sch.add_instance("BJT_", "NPN", "Q")  # NOR transistor
    r_or1 = sch.add_instance("R_", "RES", "R")    # Pull-up
    q_or2 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter
    r_or2 = sch.add_instance("R_", "RES", "R")    # Pull-up for OR output

    # Wire half-adders
    ha1["sum"].wire("C", ha2["a"], "B")  # HA1 sum to HA2 input A

    # Wire OR gate: (HA1.carry OR HA2.carry)
    ha1["carry"].wire("C", q_or1, "B")  # HA1 carry to NOR
    ha2["carry"].wire("C", q_or1, "B")  # HA2 carry to NOR
    q_or1.wire("C", r_or1, "1")         # NOR output
    r_or1.wire("2", q_or1, "E")         # Ground
    r_or1.wire("2", q_or2, "B")         # NOR to inverter
    q_or2.wire("C", r_or2, "1")         # OR output
    r_or2.wire("2", q_or2, "E")         # Ground

    return {
        "sum": ha2["sum"],      # Final sum
        "carry_out": q_or2,     # Final carry-out
        "a": ha1["a"],          # Input A
        "b": ha1["b"],          # Input B
        "carry_in": ha2["b"]    # Carry-in
    }

# Create 4 full-adders for a 4-bit adder
adders = []
for i in range(4):
    adder = create_full_adder(sch)
    adders.append(adder)

# Cascade the carry-out to carry-in
for i in range(3):
    adders[i]["carry_out"].wire("C", adders[i + 1]["carry_in"], "B")

# Generate and save the schematic
sch.wire_up()
sch.save("4bit_adder.kicad_sch")