import xml.etree.ElementTree as ET
import os
import re
import math
from collections import defaultdict
from pathlib import Path
from .eaglepy.eagle import (
    attributes, Eagle, Drawing, Grid, Schematic as EagleSchematic, Sheet, Net, 
    Part as EaglePart, Library, Device_Set, Device, Connect, Symbol as EagleSymbol, 
    Segment, Instance as EagleInstance, Gate
)
from .eaglepy import default_layers
from .eaglepy.primitives import Wire, Text, Pin, Rectangle, Circle, Pin_Ref as PinRef

class Instance:
    def __init__(self, eagle_instance, schematic, part_name, device_set, prefix):
        self.eagle_instance = eagle_instance
        self.schematic = schematic
        self.connections = {}  # {"pin_name": (target_instance, target_pin)}
        self.device_set = device_set
        self.part = part_name
        self.prefix = prefix

    def wire(self, pin_name, target_instance, target_pin):
        self.connections[pin_name] = (target_instance, target_pin)

    def __getitem__(self, pin_name):
        return PinRef(part=self.eagle_instance.part.name, gate="G$1", pin=pin_name)

def get_pins_from_symbol(symbol):
    pins = []
    for item in symbol.items:
        if isinstance(item, Pin):
            pins.append({
                'name': item.name,
                'x': item.x,
                'y': item.y,
                'direction': item.direction,
                'visible': item.visible,
                'length': item.length
            })
    return pins

def compute_absolute_position(instance_x, instance_y, rotation, pin_x, pin_y):
    rotation_str = str(rotation)
    if rotation_str == 'R0':
        return (instance_x + pin_x, instance_y + pin_y)
    elif rotation_str == 'R90':
        return (instance_x + pin_y, instance_y - pin_x)
    elif rotation_str == 'R180':
        return (instance_x - pin_x, instance_y - pin_y)
    elif rotation_str == 'R270':
        return (instance_x - pin_y, instance_y + pin_x)
    else:
        return (instance_x + pin_x, instance_y + pin_y)

def organize_components(instances, x_spacing=20, y_spacing=20, padding=5):
    resistors = [i for i in instances if i.part.name.startswith("R")]
    transistors = [i for i in instances if i.part.name.startswith("Q")]
    
    x, y = padding, padding
    for res in resistors:
        res.x, res.y = x, y
        x += x_spacing * 2
    
    x, y = padding, y + y_spacing * 2
    for trans in transistors:
        trans.x, trans.y = x, y
        x += x_spacing * 2
    
    all_x = [i.x for i in instances]
    all_y = [i.y for i in instances]
    return {
        "width": max(all_x) - min(all_x) + padding*2,
        "height": max(all_y) - min(all_y) + padding*2,
        "x": min(all_x) - padding,
        "y": min(all_y) - padding
    }

def compute_symbol_bbox(symbol):
    min_x, min_y, max_x, max_y = float('inf'), float('inf'), -float('inf'), -float('inf')
    for item in symbol.items:
        if isinstance(item, Wire) or isinstance(item, Rectangle):
            min_x = min(min_x, item.x1, item.x2)
            min_y = min(min_y, item.y1, item.y2)
            max_x = max(max_x, item.x1, item.x2)
            max_y = max(max_y, item.y1, item.y2)
        elif isinstance(item, Circle):
            min_x = min(min_x, item.x - item.radius)
            min_y = min(min_y, item.y - item.radius)
            max_x = max(max_x, item.x + item.radius)
            max_y = max(max_y, item.y + item.radius)
    return (min_x, min_y, max_x, max_y)

def compute_instance_bbox(instance):
    symbol = instance.gate.symbol
    sym_min_x, sym_min_y, sym_max_x, sym_max_y = compute_symbol_bbox(symbol)
    rotation = math.radians(instance.rotation.angle)
    cos_rot, sin_rot = math.cos(rotation), math.sin(rotation)
    corners = [
        (sym_min_x, sym_min_y), (sym_min_x, sym_max_y),
        (sym_max_x, sym_max_y), (sym_max_x, sym_min_y)
    ]
    rotated_corners = [
        (cx * cos_rot - cy * sin_rot, cx * sin_rot + cy * cos_rot)
        for cx, cy in corners
    ]
    translated_corners = [(rx + instance.x, ry + instance.y) for rx, ry in rotated_corners]
    min_x = min(c[0] for c in translated_corners)
    min_y = min(c[1] for c in translated_corners)
    max_x = max(c[0] for c in translated_corners)
    max_y = max(c[1] for c in translated_corners)
    return (min_x, min_y, max_x, max_y)

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        if self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x, y):
        self.parent[self.find(x)] = self.find(y)

