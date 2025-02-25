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
sch.save("4bit_adder.sch")