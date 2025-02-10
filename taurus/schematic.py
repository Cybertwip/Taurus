import xml.etree.ElementTree as ET
import os
import re
import math

from collections import defaultdict
from pathlib import Path
from .eaglepy.eagle import (
    attributes, Eagle, Drawing, Grid, Schematic as EagleSchematic, Sheet, Net, Part as EaglePart, Library,
    Device_Set, Device, Connect, Symbol as EagleSymbol, Segment, Instance as EagleInstance, Gate
)
from .eaglepy import default_layers
from .eaglepy.primitives import Wire, Text, Pin, Rectangle, Circle, Pin_Ref as PinRef

import xml.etree.ElementTree as ET

from math import sin, cos, radians


class Instance:
    def __init__(self, eagle_instance, schematic, part_name, device_set, prefix):
        self.eagle_instance = eagle_instance
        self.schematic = schematic
        self.connections = {}  # Store pin connections: {"pin_name": target_instance_or_pin}

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
            pin_info = {
                'name': item.name,
                'x': item.x,
                'y': item.y,
                'direction': item.direction,
                'visible': item.visible,
                'length': item.length
            }
            pins.append(pin_info)
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
        if isinstance(item, (Wire, Rectangle)): #wires affect the bounding box location Wire, Rectangle
            min_x = min(min_x, item.x1, item.x2)
            min_y = min(min_y, item.y1, item.y2)
            max_x = max(max_x, item.x1, item.x2)
            max_y = max(max_y, item.y1, item.y2)
        elif isinstance(item, Circle):
            min_x = min(min_x, item.x - item.radius)
            min_y = min(min_y, item.y - item.radius)
            max_x = max(max_x, item.x + item.radius)
            max_y = max(max_y, item.y + item.radius)
        # elif isinstance(item, Pin): # pins are not considered part of the bbox
        #     min_x = min(min_x, item.x)
        #     min_y = min(min_y, item.y)
        #     max_x = max(max_x, item.x)
        #     max_y = max(max_y, item.y)
    
    return (min_x, min_y, max_x, max_y)

def compute_instance_bbox(instance):
    symbol = instance.gate.symbol
    sym_min_x, sym_min_y, sym_max_x, sym_max_y = symbol.bounding_box

    corners = [
        (sym_min_x, sym_min_y),
        (sym_min_x, sym_max_y),
        (sym_max_x, sym_max_y),
        (sym_max_x, sym_min_y)
    ]

    sym_min_x, sym_min_y, sym_max_x, sym_max_y = symbol.bounding_box
    
    # Correct rotation direction by using negative radians for clockwise rotation
    rotation = radians(instance.rotation.angle)
    cos_rot = cos(rotation)
    sin_rot = sin(rotation)

    rotated_corners = []
    for cx, cy in corners:
        rx = cx * cos_rot - cy * sin_rot
        ry = cx * sin_rot + cy * cos_rot
        rotated_corners.append((rx, ry))

    translated_corners = [(rx + instance.x, ry + instance.y) for rx, ry in rotated_corners]

    min_x = min(c[0] for c in translated_corners)
    min_y = min(c[1] for c in translated_corners)
    max_x = max(c[0] for c in translated_corners)
    max_y = max(c[1] for c in translated_corners)

    return (min_x, min_y, max_x, max_y)


def compute_instance_bbox_with_pins(instance_eagle):
    # Compute the normal bbox (without pins)
    normal_bbox = compute_instance_bbox(instance_eagle)
    min_x, min_y, max_x, max_y = normal_bbox

    # Get the symbol's pins
    symbol = instance_eagle.gate.symbol
    pins = get_pins_from_symbol(symbol)

    # Compute each pin's absolute position and adjust the bbox
    for pin in pins:
        abs_x, abs_y = compute_absolute_position(instance_eagle.x, instance_eagle.y, instance_eagle.rotation, pin['x'], pin['y'])
        min_x = min(min_x, abs_x)
        min_y = min(min_y, abs_y)
        max_x = max(max_x, abs_x)
        max_y = max(max_y, abs_y)

    return (min_x, min_y, max_x, max_y)

