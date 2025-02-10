import json
import uuid
from typing import Dict, List, Optional

class Port:
    def __init__(self, gate, port_type: str = "input", uuid_str: Optional[str] = None, x: int = 0, y: int = 0, value: int = 0):
        self.uuid = uuid_str if uuid_str else str(uuid.uuid4())
        self.gate = gate
        self.type = port_type
        self.value = value
        self.connected_to: List['Port'] = []
        self.connected_from: Optional['Port'] = None
        self.x = x
        self.y = y

    def connect(self, target: 'Port') -> bool:
        if self.type == target.type:
            return False
            
        if self.type == "input":
            if self.connected_from:
                self.connected_from.connected_to.remove(self)
            self.connected_from = target
            target.connected_to.append(self)
        else:
            if target.connected_from:
                target.connected_from.connected_to.remove(target)
            self.connected_to.append(target)
            target.connected_from = self
        return True

    @classmethod
    def deserialize(cls, data: dict, gate) -> 'Port':
        port = cls(
            gate=gate,
            port_type=data["type"],
            uuid_str=data["uuid"],
            x=data.get("x", 0),
            y=data.get("y", 0)
        )
        port.value = data.get("value", 0)
        port.connected_to = data.get("connected_to", [])
        port.connected_from = data.get("connected_from")
        return port

    def serialize(self) -> dict:
        return {
            "uuid": self.uuid,
            "type": self.type,
            "value": self.value,
            "connected_to": [p.uuid for p in self.connected_to],
            "connected_from": self.connected_from.uuid if self.connected_from else None,
            "x": self.x,
            "y": self.y
        }

class Gate:
    GATE_LOGIC = {
        "AND": lambda a, b: a & b,
        "OR": lambda a, b: a | b,
        "XOR": lambda a, b: a ^ b,
        "NOT": lambda a: 0 if a else 1,
        "NAND": lambda a, b: 0 if (a & b) else 1,
        "NOR": lambda a, b: 0 if (a | b) else 1,
        "XNOR": lambda a, b: 0 if (a ^ b) else 1
    }

    def __init__(self, x, y, type):
        self.uuid = str(uuid.uuid4())
        self.x = x
        self.y = y
        self.type = type
        self.width = 100
        self.height = 50
        self.output = Port(self, "output", x=self.x + self.width, y=self.y + (self.height / 2))
        self.inputs = []
        
        if type != "NOT":
            self.inputs = [
                Port(self, "input", x=self.x, y=self.y + (self.height / 4)),
                Port(self, "input", x=self.x, y=self.y + (3 * self.height / 4))
            ]
        else:
            self.inputs = [Port(self, "input", x=self.x, y=self.y + (self.height / 2))]

    def calculate(self):
        if self.type == "NOT":
            a = self.inputs[0].value
            self.output.value = self.GATE_LOGIC[self.type](a)
        else:
            a = self.inputs[0].value if len(self.inputs) > 0 else 0
            b = self.inputs[1].value if len(self.inputs) > 1 else 0
            self.output.value = self.GATE_LOGIC[self.type](a, b)

    @classmethod
    def deserialize(cls, data: dict) -> 'Gate':
        gate = cls(
            x=data["x"],
            y=data["y"],
            type=data["type"]
        )
        gate.output = Port.deserialize(data["output"], gate)
        gate.inputs = [Port.deserialize(p_data, gate) for p_data in data["input"]]
        return gate

    def serialize(self) -> dict:
        return {
            "uuid": self.uuid,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "input": [p.serialize() for p in self.inputs],
            "output": self.output.serialize()
        }

class Input:
    def __init__(self, x, y, type):
        self.uuid = str(uuid.uuid4())
        self.x = x
        self.y = y
        self.type = type
        self.port = Port(self, "output", x=self.x + 20 + 30, y=self.y)
        self.output = self.port

    def set_value(self, value: int):
        self.port.value = value
        for conn in self.port.connected_to:
            conn.value = value

    @classmethod
    def deserialize(cls, data: dict) -> 'Input':
        inp = cls(
            x=data["x"],
            y=data["y"],
            type=data["type"]
        )
        inp.port = Port.deserialize(data["port"], inp)
        return inp

    def serialize(self) -> dict:
        return {
            "uuid": self.uuid,
            "type": self.type,
            "x": self.x,
            "y": self.y,
            "port": self.port.serialize()
        }

class Output:
    def __init__(self, x, y):
        self.uuid = str(uuid.uuid4())
        self.x = x
        self.y = y
        self.port = Port(self, "input", x=self.x - 20 - 30, y=self.y)
        self.input = self.port

    def get_value(self) -> int:
        return self.port.value

    @classmethod
    def deserialize(cls, data: dict) -> 'Output':
        out = cls(
            x=data["x"],
            y=data["y"]
        )
        out.port = Port.deserialize(data["port"], out)
        return out

    def serialize(self) -> dict:
        return {
            "uuid": self.uuid,
            "x": self.x,
            "y": self.y,
            "port": self.port.serialize()
        }

