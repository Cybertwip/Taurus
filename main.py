#!/usr/bin/env python3
"""
Taurus main entry point.

Usage:
    python3 main.py                  # Launch the logic gate simulator
    python3 main.py sim              # Same – launch the simulator
    python3 main.py trace [target]   # Run the tracer (target: adder | riscv | all)
    python3 main.py export <json> <out> [backend]
                                     # Export project.json → KiCad (.kicad_sch) or Eagle (.sch)
    python3 main.py roundtrip <file> # Roundtrip test on a .kicad_sch file
"""
from __future__ import annotations

import sys
from pathlib import Path


def cmd_sim():
    """Launch the pygame logic gate simulator."""
    from taurus.simulator import main as sim_main
    sim_main()


def cmd_trace(args: list[str]):
    """Run the tracer to generate schematics."""
    from tracer import main as tracer_main
    # Forward target argument
    sys.argv = ["tracer"] + args
    tracer_main()


def cmd_export(args: list[str]):
    """Export a project.json → schematic file."""
    if len(args) < 2:
        print("Usage: main.py export <project.json> <output> [kicad|eagle]")
        sys.exit(1)
    json_path = args[0]
    out_path = args[1]
    backend = args[2] if len(args) > 2 else "kicad"

    import json
    from taurus import schematic

    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    sch = schematic.Schematic()
    sch.init_libraries("transistor-npn", "resistor-power")

    # Map simulator gate types to transistor-level building blocks
    from tracer import build_and, build_or, build_xor, build_nand, build_not, _new_sch

    print(f"Project has {len(data.get('gates', []))} gates, "
          f"{len(data.get('inputs', []))} inputs, "
          f"{len(data.get('outputs', []))} outputs.")
    print(f"Exporting to {out_path} (backend={backend})")

    # For direct export, create a simple schematic with component labels
    sch = _new_sch()
    gate_map = {
        "AND": build_and,
        "OR": build_or,
        "XOR": build_xor,
        "NAND": build_nand,
        "NOT": build_not,
    }

    built = {}
    for gate in data.get("gates", []):
        gtype = gate.get("gate_type", "AND")
        gid = gate.get("id")
        builder = gate_map.get(gtype)
        if builder:
            built[gid] = builder(sch)

    sch.wire_up()
    sch.save(out_path, backend=backend)


def cmd_roundtrip(args: list[str]):
    """Run roundtrip test on a .kicad_sch file."""
    if not args:
        print("Usage: main.py roundtrip <file.kicad_sch>")
        sys.exit(1)
    from tracer import roundtrip_test
    ok = roundtrip_test(args[0])
    sys.exit(0 if ok else 1)


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "sim"

    if cmd in ("sim", "simulator"):
        cmd_sim()
    elif cmd in ("trace", "tracer"):
        cmd_trace(args[1:])
    elif cmd == "export":
        cmd_export(args[1:])
    elif cmd == "roundtrip":
        cmd_roundtrip(args[1:])
    elif cmd in ("-h", "--help", "help"):
        print(__doc__)
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