class Schematic:
    def __init__(self):
        self.grid = Grid(distance=0.1, unit_dist="inch", unit="inch", style="lines", multiple=1, display=False)
        self.layers = default_layers.get_layers()
        self.sheet = Sheet()
        self.eagle_schematic = EagleSchematic(sheets=[self.sheet])
        self.drawing = Drawing(grid=self.grid, layers=self.layers, document=self.eagle_schematic)
        self.libraries = {}
        self.device_sets = {}
        self.devices = {}
        self.parts = {}
        self.instances = []
        self.part_counters = {}

    def _parse_rc_file(self):
        rc_path = os.path.expanduser("~/Library/Application Support/Eagle/lbr/libraries.rc")
        library_paths = {}
        with open(rc_path, 'r') as f:
            for line in f:
                m = re.match(r'Lbr\.Managed\.(\d+)\.path\s*=\s*"(.+)"', line)
                if m:
                    library_paths[m.group(1)] = m.group(2)
        return library_paths

    def _find_library_path(self, name):
        lib_paths = self._parse_rc_file().values()
        for path in lib_paths:
            if name.lower() in path.lower():
                return path
        raise ValueError(f"Library {name} not found")

    def _parse_symbol(self, lbr_path, symbol_name):
        tree = ET.parse(lbr_path)
        root = tree.getroot()
        for symbol_elem in root.findall(".//symbol"):
            if symbol_elem.attrib.get("name") == symbol_name:
                items = []
                for wire_elem in symbol_elem.findall(".//wire"):
                    items.append(Wire(
                        x1=float(wire_elem.attrib["x1"]),
                        y1=float(wire_elem.attrib["y1"]),
                        x2=float(wire_elem.attrib["x2"]),
                        y2=float(wire_elem.attrib["y2"]),
                        width=float(wire_elem.attrib["width"]),
                        layer=int(wire_elem.attrib["layer"])
                    ))
                for pin_elem in symbol_elem.findall(".//pin"):
                    items.append(Pin(
                        name=pin_elem.attrib["name"],
                        x=float(pin_elem.attrib["x"]),
                        y=float(pin_elem.attrib["y"]),
                        visible=pin_elem.attrib.get("visible", "off"),
                        length=pin_elem.attrib.get("length", "short"),
                        direction=pin_elem.attrib.get("direction", "pas"),
                        rotation=attributes.ATTR_ROT.parse(pin_elem.attrib.get("rot", "R0"))
                    ))
                symbol = EagleSymbol(name=symbol_name, items=items)
                symbol.bounding_box = compute_symbol_bbox(symbol)
                return symbol

    def init_libraries(self, *names):
        for name in names:
            path = self._find_library_path(name)
            lib = Library(name=name)
            self.libraries[name] = lib
            self.eagle_schematic.libraries.append(lib)

    def init_device_set(self, name, prefix):
        if prefix == 'Q':
            lib_name, symbol_name = 'transistor-npn', 'NPN'
        elif prefix == 'R':
            lib_name, symbol_name = 'resistor-power', 'R'
        else:
            raise ValueError(f"Unsupported prefix {prefix}")
        lib = self.libraries.get(lib_name)
        if not lib:
            raise ValueError(f"Library {lib_name} not initialized")
        lbr_path = self._find_library_path(lib_name)
        symbol = self._parse_symbol(lbr_path, symbol_name)
        ds = Device_Set(name=name, prefix=prefix, gates=[Gate(name="G$1", symbol=symbol, x=0, y=0)])
        lib.device_sets.append(ds)
        lib.symbols.append(symbol)
        self.device_sets[name] = ds
        return ds

    def init_device(self, ds, name, package=None):
        dev = Device(name=name, package=package)
        ds.devices.append(dev)
        self.devices[name] = dev

    def add_instance(self, device_set_name, part_name, prefix):
        if prefix not in self.part_counters:
            self.part_counters[prefix] = 0
        self.part_counters[prefix] += 1
        full_part_name = f"{prefix}{self.part_counters[prefix]}"
        ds = self.device_sets[device_set_name]
        lib = next(lib for lib in self.libraries.values() if ds in lib.device_sets)
        part = EaglePart(name=full_part_name, library=lib, device_set=ds, device=ds.devices[part_name])
        self.parts[full_part_name] = part
        self.eagle_schematic.parts.append(part)
        eagle_instance = EagleInstance(
            part=part, x=0, y=0, gate=ds.gates[0], rotation=attributes.Rotation(0)
        )
        instance = Instance(eagle_instance, self, device_set_name, part_name, prefix)
        self.instances.append(instance)
        self.sheet.instances.append(eagle_instance)
        self._organize_components()
        return instance

    def _organize_components(self):
        bounds = organize_components([i.eagle_instance for i in self.instances])
        self.sheet.width = bounds['width']
        self.sheet.height = bounds['height']
        self.sheet.x = bounds['x']
        self.sheet.y = bounds['y']

    def wire_up(self):
        # Compute pin positions
        pin_positions = {}
        for instance in self.instances:
            inst = instance.eagle_instance
            for pin in get_pins_from_symbol(inst.gate.symbol):
                abs_pos = compute_absolute_position(inst.x, inst.y, inst.rotation, pin['x'], pin['y'])
                pin_positions[(inst.part.name, pin['name'])] = abs_pos

        # Collect all direct connections and group into nets
        uf = UnionFind()
        connections = []
        for instance in self.instances:
            for pin, (target_instance, target_pin) in instance.connections.items():
                start = (instance.eagle_instance.part.name, pin)
                end = (target_instance.eagle_instance.part.name, target_pin)
                start_pos = pin_positions[start]
                end_pos = pin_positions[end]
                uf.union(start, end)
                connections.append({
                    'start_part': start[0], 'start_pin': start[1],
                    'end_part': end[0], 'end_pin': end[1],
                    'start_pos': start_pos, 'end_pos': end_pos
                })

        nets = defaultdict(list)
        for conn in connections:
            root = uf.find((conn['start_part'], conn['start_pin']))
            nets[root].append(conn)

        # Assign tracks to horizontal connections
        horizontal_conns = [
            conn for conn in connections
            if conn['start_pos'][0] != conn['end_pos'][0]
        ]
        horizontal_conns.sort(key=lambda c: min(c['start_pos'][0], c['end_pos'][0]))
        tracks = []
        for conn in horizontal_conns:
            x_range = (min(conn['start_pos'][0], conn['end_pos'][0]), 
                      max(conn['start_pos'][0], conn['end_pos'][0]))
            assigned = False
            for i, track in enumerate(tracks):
                if not any(x_range[0] < t[1] and x_range[1] > t[0] for t in track):
                    track.append(x_range)
                    conn['track'] = i
                    assigned = True
                    break
            if not assigned:
                tracks.append([x_range])
                conn['track'] = len(tracks) - 1

        # Define track positions
        track_spacing = 2
        max_y = max(inst.eagle_instance.y for inst in self.instances) if self.instances else 0
        y_base = max_y + 5

        # Route wires for each net
        existing_net_names = set()
        for net_root, net_conns in nets.items():
            # Generate unique net name
            parts = {p for p, _ in list(uf.parent.items()) if uf.find((p, None)) == net_root}
            
            counts = defaultdict(int)
            for p in parts:
                counts[p[0]] += 1
            name_parts = [f"{t}{counts[t]}" for t in sorted(counts.keys())]
            base_name = "net_" + "_".join(name_parts)
            net_name = base_name
            suffix = 1
            while net_name in existing_net_names:
                net_name = f"{base_name}_{suffix}"
                suffix += 1
            existing_net_names.add(net_name)

            # Route wires
            wires = []
            pin_refs = set()
            for conn in net_conns:
                sx, sy = conn['start_pos']
                ex, ey = conn['end_pos']
                pin_refs.add(PinRef(part=conn['start_part'], gate="G$1", pin=conn['start_pin']))
                pin_refs.add(PinRef(part=conn['end_part'], gate="G$1", pin=conn['end_pin']))
                if sx == ex:
                    wires.append(Wire(x1=sx, y1=sy, x2=ex, y2=ey, width=0.2))
                else:
                    track = conn.get('track')
                    if track is not None:
                        y_track = y_base + track * track_spacing
                        wires.extend([
                            Wire(x1=sx, y1=sy, x2=sx, y2=y_track, width=0.2),
                            Wire(x1=sx, y1=y_track, x2=ex, y2=y_track, width=0.2),
                            Wire(x1=ex, y1=y_track, x2=ex, y2=ey, width=0.2)
                        ])

            segment = Segment(items=list(pin_refs) + wires)
            net = Net(name=net_name, net_class=0)
            net.segments.append(segment)
            self.eagle_schematic.sheets[0].nets.append(net)

    def save(self, filename):
        eagle = Eagle(drawing=self.drawing)
        eagle.save(Path(filename))
        print(f"Schematic saved to {filename}")

