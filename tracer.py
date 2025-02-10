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
    # NAND stage (A AND B)
    q1 = sch.add_instance("BJT_", "NPN", "Q")  # First transistor for NAND
    q2 = sch.add_instance("BJT_", "NPN", "Q")  # Second transistor for NAND
    r1 = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for NAND
    q3 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter for NAND output
    r2 = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for inverter

    # XOR stage (A XOR B)
    q4 = sch.add_instance("BJT_", "NPN", "Q")  # First transistor for NOR
    q5 = sch.add_instance("BJT_", "NPN", "Q")  # Second transistor for NOR
    r3 = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for NOR
    q6 = sch.add_instance("BJT_", "NPN", "Q")  # Inverter for NOR output
    r4 = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for inverter

    # Wire NAND stage
    q1.wire("C", r1, "1")      # Q1 collector to R1
    q1.wire("E", q2, "C")      # Q1 emitter to Q2 collector
    q2.wire("E", q3, "B")      # NAND output to Q3 base
    q3.wire("C", r2, "1")      # Q3 collector to R2 (pull-up)

    # Wire NOR stage
    q4.wire("C", r3, "1")      # Both collectors to R3
    q5.wire("C", r3, "1")
    r3.wire("2", q6, "B")      # NOR output to Q6 base
    q6.wire("C", r4, "1")      # Q6 collector to R4 (pull-up)

    return {
        "carry": q3,  # Carry output
        "sum": q6     # Sum output
    }

# Function to create a full-adder
def create_full_adder(sch):
    # Create two half-adders
    ha1 = create_half_adder(sch)  # First half-adder (A + B)
    ha2 = create_half_adder(sch)  # Second half-adder (Sum + Carry_in)

    # OR gate for carry-out
    q_or = sch.add_instance("BJT_", "NPN", "Q")  # OR gate transistor
    r_or = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for OR gate

    # Input for carry-in
    q_cin = sch.add_instance("BJT_", "NPN", "Q")  # Transistor for carry-in
    r_cin = sch.add_instance("R_", "RES", "R")    # Pull-up resistor for carry-in

    # Wire OR gate
    ha1["carry"].wire("C", q_or, "B")  # Carry from HA1 to OR base
    ha2["carry"].wire("C", q_or, "B")  # Carry from HA2 to OR base
    q_or.wire("C", r_or, "1")          # OR collector to pull-up resistor

    # Wire carry-in
    q_cin.wire("C", ha2["carry"], "B")  # Carry-in to second half-adder's carry input
    r_cin.wire("1", q_cin, "B")         # Pull-up for carry-in

    return {
        "sum": ha2["sum"],              # Final sum output
        "carry_out": q_or,              # Final carry-out
        "carry_in": q_cin               # Carry-in input
    }

# Create 8 full-adders for an 8-bit adder
adders = []
for i in range(4):
    adder = create_full_adder(sch)
    adders.append(adder)

# Cascade the carry-out of one adder to the carry-in of the next
for i in range(3):
    adders[i]["carry_out"].wire("E", adders[i + 1]["carry_in"], "B")

# Save the schematic
sch.wire_up()
sch.save("4bit_adder.sch")