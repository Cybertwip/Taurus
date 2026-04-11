import sys; sys.path.insert(0, '.')
from taurus.schematic import _Router, _KiCadWriter, _outward, _snap
sch = __import__('tracer').build_4bit_adder()
r = _Router(sch)
r.run()
writer = _KiCadWriter(sch)
occupied_edges = set()
occupied_nodes = set()
for x1, y1, x2, y2 in r.wires:
    writer._reserve_path([(x1, y1), (x2, y2)], occupied_edges, occupied_nodes)

for idx, pin_label in enumerate(sch.pin_labels):
    px, py = sch.pin_position(pin_label.reference, pin_label.pin_name)
    orient = sch.pin_orientation(pin_label.reference, pin_label.pin_name)
    dx, dy = _outward(orient)
    pin_gp = writer._grid_point(px, py)
    first_step = (pin_gp[0] + int(round(dx)), pin_gp[1] + int(round(dy)))
    first_edge = writer._edge_key(pin_gp, first_step)
    if first_edge in occupied_edges:
        stub_len = 2.54
        lbl_x = _snap(px + dx * stub_len)
        lbl_y = _snap(py + dy * stub_len)
        lbl_length = max(pin_label.length - stub_len, 2.54)
        stub_used = True
    else:
        lbl_x, lbl_y = px, py
        lbl_length = pin_label.length
        stub_used = False
    candidates = writer._pin_label_paths(lbl_x, lbl_y, orient, lbl_length, pin_label.net_name)
    path, rot, jst = candidates[0]
    chosen_idx = 0
    for ci, (candidate, cand_rot, cand_jst) in enumerate(candidates):
        if writer._path_is_clear(candidate, occupied_edges, occupied_nodes):
            path, rot, jst = candidate, cand_rot, cand_jst
            chosen_idx = ci
            break
    if pin_label.net_name == 'S0':
        print('S0 label (idx %d): %s.%s' % (idx, pin_label.reference, pin_label.pin_name))
        print('  stub_used=%s lbl_x=%.2f lbl_y=%.2f lbl_length=%.2f' % (stub_used, lbl_x, lbl_y, lbl_length))
        print('  chosen candidate %d of %d' % (chosen_idx, len(candidates)))
        print('  path: %s' % path)
        print('  rot=%s jst=%s' % (rot, jst))
        segments = list(zip(path, path[1:]))
        print('  segments: %d' % len(segments))
        for si, (start, end) in enumerate(segments):
            print('    seg %d: (%.2f,%.2f)->(%.2f,%.2f)' % (si, start[0], start[1], end[0], end[1]))
    writer._reserve_path(path, occupied_edges, occupied_nodes)