class Descriptor:
    def __init__(self, identifier, device_set, part, prefix):
        self.identifier = identifier
        self.device_set = device_set
        self.part = part
        self.prefix = prefix

class Symbol:
    def __init__(self, name, parts=None, descriptors=None, connections=None):
        self.name = name
        self.parts = parts or []
        self.descriptors = descriptors or []
        self.descriptor_counters = {}
        self.connections = connections or {}

    def add_descriptor(self, device_set, part, prefix):
        self.descriptor_counters[prefix] = self.descriptor_counters.get(prefix, 0) + 1
        descriptor = Descriptor(self.descriptor_counters[prefix], device_set, part, prefix)
        self.descriptors.append(descriptor)
        return descriptor

    def add_connection(self, pin_name, source_instance, target):
        self.connections[pin_name] = (source_instance, target)

    def to_xml(self):
        symbol_elem = ET.Element("symbol", {"name": self.name})
        instances_elem = ET.SubElement(symbol_elem, "instances")
        for d in self.descriptors:
            ET.SubElement(instances_elem, "instance", {
                "identifier": str(d.identifier),
                "device_set": d.device_set,
                "part": d.part,
                "prefix": d.prefix
            })
        connections_elem = ET.SubElement(symbol_elem, "connections")
        for pin_name, (src, (tgt, tgt_pin)) in self.connections.items():
            src_str = f"{src.identifier}:{src.device_set}:{src.part}:{src.prefix}:{pin_name}"
            tgt_str = f"{tgt.identifier}:{tgt.device_set}:{tgt.part}:{tgt.prefix}:{tgt_pin}"
            ET.SubElement(connections_elem, "connection", {
                "source_instance": src_str,
                "target_instance": tgt_str
            })
        return ET.tostring(symbol_elem, encoding='unicode')

    def save(self, filename):
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(self.to_xml())
        print(f"Symbol saved to {filename}")

    @staticmethod
    def load(filename, schematic):
        with open(filename, 'r', encoding='utf-8') as f:
            return Symbol.from_xml(f.read(), schematic)

    @staticmethod
    def from_xml(xml_string, schematic):
        root = ET.fromstring(xml_string)
        symbol = Symbol(root.attrib["name"])
        for inst in root.find("instances").findall("instance"):
            symbol.descriptors.append(Descriptor(
                int(inst.attrib["identifier"]),
                inst.attrib["device_set"],
                inst.attrib["part"],
                inst.attrib["prefix"]
            ))
        for conn in root.find("connections").findall("connection"):
            src_parts = conn.attrib["source_instance"].split(":")
            tgt_parts = conn.attrib["target_instance"].split(":")
            src = next(d for d in symbol.descriptors if d.identifier == int(src_parts[0]))
            tgt = next(d for d in symbol.descriptors if d.identifier == int(tgt_parts[0]))
            symbol.add_connection(src_parts[4], src, (tgt, tgt_parts[4]))
        return symbol