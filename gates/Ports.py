import pygame
import uuid

class Port:
    def __init__(self, x, y, gate, type="input", width=20, height=20) -> None:
        self.uuid = str(uuid.uuid4())
        self.x = x  # Original x position
        self.y = y  # Original y position
        self.gate = gate
        self.type = type
        self.value = 0
        self.connected_to = []
        self.connected_from = None
        self.width = width  # Width of the port (default: 20)
        self.height = height  # Height of the port (default: 20)

    def serialize(self):
        return {
            "uuid": self.uuid,
            "x": self.x,
            "y": self.y,
            "type": self.type,
            "value": self.value,
            "connected_to": [port.uuid for port in self.connected_to],
            "connected_from": self.connected_from.uuid if self.connected_from is not None else None
        }

    @classmethod
    def deserialize(cls, data, gate):
        port = cls(data["x"], data["y"], gate, data["type"])
        port.uuid = data["uuid"]
        port.value = data["value"]
        port.serial_to = data["connected_to"]
        port.serial_from = data["connected_from"]
        return port
    
    def solve_connections(self, port_map):
        # Resolve the 'connected_from' relationship
        if self.serial_from is not None:
            connected_from_uuid = self.serial_from
            if connected_from_uuid in port_map:
                self.connected_from = port_map[connected_from_uuid]
            else:
                raise ValueError(f"Port with UUID {connected_from_uuid} not found in port_map.")

        # Resolve the 'connected_to' relationships
        if self.serial_to:
            for target_port_uuid in self.serial_to:
                if target_port_uuid in port_map:
                    target_port = port_map[target_port_uuid]
                    self.connected_to.append(target_port)
                    target_port.connected_from = self
                else:
                    raise ValueError(f"Port with UUID {target_port_uuid} not found in port_map.")
        del self.serial_from
        del self.serial_to

    def set_pos(self, x, y):
        """Update the original position of the port."""
        self.x = x
        self.y = y

    def mouse_hovered(self, zoom=1.0, offset=(0, 0)):
        """
        Check if the mouse is hovering over the port.
        Adjusts for zoom and pan offset.
        """
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_width = int(self.width * zoom)
        scaled_height = int(self.height * zoom)
        return (
            scaled_x - scaled_width / 2 <= x <= scaled_x + scaled_width / 2 and
            scaled_y - scaled_height / 2 <= y <= scaled_y + scaled_height / 2
        )

    def connect(self, port):
        """
        Connect this port to another port.
        Ensures proper connection based on port types (input/output).
        """
        if self.type != port.type:
            if self.type == "input":
                if self.connected_from:
                    self.connected_from.connected_to.remove(self)
                self.connected_from = port
                port.connected_to.append(self)
            elif self.type == "output":
                if port.connected_from:
                    port.connected_from.connected_to.remove(port)
                self.connected_to.append(port)
                port.connected_from = self
            return True
        return False