def calculate_wire_path(start_x, start_y, end_x, end_y, instances_bbox, manhattan_offset):
    def segment_intersects_bbox(x1, y1, x2, y2, bbox):
        """
        Check if a line segment intersects with a bounding box.
        :param x1, y1: Start point of the segment.
        :param x2, y2: End point of the segment.
        :param bbox: Bounding box as (min_x, min_y, max_x, max_y).
        :return: True if the segment intersects the bbox, False otherwise.
        """
        min_x, min_y, max_x, max_y = bbox
        
        # Check if either endpoint is inside the bbox
        if (min_x <= x1 <= max_x and min_y <= y1 <= max_y) or \
           (min_x <= x2 <= max_x and min_y <= y2 <= max_y):
            return True
        
        # Check if the segment crosses any of the bbox edges
        def ccw(ax, ay, bx, by, cx, cy):
            return (by - ay) * (cx - ax) > (cy - ay) * (bx - ax)
        
        def intersect(p1, p2, p3, p4):
            return ccw(p1[0], p1[1], p3[0], p3[1], p4[0], p4[1]) != ccw(p2[0], p2[1], p3[0], p3[1], p4[0], p4[1]) and \
                   ccw(p1[0], p1[1], p2[0], p2[1], p3[0], p3[1]) != ccw(p1[0], p1[1], p2[0], p2[1], p4[0], p4[1])
        
        bbox_corners = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
        for i in range(4):
            if intersect((x1, y1), (x2, y2), bbox_corners[i], bbox_corners[(i + 1) % 4]):
                return True
        
        return False

    # Try direct Manhattan path first
    path = []
    current_x, current_y = start_x, start_y
    
    # Determine the direction based on relative positions
    dx = end_x - start_x
    dy = end_y - start_y
    
    # Decide if we're going up or down, left or right first
    if dx > 0:  # Target is to the right
        if dy > 0:  # and up
            direction = 'right_up'
        else:  # and down
            direction = 'right_down'
    else:  # Target is to the left
        if dy > 0:  # and up
            direction = 'left_up'
        else:  # and down
            direction = 'left_down'
    
    # Create the path based on direction
    path.append((current_x, current_y))

    # Helper function to add detours
    def add_detour(path, current_x, current_y, target_x, target_y, offset):
        if direction == 'left_up':
            path.append((current_x, current_y + offset))  # Down detour
            path.append((target_x + offset, current_y - offset))  # Up Horizontal detour
            path.append((target_x + offset, target_y))  # Finish at target
            path.append((target_x, target_y))  # Finish at target
        elif direction == 'right_up':
            path.append((current_x, current_y + offset))  # Down detour
            path.append((target_x, current_y + offset))  # Up Horizontal detour
            path.append((target_x, target_y))  # Finish at target
        elif direction == 'left_down':
            path.append((current_x, current_y + offset))  # Up detour
            path.append((target_x - offset, current_y + offset))  # Up Horizontal detour
            path.append((target_x - offset, target_y))  # Finish at target
            path.append((target_x, target_y))  # Finish at target
        elif direction == 'right_down':
            path.append((current_x, current_y + offset))  # Up detour
            path.append((target_x, current_y + offset))  # Up Horizontal detour
            path.append((target_x, target_y))  # Finish at target
    
    # Add detours based on direction
    add_detour(path, current_x, current_y, end_x, end_y, manhattan_offset)
    
    # Check and avoid obstacles
    optimized_path = [path[0]]
    for i in range(1, len(path)):
        prev = optimized_path[-1]
        current = path[i]
        
        # Check segment for collisions
        collision = False
        for bbox in instances_bbox:
            if segment_intersects_bbox(prev[0], prev[1], current[0], current[1], bbox):
                collision = True
                break

        if collision:
            # Extend the path to detour around the obstacle
            detour_offset = manhattan_offset * 2  # Increase offset for detour
            add_detour(optimized_path, prev[0], prev[1], current[0], current[1], -detour_offset)
        else:
            optimized_path.append(current)
    
    # Create wire segments from optimized path
    wire_segments = []
    for i in range(1, len(optimized_path)):
        x1, y1 = optimized_path[i-1]
        x2, y2 = optimized_path[i]
        wire_segments.append((x1, y1, x2, y2))
    
    return wire_segments


