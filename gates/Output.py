import pygame
from gates.Ports import Port
from utils.popup import Popup
from utils.colors import COLORS

class Output:
    def __init__(self, x, y) -> None:
        self.x = x  # Original x position
        self.y = y  # Original y position
        self.port = Port(x - 20 - 30, y, self, "input")
        self.input = self.port
        self.color = COLORS["INPUT"]

    def serialize(self):
        return {
            "x": self.x,
            "y": self.y,
            "color": self.color,
            "port": self.port.serialize()
        }

    @staticmethod
    def deserialize(data):
        output = Output(data["x"], data["y"])
        output.color = COLORS["INPUT"]
        output.port = Port.deserialize(data["port"], output)
        output.move(output.x, output.y) # temporary serialization patch
        return output
    

    def draw(self, screen, font, images, zoom=1.0, offset=(0, 0)):
        # Apply zoom and pan offset
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        scaled_port_x = int(self.port.x * zoom + offset[0])
        scaled_port_y = int(self.port.y * zoom + offset[1])

        # Draw the connection line
        for port in self.port.connected_to:
            scaled_connected_x = int(port.x * zoom + offset[0])
            scaled_connected_y = int(port.y * zoom + offset[1])
            pygame.draw.line(screen, "#0f0fff", (scaled_connected_x, scaled_connected_y), (scaled_port_x, scaled_port_y), int(5 * zoom))

        # Draw the output circle
        pygame.draw.circle(screen, COLORS["INPUT"], (scaled_port_x, scaled_port_y), int(5 * zoom))
        pygame.draw.circle(screen, self.color, (scaled_x, scaled_y), int(20 * zoom))
        pygame.draw.circle(screen, COLORS["BLACK"], (scaled_x, scaled_y), int(20 * zoom), int(4 * zoom))

        # Draw the value text
        text = font.render(f"{str(int(self.port.value))}", 1, COLORS["BLACK"])
        scaled_text_x = scaled_x - int(text.get_width() / 2)
        scaled_text_y = scaled_y - int(text.get_height() / 2)
        screen.blit(text, (scaled_text_x, scaled_text_y))

    def move(self, x, y):
        # Update the original position
        self.x = x
        self.y = y
        self.port.x = x - 50
        self.port.y = y

    def calculate(self, update=False):
        self.port.value = self.port.connected_from.value if self.port.connected_from else 0
        self.color = COLORS["GREEN"] if self.port.value else COLORS["RED"]

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
        return (x > 50 and x < screen.get_width() - (25) and y > 12 / 2 + 50 and y < screen.get_height() - (12))

    def mouse_hovered(self, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])
        return (scaled_x - 55 <= x <= scaled_x + 25 and scaled_y - 25 <= y <= scaled_y + 25)

    def event_handler(self, screen, event, selected_gate, selected_port, output_to_remove, popup, zoom=1.0, offset=(0, 0)):
        x, y = pygame.mouse.get_pos()
        scaled_x = int(self.x * zoom + offset[0])
        scaled_y = int(self.y * zoom + offset[1])

        if event.type == pygame.MOUSEBUTTONDOWN:
            l, _, r = pygame.mouse.get_pressed()

            if self.port.mouse_hovered(zoom, offset):
                if l:
                    selected_port = self.port
                if r:
                    if self.port.connected_from:
                        self.port.connected_from.connected_to.remove(self.port)
                    self.port.connected_from = None

            if not selected_port and self.mouse_hovered(zoom, offset):
                if l:
                    selected_gate = self
                if r:
                    popup = Popup(
                        x, y, screen,
                        [
                            ("Delete", lambda: self),
                            ("N/A", lambda: Popup(
                                x, y, screen,
                                [("1", lambda: None), ("2", lambda: None)]
                            )),
                            ("Remove_All_Connection", lambda: self.remove())
                        ]
                    )

        if event.type == pygame.MOUSEBUTTONUP:
            if selected_port:
                if self != selected_port.gate:
                    if self.port.mouse_hovered(zoom, offset):
                        self.port.connect(selected_port)

        if event.type == pygame.MOUSEMOTION:
            if selected_gate and self.mouse_in_bound(screen, x, y, selected_gate, zoom, offset):
                scaled_mouse_x = int((x - offset[0]) / zoom)
                scaled_mouse_y = int((y - offset[1]) / zoom)
                selected_gate.move(scaled_mouse_x, scaled_mouse_y)

        return selected_gate, selected_port, output_to_remove, popup