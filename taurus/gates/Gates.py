import pygame
from gates.Ports import Port
from utils.popup import Popup
from utils.colors import COLORS

class Gate:
    def __init__(self, x, y, images, type) -> None:
        self.x = x  # Original x position
        self.y = y  # Original y position
        self.type = type
        self.width = images[self.type].get_width()
        self.height = images[self.type].get_height()
        self.output = Port(x + self.width, y + (self.height / 2), self, "output")

        if type != "NOT":
            self.input = [Port(x, y + (self.height / 4), self), Port(x, y + (3 * self.height / 4), self)]
        else:
            self.input = [Port(x, y + (self.height / 2), self)]

    def serialize(self):
        return {
            "x": self.x,
            "y": self.y,
            "type": self.type,
            "output": self.output.serialize(),
            "input": [port.serialize() for port in self.input]
        }

    @staticmethod
    def deserialize(data, images):
        gate = Gate(data["x"], data["y"], images, data["type"])
        gate.output = Port.deserialize(data["output"], gate)
        gate.input = [Port.deserialize(port_data, gate) for port_data in data["input"]]

        gate.move(gate.x, gate.y) # temporary serialization patch
        return gate

    def draw(self, screen, font, images, wires=True, zoom=1.0, offset=(0, 0)):
        # Apply zoom and pan offset
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_width = int(self.width * zoom)
        scaled_height = int(self.height * zoom)

        # Draw the gate image
        scaled_img = pygame.transform.scale(images[self.type], (scaled_width, scaled_height))
        screen.blit(scaled_img, (scaled_x, scaled_y))

        # Draw connection wires
        color = COLORS["FALSE"] if self.output.value else COLORS["TRUE"]
        if wires:
            for port in self.get_ports():
                scaled_port_x = int(port.x * zoom + offset[0])
                scaled_port_y = int(port.y * zoom + offset[1])
                for connected_port in port.connected_to:
                    scaled_connected_x = int(connected_port.x * zoom + offset[0])
                    scaled_connected_y = int(connected_port.y * zoom + offset[1])
                    pygame.draw.line(
                        screen,
                        color,
                        (scaled_connected_x, scaled_connected_y),
                        (scaled_port_x, scaled_port_y),
                        int(5 * zoom),
                    )

        # Draw output port
        pygame.draw.circle(
            screen,
            COLORS["OUTPUT"],
            (int(self.output.x * zoom + offset[0]), int(self.output.y * zoom + offset[1])),
            int(5 * zoom),
        )

        # Draw input ports
        if self.type != "NOT":
            pygame.draw.circle(
                screen,
                COLORS["INPUT"],
                (int(self.input[0].x * zoom + offset[0]), int(self.input[0].y * zoom + offset[1])),
                int(5 * zoom),
            )
            pygame.draw.circle(
                screen,
                COLORS["INPUT"],
                (int(self.input[1].x * zoom + offset[0]), int(self.input[1].y * zoom + offset[1])),
                int(5 * zoom),
            )
        else:
            pygame.draw.circle(
                screen,
                COLORS["INPUT"],
                (int(self.input[0].x * zoom + offset[0]), int(self.input[0].y * zoom + offset[1])),
                int(5 * zoom),
            )

    def move(self, x, y):
        # Update the original position
        self.x = x - (self.width / 2)
        self.y = y - (self.height / 2)
        self.output.set_pos(self.x + self.width, self.y + (self.height / 2))
        if self.type != "NOT":
            self.input[0].set_pos(self.x, self.y + (self.height / 4))
            self.input[1].set_pos(self.x, self.y + (3 * self.height / 4))
        else:
            self.input[0].set_pos(self.x, self.y + (self.height / 2))

    def calculate(self, update=False):
        if update:
            for input in self.input:
                input.value = input.connected_from.value if input.connected_from else 0
        match self.type:
            case "AND":
                self.output.value = self.input[0].value and self.input[1].value
            case "OR":
                self.output.value = self.input[0].value or self.input[1].value
            case "NOT":
                self.output.value = not self.input[0].value
            case "NAND":
                self.output.value = not (self.input[0].value and self.input[1].value)
            case "NOR":
                self.output.value = not (self.input[0].value or self.input[1].value)
            case "XOR":
                self.output.value = self.input[0].value ^ self.input[1].value
            case "XNOR":
                self.output.value = not (self.input[0].value ^ self.input[1].value)

    def remove(self):
        for port in self.get_ports():
            if port.connected_from:
                port.connected_from.connected_to.remove(port)
            for p in port.connected_to:
                p.connected_from = None
            port.connected_to = []
            port.connected_from = None
        return "remove"

    def get_ports(self):
        ports = [self.output]
        ports.extend(self.input)
        return ports

    def convert(self, type, images):
        if self.type != type:
            if self.type == "NOT" or type == "NOT":
                for input in self.input:
                    if input.connected_from:
                        input.connected_from.connected_to.remove(input)
                    input.connected_from = None
            if type == "NOT":
                self.input = [Port(self.x, self.y + (self.height / 2), self)]
            elif self.type == "NOT":
                self.input = [
                    Port(self.x, self.y + (self.height / 4), self),
                    Port(self.x, self.y + (3 * self.height / 4), self),
                ]
            self.type = type
        return "remove"

    def mouse_hovered(self, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_width = int(self.width * zoom)
        scaled_height = int(self.height * zoom)
        return (
            scaled_x <= x <= scaled_x + scaled_width and scaled_y <= y <= scaled_y + scaled_height
        )

    def mouse_in_bound(self, screen, x, y, obj, zoom=1.0, offset=(0, 0)):
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_width = int(obj.width * zoom)
        scaled_height = int(obj.height * zoom)
        return (
            x > scaled_width / 2
            and x < screen.get_width() - (scaled_width / 2)
            and y > scaled_height / 2 + 50
            and y < screen.get_height() - (scaled_height / 2)
        )

    def event_handler(self, screen, images, event, selected_gate, selected_port, gate_to_remove, popup, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])

        if event.type == pygame.MOUSEBUTTONDOWN:
            l, _, r = pygame.mouse.get_pressed()
            for port in self.get_ports():
                if port.mouse_hovered(zoom, offset):
                    if l:
                        selected_port = port
                    if r:
                        if port.type == "output":
                            for _port in port.connected_to:
                                _port.connected_from = None
                            port.connected_to = []
                        else:
                            if port.connected_from:
                                port.connected_from.connected_to.remove(port)
                            port.connected_from = None

            if not selected_port and self.mouse_hovered(zoom, offset):
                if l:
                    selected_gate = self
                if r:
                    popup = Popup(
                        x,
                        y,
                        screen,
                        [
                            ("Delete", lambda: self),
                            (
                                "Convert_To",
                                lambda: Popup(
                                    x,
                                    y,
                                    screen,
                                    [
                                        ("AND", lambda: self.convert("AND", images)),
                                        ("OR", lambda: self.convert("OR", images)),
                                        ("NOT", lambda: self.convert("NOT", images)),
                                        ("NAND", lambda: self.convert("NAND", images)),
                                        ("NOR", lambda: self.convert("NOR", images)),
                                        ("XOR", lambda: self.convert("XOR", images)),
                                        ("XNOR", lambda: self.convert("XNOR", images)),
                                    ],
                                ),
                            ),
                            ("Remove_All_Connection", lambda: self.remove()),
                        ],
                    )

        if event.type == pygame.MOUSEBUTTONUP:
            if selected_port:
                if self != selected_port.gate:
                    for port in self.get_ports():
                        if port.mouse_hovered(zoom, offset):
                            port.connect(selected_port)

        if event.type == pygame.MOUSEMOTION:
            if selected_gate and self.mouse_in_bound(screen, x, y, selected_gate, zoom, offset):
                scaled_mouse_x = int((x - offset[0]) / zoom)
                scaled_mouse_y = int((y - offset[1]) / zoom)
                selected_gate.move(scaled_mouse_x, scaled_mouse_y)

        return selected_gate, selected_port, gate_to_remove, popup