class UnionFind:
    def __init__(self):
        self.parent = {}
    
    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]  # Path compression
            x = self.parent[x]
        return x
    
    def union(self, x, y):
        x_root = self.find(x)
        y_root = self.find(y)
        if x_root != y_root:
            self.parent[y_root] = x_root

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
        self.part_counters = {}  # To track incremental enumeration for each prefix

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
        symbols = root.findall(".//symbol")
        for symbol_elem in symbols:
            if symbol_elem.attrib.get("name") == symbol_name:
                items = []
                for wire_elem in symbol_elem.findall(".//wire"):
                    wire = Wire(
                        x1=float(wire_elem.attrib["x1"]),
                        y1=float(wire_elem.attrib["y1"]),
                        x2=float(wire_elem.attrib["x2"]),
                        y2=float(wire_elem.attrib["y2"]),
                        width=float(wire_elem.attrib["width"]),
                        layer=int(wire_elem.attrib["layer"])
                    )
                    items.append(wire)
                for text_elem in symbol_elem.findall(".//text"):
                    text = Text(
                        x=float(text_elem.attrib["x"]),
                        y=float(text_elem.attrib["y"]),
                        size=float(text_elem.attrib["size"]),
                        layer=int(text_elem.attrib["layer"]),
                        value=text_elem.text
                    )
                    items.append(text)
                for pin_elem in symbol_elem.findall(".//pin"):
                    pin = Pin(
                        name=pin_elem.attrib["name"],
                        x=float(pin_elem.attrib["x"]),
                        y=float(pin_elem.attrib["y"]),
                        visible=pin_elem.attrib.get("visible", "off"),
                        length=pin_elem.attrib.get("length", "short"),
                        direction=pin_elem.attrib.get("direction", "pas"),
                        rotation=attributes.ATTR_ROT.parse(pin_elem.attrib.get("rot", "R0"))
                    )
                    items.append(pin)
                for rect_elem in symbol_elem.findall(".//rectangle"):
                    rectangle = Rectangle(
                        x1=float(rect_elem.attrib["x1"]),
                        y1=float(rect_elem.attrib["y1"]),
                        x2=float(rect_elem.attrib["x2"]),
                        y2=float(rect_elem.attrib["y2"]),
                        layer=int(rect_elem.attrib["layer"]),
                        rotation=attributes.ATTR_ROT.parse(rect_elem.attrib.get("rot", "R0"))
                    )
                    items.append(rectangle)
                for circle_elem in symbol_elem.findall(".//circle"):
                    circle = Circle(
                        x=float(circle_elem.attrib["x"]),
                        y=float(circle_elem.attrib["y"]),
                        radius=float(circle_elem.attrib["radius"]),
                        layer=int(circle_elem.attrib["layer"]),
                        width=float(circle_elem.attrib.get("width", 0.254))
                    )
                    items.append(circle)
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
            lib_name = 'transistor-npn'
            symbol_name = 'NPN'
        elif prefix == 'R':
            lib_name = 'resistor-power'
            symbol_name = 'R'
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

    def add_part(self, ds_name, name):
        part_name = name.split('_')[1]
        d_name = name.split('_')[0]
        ds = self.device_sets[ds_name]
        lib = next(lib for lib in self.libraries.values() if ds in lib.device_sets)
        part = EaglePart(name=part_name, library=lib, device_set=ds, device=ds.devices[d_name])
        self.parts[part_name] = part
        self.eagle_schematic.parts.append(part)

    def add_instance(self, device_set_name, part_name, prefix):
        # Auto-increment the part counter for the given prefix
        if prefix not in self.part_counters:
            self.part_counters[prefix] = 1
        else:
            self.part_counters[prefix] += 1

        # Generate the part name with the incremented counter
        part_number = self.part_counters[prefix]
        full_part_name = f"{prefix}{part_number}"

        # Add the part to the schematic
        ds = self.device_sets[device_set_name]
        lib = next(lib for lib in self.libraries.values() if ds in lib.device_sets)
        part = EaglePart(name=full_part_name, library=lib, device_set=ds, device=ds.devices[part_name])
        self.parts[full_part_name] = part
        self.eagle_schematic.parts.append(part)

        # Create the instance
        eagle_instance = EagleInstance(
            part=part, x=0, y=0, gate=part.device_set.gates[0], rotation=attributes.Rotation(0)
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
        pin_positions = {}
        for instance in self.instances:
            inst = instance.eagle_instance
            symbol = inst.gate.symbol
            pins = get_pins_from_symbol(symbol)
            for pin in pins:
                abs_pos = compute_absolute_position(inst.x, inst.y, inst.rotation, pin['x'], pin['y'])
                pin_positions[(inst.part.name, pin['name'])] = abs_pos

        uf = UnionFind()
        connections = []
        for instance in self.instances:
            for pin, target in instance.connections.items():
                start_part = instance.eagle_instance.part.name
                start_pin = pin
                start_pos = pin_positions.get((start_part, start_pin))
                if start_pos is None:
                    raise ValueError(f"Pin {start_pin} not found on part {start_part}")
                
                target_instance, target_pin = target
                end_inst_eagle = target_instance.eagle_instance
                end_part = end_inst_eagle.part.name
                end_pin = target_pin
                end_pos = pin_positions.get((end_part, end_pin))
                if end_pos is None:
                    raise ValueError(f"Pin {end_pin} not found on part {end_part}")
                
                uf.union((start_part, start_pin), (end_part, end_pin))
                connections.append({
                    'start_part': start_part,
                    'start_pin': start_pin,
                    'end_part': end_part,
                    'end_pin': end_pin,
                    'start_pos': start_pos,
                    'end_pos': end_pos,
                    'start_instance': instance.eagle_instance,
                    'end_instance': end_inst_eagle,
                })
        
        nets = defaultdict(list)
        for conn in connections:
            root = uf.find((conn['start_part'], conn['start_pin']))
            nets[root].append(conn)
        
        existing_net_names = set()
        
        manhattan_offset = 2
        for net_root, net_conns in nets.items():
            parts_in_net = set()
            for part_pin in uf.parent.keys():
                if uf.find(part_pin) == net_root:
                    parts_in_net.add(part_pin[0])
            
            component_counts = defaultdict(int)
            for part in parts_in_net:
                component_type = part[0] if part else '?'
                component_counts[component_type] += 1
            
            sorted_types = sorted(component_counts.keys())
            name_parts = [f"{t}{component_counts[t]}" for t in sorted_types]
            base_name = "net_" + "_".join(name_parts)
            suffix = 1
            net_name = base_name
            while net_name in existing_net_names:
                net_name = f"{base_name}_{suffix}"
                suffix += 1
            existing_net_names.add(net_name)
            
            wires_set = set()
            pin_refs = {}
            
            for conn in net_conns:
                start_part = conn['start_part']
                start_pin = conn['start_pin']
                end_part = conn['end_part']
                end_pin = conn['end_pin']
                start_pos = conn['start_pos']
                end_pos = conn['end_pos']
                start_inst = conn['start_instance']
                end_inst = conn['end_instance']
                
                final_end_x, final_end_y = end_pos
                
                # Compute obstacle instances' bboxes with pins
                obstacle_instances = [i.eagle_instance for i in self.instances if i.eagle_instance not in [start_inst, end_inst]]
                obstacle_bbox = [compute_instance_bbox_with_pins(inst) for inst in obstacle_instances]
                instances_bbox = [compute_instance_bbox(inst) for inst in  [start_inst, end_inst]]
                
                wire_path = calculate_wire_path(
                    start_pos[0], start_pos[1],
                    final_end_x, final_end_y,
                    obstacle_bbox,
                    manhattan_offset
                )

                manhattan_offset += 1
                
                for seg in wire_path:
                    x1, y1, x2, y2 = seg
                    wire_tuple = (x1, y1, x2, y2, 0.2)
                    wires_set.add(wire_tuple)
                
                pin_refs[(start_part, start_pin)] = PinRef(part=start_part, gate="G$1", pin=start_pin)
                pin_refs[(end_part, end_pin)] = PinRef(part=end_part, gate="G$1", pin=end_pin)
            
            wires = [Wire(x1=x1, y1=y1, x2=x2, y2=y2, width=width) for (x1, y1, x2, y2, width) in wires_set]
            net = Net(name=net_name, net_class=0)
            segment = Segment(items=list(pin_refs.values()) + wires)
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
        self.parts = parts if parts else []  # parts will now store tuples of (device_set_name, part_name)
        self.descriptors = descriptors if descriptors else []
        self.descriptor_counters = {}  # To track incremental enumeration for each prefix
        self.connections = connections if connections else {}
        
    def add_descriptor(self, device_set, part, prefix):
        if prefix not in self.descriptor_counters:
            self.descriptor_counters[prefix] = 1
        else:
            self.descriptor_counters[prefix] += 1

        descriptor = Descriptor(self.descriptor_counters[prefix], device_set, part, prefix)
        self.descriptors.append(descriptor)

        return descriptor

    def add_connection(self, pin_name, source_instance, target):
        self.connections[pin_name] = source_instance, target

    def to_xml(self):
        # Create the root element for the symbol
        symbol_elem = ET.Element("symbol", {"name": self.name})

        # Add instances
        instances_elem = ET.SubElement(symbol_elem, "instances")
        for instance in self.descriptors:
            identifier = instance.identifier
            device_set_name = instance.device_set
            part_name = instance.part
            prefix = instance.prefix

            instance_elem = ET.SubElement(instances_elem, "instance", {
                "identifier": str(identifier),
                "device_set": device_set_name,
                "part": part_name,
                "prefix": prefix
            })

        # Add connections
        connections_elem = ET.SubElement(symbol_elem, "connections")
        for pin_name, connection in self.connections.items():
            source, target = connection
            target_instance, target_pin = target

            conn_elem = ET.SubElement(connections_elem, "connection", {
                "source_instance": ":".join([str(source.identifier), source.device_set,source.part,source.prefix,pin_name]), # Serialize tuple as a string
                "target_instance": ":".join([str(target_instance.identifier),target_instance.device_set,target_instance.part,target_instance.prefix,target_pin])  # Serialize tuple as a string
            })
        return ET.tostring(symbol_elem, encoding='unicode')
    
    def save(self, filename):
        symbol = self.to_xml()
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(symbol)  # Decode bytes to string    
        print(f"Symbol saved to {filename}")


    @staticmethod
    def load(filename, schematic):
        symbol = None
        with open(filename, 'r', encoding='utf-8') as f:
            symbol = Symbol.from_xml(f.read(), schematic)
        
        return symbol

    @staticmethod
    def from_xml(xml_string, schematic):
        # Parse the XML string
        symbol_elem = ET.fromstring(xml_string)
        name = symbol_elem.attrib["name"]
        
        # Create a new Symbol object
        symbol = Symbol(name)
        
        # Parse instances
        instances_elem = symbol_elem.find("instances")
        for instance_elem in instances_elem.findall("instance"):
            identifier = int(instance_elem.attrib["identifier"])  # Add identifier
            device_set = instance_elem.attrib["device_set"]
            part = instance_elem.attrib["part"]
            prefix = instance_elem.attrib["prefix"]
            # Add the descriptor with the identifier
            symbol.descriptors.append(Descriptor(identifier, device_set, part, prefix))
        # Parse connections
        connections_elem = symbol_elem.find("connections")
        for conn_elem in connections_elem.findall("connection"):
            source = conn_elem.attrib.get("source_instance")
            source_identifier = int(source.split(":")[0])
            source_device_set = source.split(":")[1]
            source_part = source.split(":")[2]
            source_prefix = source.split(":")[3]
            source_instance_pin = source.split(":")[4]
            
            target = conn_elem.attrib.get("target_instance")
            target_identifier = int(target.split(":")[0])
            target_device_set = target.split(":")[1]
            target_part = target.split(":")[2]
            target_prefix = target.split(":")[3]
            target_instance_pin = target.split(":")[4]
            
            # Find the source instance
            source_instance = next(
                (i for i in symbol.descriptors 
                if i.identifier == source_identifier 
                    and i.device_set == source_device_set 
                    and i.part == source_part 
                    and i.prefix == source_prefix),
                None  # Provide a default value to avoid StopIteration
            )
            if source_instance is None:
                raise ValueError(f"Source instance not found: {source}")
            
            # Find the target instance
            target_instance = next(
                (i for i in symbol.descriptors 
                if i.identifier == target_identifier 
                    and i.device_set == target_device_set 
                    and i.part == target_part 
                    and i.prefix == target_prefix),
                None  # Provide a default value to avoid StopIteration
            )
            if target_instance is None:
                raise ValueError(f"Target instance not found: {target}")
            
            # Add the connection
            symbol.add_connection(source_instance_pin, source_instance, (target_instance, target_instance_pin))
        
        return symbol
