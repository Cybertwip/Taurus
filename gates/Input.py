import pygame
from gates.Ports import Port
from utils.popup import Popup
from utils.colors import COLORS

class Input:
    def __init__(self, x, y) -> None:
        self.x = x  # Original x position
        self.y = y  # Original y position
        self.type = "switch"
        self.port = Port(x + 20 + 30, y, self, "output")
        self.output = self.port
        self.color = COLORS["RED"]

    def serialize(self):
        return {
            "x": self.x,
            "y": self.y,
            "type": self.type,
            "color": self.color,
            "port": self.port.serialize()
        }

    @staticmethod
    def deserialize(data):
        input_obj = Input(data["x"], data["y"])
        input_obj.type = data["type"]
        input_obj.color = COLORS["RED"]
        input_obj.port = Port.deserialize(data["port"], input_obj)
        input_obj.move(input_obj.x, input_obj.y) # temporary serialization patch
        input_obj.color = COLORS["GREEN"] if input_obj.port.value else COLORS["RED"]
        return input_obj

    def draw(self, screen, font, images, zoom=1.0, offset=(0, 0)):
        # Apply zoom and pan offset
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_port_x = int(self.port.x * zoom + offset[0])
        scaled_port_y = int(self.port.y * zoom + offset[1])

        # Draw the input switch or push button
        if self.type == "push":
            pygame.draw.circle(screen, self.color, (scaled_x, scaled_y), int(20 * zoom))
            pygame.draw.circle(screen, COLORS["BLACK"], (scaled_x, scaled_y), int(20 * zoom), int(4 * zoom))
        else:
            pygame.draw.rect(screen, self.color, (scaled_x - int(16 * zoom), scaled_y - int(16 * zoom), int(36 * zoom), int(36 * zoom)))
            pygame.draw.rect(screen, COLORS["BLACK"], (scaled_x - int(20 * zoom), scaled_y - int(20 * zoom), int(40 * zoom), int(40 * zoom)), int(4 * zoom))

        # Draw the port and connections
        for port in self.port.connected_to:
            scaled_connected_x = int(port.x * zoom + offset[0])
            scaled_connected_y = int(port.y * zoom + offset[1])
            pygame.draw.line(screen, self.color, (scaled_connected_x, scaled_connected_y), (scaled_port_x, scaled_port_y), int(5 * zoom))
        pygame.draw.circle(screen, COLORS["OUTPUT"], (scaled_port_x, scaled_port_y), int(5 * zoom))

        # Draw the value text
        text = font.render(f"{str(int(self.port.value))}", 1, COLORS["BLACK"])
        scaled_text_x = scaled_x - int(text.get_width() / 2)
        scaled_text_y = scaled_y - int(text.get_height() / 2)
        screen.blit(text, (scaled_text_x, scaled_text_y))

    def move(self, x, y):
        # Update the original position
        self.x = x
        self.y = y
        self.port.x = x + 50
        self.port.y = y

    def switch(self, zoom_level, pan_offset):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom_level + pan_offset[0])
        scaled_y = int(self.y * zoom_level + pan_offset[1])
        if (scaled_x - 20 <= x <= scaled_x + 20 and scaled_y - 20 <= y <= scaled_y + 20):
            self.port.value = not self.port.value
            self.color = COLORS["GREEN"] if self.port.value else COLORS["RED"]

    def convert(self):
        self.type = "push" if self.type == "switch" else "switch"
        if self.port.value:
            self.port.value = 0
            self.color = COLORS["RED"]
        return "remove"

    def remove(self):
        if self.port.connected_from:
            self.port.connected_from.connected_to.remove(self.port)
        for port in self.port.connected_to:
            port.connected_from = None
        self.port.connected_to = []
        self.port.connected_from = None
        return "remove"

    def mouse_in_bound(self, screen, x, y, obj, zoom=1.0, offset=(0, 0)):
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        return (x > 12 and x < screen.get_width() - (25) and y > 12 / 2 + 50 and y < screen.get_height() - (12))

    def mouse_hovered(self, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        return (scaled_x - 25 <= x <= scaled_x + 50 and scaled_y - 25 <= y <= scaled_y + 25)

    def event_handler(self, screen, event, selected_gate, selected_port, input_to_remove, popup, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])

        if event.type == pygame.MOUSEBUTTONDOWN:
            l, _, r = pygame.mouse.get_pressed()
            if l and self.mouse_hovered(zoom, offset):
                self.switch(zoom, offset)

            if self.port.mouse_hovered(zoom, offset):
                if l:
                    selected_port = self.port
                if r:
                    for port in self.port.connected_to:
                        port.connected_from = None
                    self.port.connected_to = []

            if not selected_port and self.mouse_hovered(zoom, offset):
                if l:
                    selected_gate = self
                if r:
                    popup = Popup(
                        x, y, screen,
                        [
                            ("Delete", lambda: self),
                            ("Change_Type", lambda: Popup(
                                x, y, screen,
                                [("Push" if self.type == "switch" else "Switch", lambda: self.convert())]
                            )),
                            ("Remove_All_Connection", lambda: self.remove())
                        ]
                    )

        if event.type == pygame.MOUSEBUTTONUP:
            if self.type == "push":
                self.switch(zoom, offset)
            if selected_port:
                if self != selected_port.gate:
                    if self.port.mouse_hovered(zoom, offset):
                        self.port.connect(selected_port)

        if event.type == pygame.MOUSEMOTION:
            if selected_gate and self.mouse_in_bound(screen, x, y, selected_gate, zoom, offset):
                scaled_mouse_x = int((x - offset[0]) / zoom)
                scaled_mouse_y = int((y - offset[1]) / zoom)
                selected_gate.move(scaled_mouse_x, scaled_mouse_y)

        return selected_gate, selected_port, input_to_remove, popup