class ALU:
    def __init__(self):
        self.inputs: List[Input] = []
        self.outputs: List[Output] = []
        self.gates: List[Gate] = []
        self.port_map: Dict[str, Port] = {}

    def add_component(self, component):
        if isinstance(component, Input):
            self.inputs.append(component)
            self._register_port(component.port)
        elif isinstance(component, Output):
            self.outputs.append(component)
            self._register_port(component.port)
        elif isinstance(component, Gate):
            self.gates.append(component)
            self._register_port(component.output)
            for port in component.inputs:
                self._register_port(port)

    def _register_port(self, port: Port):
        self.port_map[port.uuid] = port

    def calculate(self):
        visited = set()
        input_ports = [inp.port for inp in self.inputs]
        
        def propagate(port: Port):
            if port in visited:
                return
            visited.add(port)
            
            if port.type == "output":
                port.gate.calculate()
                for conn in port.connected_to:
                    conn.value = port.value
                    propagate(conn)
            else:
                if port.connected_from:
                    port.value = port.connected_from.value
                    propagate(port.connected_from)

        for port in input_ports:
            for conn in port.connected_to:
                conn.value = port.value
                propagate(conn)

        for gate in self.gates:
            gate.calculate()

    @classmethod
    def load_from_json(cls, file_path: str) -> 'ALU':
        alu = cls()
        with open(file_path, 'r') as f:
            data = json.load(f)

        for input_data in data["inputs"]:
            inp = Input.deserialize(input_data)
            alu.add_component(inp)

        for output_data in data["outputs"]:
            out = Output.deserialize(output_data)
            alu.add_component(out)

        for gate_data in data["gates"]:
            gate = Gate.deserialize(gate_data)
            alu.add_component(gate)

        for port in alu.port_map.values():
            if port.connected_from:
                port.connected_from = alu.port_map.get(port.connected_from)
            port.connected_to = [alu.port_map[uuid] for uuid in port.connected_to]

        return alu

    def save_to_json(self, file_path: str):
        data = {
            "inputs": [inp.serialize() for inp in self.inputs],
            "outputs": [out.serialize() for out in self.outputs],
            "gates": [gate.serialize() for gate in self.gates],
            "zoom_level": 1.0,
            "pan_offset": [0, 0]
        }
        
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)

    def generate_python_code(self, output_file: str = "generated_circuit.py"):
        code = [
            "from ALU.ALU import ALU, Input, Output, Gate, Port\n\n",
            "def create_circuit():\n",
            "    alu = ALU()\n\n"
        ]

        component_map = {}

        counter = 0
        for inp in self.inputs:
            component_map[inp.uuid] = f"input_{counter}"
            counter += 1
            code.append(
                f"    {component_map[inp.uuid]} = Input({inp.x}, {inp.y}, '{inp.type}')\n"
                f"    {component_map[inp.uuid]}.port.uuid = '{inp.port.uuid}'\n"
                f"    {component_map[inp.uuid]}.port.value = '{inp.port.value}'\n"
                f"    alu.add_component({component_map[inp.uuid]})\n\n"
            )

        counter = 0
        for out in self.outputs:
            component_map[out.uuid] = f"output_{counter}"
            counter += 1
            code.append(
                f"    {component_map[out.uuid]} = Output({out.x}, {out.y})\n"
                f"    {component_map[out.uuid]}.port.uuid = '{out.port.uuid}'\n"
                f"    alu.add_component({component_map[out.uuid]})\n\n"
            )

        counter = 0
        port_counter = 0
        for gate in self.gates:
            component_map[gate.uuid] = f"gate_{counter}"
            counter += 1
            code.append(
                f"    {component_map[gate.uuid]} = Gate({gate.x}, {gate.y}, '{gate.type}')\n"
                f"    {component_map[gate.uuid]}.output.uuid = '{gate.output.uuid}'\n"
                f"    {component_map[gate.uuid]}.inputs = []\n"
            )
            for i, port in enumerate(gate.inputs):
                code.append(
                    f"    input_port_{port_counter} = Port({component_map[gate.uuid]}, 'input', "
                    f"uuid_str='{port.uuid}', x={port.x}, y={port.y})\n"
                    f"    {component_map[gate.uuid]}.inputs.append(input_port_{port_counter})\n"
                )
                port_counter += 1
            code.append(f"    alu.add_component({component_map[gate.uuid]})\n\n")


        code.append("    # Create connections\n")
        for port in self.port_map.values():
            if port.connected_from:
                code.append(
                    f"    alu.port_map['{port.uuid}'].connected_from = "
                    f"alu.port_map['{port.connected_from.uuid}']\n"
                )
            for target in port.connected_to:
                code.append(
                    f"    alu.port_map['{port.uuid}'].connected_to.append("
                    f"alu.port_map['{target.uuid}'])\n"
                )

        code.append("\n    return alu\n")
        code.append("\nif __name__ == '__main__':\n")
        code.append("    circuit = create_circuit()\n")

        with open(output_file, 'w') as f:
            f.writelines(code)

if __name__ == "__main__":
    # Example usage
    alu = ALU.load_from_json("project.json")
    
    # Generate executable Python code
    alu.generate_python_code()
    
    # Save modified state back to JSON
    alu.save_to_json("modified_project.json")