import json
from pathlib import Path

import pygame

from .gates.Gates import Gate
from .utils.navbar import Menu
from .gates.Input import Input
from .gates.Output import Output
from .utils.colors import COLORS


def _asset_path(*parts: str) -> Path:
    return Path(__file__).resolve().parent.joinpath(*parts)


def _project_path() -> Path:
    return Path(__file__).resolve().with_name("project.json")


def _load_images():
    img_dir = _asset_path("img")
    return {
        "AND": pygame.image.load(str(img_dir / "AND.svg")),
        "OR": pygame.image.load(str(img_dir / "OR.svg")),
        "NOT": pygame.image.load(str(img_dir / "NOT.svg")),
        "NAND": pygame.image.load(str(img_dir / "NAND.svg")),
        "NOR": pygame.image.load(str(img_dir / "NOR.svg")),
        "XOR": pygame.image.load(str(img_dir / "XOR.svg")),
        "XNOR": pygame.image.load(str(img_dir / "XNOR.svg")),
        "INPUT": pygame.image.load(str(img_dir / "input.png")),
        "OUTPUT": pygame.transform.flip(pygame.image.load(str(img_dir / "input.png")), 1, 0),
    }


def main():
    pygame.init()

    images = _load_images()
    pygame.display.set_caption("Taurus Logical Simulator")
    screen = pygame.display.set_mode((900, 600), pygame.RESIZABLE)
    gates_list = ["AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR"]
    font = pygame.font.SysFont("arial", 10)
    port_font = pygame.font.SysFont("arial", 20, 1)

    selected_gate = None
    selected_input = None
    selected_output = None
    selected_port = None
    selected = None
    gate_to_remove = None
    input_to_remove = None
    output_to_remove = None
    popup = None
    to_remove = None
    sub_popup = None
    tooltip = None
    menu = Menu(screen)

    gates = [Gate(200, 170, images, "OR")]
    inputs = [Input(100, 120), Input(100, 220)]
    outputs = [Output(500, 120)]

    inputs[0].port.connected_to.append(gates[0].input[0])
    gates[0].input[0].connected_from = inputs[0].port
    inputs[1].port.connected_to.append(gates[0].input[1])
    gates[0].input[1].connected_from = inputs[1].port
    gates[0].output.connected_to.append(outputs[0].port)
    outputs[0].port.connected_from = gates[0].output

    for key in images:
        menu.add_child(key, images[key])

    zoom_level = 1.0
    min_zoom = 0.25
    max_zoom = 2.0
    pan_offset = [0, 0]
    dragging = False
    last_mouse_pos = None
    default_project = _project_path()

    def save_project(filename=default_project):
        try:
            serialized_data = {
                "inputs": [input_obj.serialize() for input_obj in inputs],
                "outputs": [output.serialize() for output in outputs],
                "gates": [gate.serialize() for gate in gates],
                "zoom_level": zoom_level,
                "pan_offset": pan_offset,
            }
            path = Path(filename)
            path.write_text(json.dumps(serialized_data, indent=4), encoding="utf-8")
            print(f"Project saved to {path}")
        except Exception as exc:
            print(f"Error saving project: {exc}")

    def load_project(filename=default_project):
        nonlocal inputs, outputs, gates, zoom_level, pan_offset
        try:
            loaded_data = json.loads(Path(filename).read_text(encoding="utf-8"))
            ports = {}

            inputs = []
            for input_data in loaded_data["inputs"]:
                input_obj = Input.deserialize(input_data)
                inputs.append(input_obj)
                ports[input_obj.port.uuid] = input_obj.port

            outputs = []
            for output_data in loaded_data["outputs"]:
                output = Output.deserialize(output_data)
                outputs.append(output)
                ports[output.port.uuid] = output.port

            gates = []
            for gate_data in loaded_data["gates"]:
                gate = Gate.deserialize(gate_data, images)
                gates.append(gate)
                ports[gate.output.uuid] = gate.output
                for input_port in gate.input:
                    ports[input_port.uuid] = input_port

            for uuid in ports:
                ports[uuid].solve_connections(ports)

            zoom_level = loaded_data["zoom_level"]
            pan_offset = loaded_data["pan_offset"]
            print(f"Project loaded from {filename}")
        except Exception as exc:
            print(f"Error loading project: {exc}")

    def calculate_output():
        visited = {}

        def dfs(obj):
            if type(obj) == Input:
                return obj.port.value
            if obj in visited:
                return obj.output.value
            visited[obj] = 0
            for input_port in obj.input:
                if input_port in visited:
                    input_port.value = visited[input_port]
                else:
                    input_port.value = dfs(input_port.connected_from.gate) if input_port.connected_from else 0
                    visited[input_port] = input_port.value
            obj.calculate()
            return obj.output.value

        for output in outputs:
            cur = output.port.connected_from if output.port.connected_from else None
            output.port.value = dfs(cur.gate) if cur else 0
            output.calculate()

        for input_obj in inputs:
            for connection in input_obj.port.connected_to:
                connection.value = input_obj.port.value
        for obj in gates + outputs:
            obj.calculate(update=True)

    def draw_bg():
        screen.fill(COLORS["GREY"])
        grid_size = 20
        scaled_grid_size = max(1, int(grid_size * zoom_level))
        offset_x, offset_y = pan_offset
        for x in range(offset_x % scaled_grid_size, screen.get_width(), scaled_grid_size):
            pygame.draw.line(screen, COLORS["LIGHT_GREY"], (x, 0), (x, screen.get_height()))
        for y in range(offset_y % scaled_grid_size, screen.get_height(), scaled_grid_size):
            pygame.draw.line(screen, COLORS["LIGHT_GREY"], (0, y), (screen.get_width(), y))
        text = font.render("Cybertwip", 1, COLORS["BLACK"])
        screen.blit(text, (screen.get_width() - text.get_width() - 10, screen.get_height() - text.get_height() - 20))

    running = True
    clock = pygame.time.Clock()

    while running:
        clock.tick(60)
        calculate_output()
        draw_bg()

        for obj in gates + inputs + outputs:
            obj.draw(screen, port_font, images, zoom=zoom_level, offset=pan_offset)
        for gate in gates:
            gate.draw(screen, port_font, images, wires=False, zoom=zoom_level, offset=pan_offset)
        menu.draw()

        if popup:
            popup.draw()

        if tooltip:
            tooltip.draw(screen)

        x, y = pygame.mouse.get_pos()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_s and pygame.key.get_mods() & pygame.KMOD_CTRL:
                    save_project(default_project)
                elif event.key == pygame.K_o and pygame.key.get_mods() & pygame.KMOD_CTRL:
                    load_project(default_project)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 4:
                zoom_level = min(zoom_level + 0.1, max_zoom)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 5:
                zoom_level = max(zoom_level - 0.1, min_zoom)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
                dragging = True
                last_mouse_pos = pygame.mouse.get_pos()
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 3:
                dragging = False
            if dragging:
                current_mouse_pos = pygame.mouse.get_pos()
                dx, dy = current_mouse_pos[0] - last_mouse_pos[0], current_mouse_pos[1] - last_mouse_pos[1]
                pan_offset[0] += dx
                pan_offset[1] += dy
                last_mouse_pos = current_mouse_pos

            if not selected:
                selected, tooltip = menu.event_handler(event, selected, tooltip)
                if selected:
                    if selected.title in gates_list:
                        scaled_x = int((x - pan_offset[0]) / zoom_level)
                        scaled_y = int((y - pan_offset[1]) / zoom_level)
                        gates.append(Gate(scaled_x, scaled_y, images, selected.title))
                        selected_gate = gates[-1]
                    elif selected.title == "INPUT":
                        scaled_x = int((x - pan_offset[0]) / zoom_level)
                        scaled_y = int((y - pan_offset[1]) / zoom_level)
                        inputs.append(Input(scaled_x, scaled_y))
                        selected_input = inputs[-1]
                    elif selected.title == "OUTPUT":
                        scaled_x = int((x - pan_offset[0]) / zoom_level)
                        scaled_y = int((y - pan_offset[1]) / zoom_level)
                        outputs.append(Output(scaled_x, scaled_y))
                        selected_output = outputs[-1]

            for gate in gates:
                selected_gate, selected_port, gate_to_remove, popup = gate.event_handler(
                    screen, images, event, selected_gate, selected_port, gate_to_remove, popup, zoom=zoom_level, offset=pan_offset
                )

            for input_obj in inputs:
                selected_input, selected_port, input_to_remove, popup = input_obj.event_handler(
                    screen, event, selected_input, selected_port, input_to_remove, popup, zoom=zoom_level, offset=pan_offset
                )

            for output in outputs:
                selected_output, selected_port, output_to_remove, popup = output.event_handler(
                    screen, event, selected_output, selected_port, output_to_remove, popup, zoom=zoom_level, offset=pan_offset
                )

            if popup:
                popup, to_remove, sub_popup = popup.event_handler(event, popup, to_remove, sub_popup)
                if to_remove == "remove" or type(to_remove) in [Gate, Input, Output]:
                    popup = None
                if type(to_remove) == Gate:
                    gate_to_remove = to_remove
                elif type(to_remove) == Input:
                    input_to_remove = to_remove
                elif type(to_remove) == Output:
                    output_to_remove = to_remove
                to_remove = None

            if event.type == pygame.MOUSEBUTTONUP:
                selected_gate, selected_input, selected_output, selected_port, selected = [None] * 5
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_ARROW)

        if sub_popup:
            popup = sub_popup
            sub_popup = None

        if gate_to_remove:
            gate_to_remove.remove()
            gates.remove(gate_to_remove)
            gate_to_remove = None
        if input_to_remove:
            input_to_remove.remove()
            inputs.remove(input_to_remove)
            input_to_remove = None
        if output_to_remove:
            output_to_remove.remove()
            outputs.remove(output_to_remove)
            output_to_remove = None

        if selected_port:
            scaled_port_x = int(selected_port.x * zoom_level + pan_offset[0])
            scaled_port_y = int(selected_port.y * zoom_level + pan_offset[1])
            pygame.draw.line(screen, COLORS["RED"], (scaled_port_x, scaled_port_y), (x, y), 5)

        if selected_gate:
            pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND)

        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
