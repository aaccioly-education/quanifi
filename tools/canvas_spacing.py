"""Canvas spacing rule for NiFi flow definitions.

Rule: two components that are direct children of the same process group are
compared by their boxes ``[x, x+w) x [y, y+h)``.  With the edge gaps ``gx``
and ``gy`` (negative when the boxes overlap on that axis) a pair is compliant
when ``gx >= 328`` OR ``gy >= 160``; equivalently, grow every box by 164 px
left and right and by 80 px top and bottom and require no overlap.  Each
nested process group is checked in its own coordinate space.

Exception: two labels only need to not overlap (``gx >= 0`` or ``gy >= 0``).
A label against any non-label uses the full rule.  Two cheap bend checks are
included: a self-loop connection needs at least two bends, and no bend may lie
strictly inside a component box.

Sizes come from the NiFi 2.9.0 UI bundle
(``nifi-ui-2.9.0.war/webapp``): processor 352x128, process group and remote
process group 384x176, port 240x48 (240x80 when remote access is allowed),
funnel 48x48, snap 8, self-loop bends at ``(x+w+125, y+h/2 -/+ 25)``.

Canonical copy: ``Quanifi/tools/canvas_spacing.py`` (stdlib only).  Byte
identical vendored copies live in ``quanifi-experiments/tools/`` and
``quanifi-qats/experiments/qats_density_vqe/``; edit only the canonical one.
"""
from __future__ import annotations

import gzip
import json
from collections import namedtuple
from pathlib import Path

SNAP = 8
MIN_GAP_X = 328
MIN_GAP_Y = 160

PROCESSOR_SIZE = (352, 128)
PROCESS_GROUP_SIZE = (384, 176)
PORT_SIZE = (240, 48)
REMOTE_PORT_SIZE = (240, 80)
FUNNEL_SIZE = (48, 48)
SIZES = {
    "PROCESSOR": PROCESSOR_SIZE,
    "PROCESS_GROUP": PROCESS_GROUP_SIZE,
    "REMOTE_PROCESS_GROUP": PROCESS_GROUP_SIZE,
    "INPUT_PORT": PORT_SIZE,
    "OUTPUT_PORT": PORT_SIZE,
    "FUNNEL": FUNNEL_SIZE,
}

# Grid pitch: groups holding only processors/ports/funnels, and groups whose
# grid also holds process groups.
PITCH = (680, 288)
GROUP_PITCH = (712, 336)

SELF_LOOP_X_OFFSET = 125
SELF_LOOP_Y_OFFSET = 25

COLLECTIONS = (
    ("processors", "PROCESSOR"),
    ("processGroups", "PROCESS_GROUP"),
    ("remoteProcessGroups", "REMOTE_PROCESS_GROUP"),
    ("inputPorts", "INPUT_PORT"),
    ("outputPorts", "OUTPUT_PORT"),
    ("funnels", "FUNNEL"),
    ("labels", "LABEL"),
)

Box = namedtuple("Box", "kind name identifier x y w h")


def snap_up(value, step=SNAP):
    """Round ``value`` up to a multiple of ``step``."""
    return int(-(-value // step) * step)


def grid_xy(col, row, pitch=PITCH, origin=(0, 0)):
    return (origin[0] + col * pitch[0], origin[1] + row * pitch[1])


def centered(start, outer, inner):
    """Start coordinate of an ``inner``-long span centred in ``outer``."""
    return start + (outer - inner) // 2


def stack(sizes, gap=MIN_GAP_Y, origin=0):
    """Top coordinates for items of the given heights stacked with ``gap``.

    Returns ``len(sizes) + 1`` values; the last is where the next item starts.
    """
    starts = [origin]
    for size in sizes:
        starts.append(snap_up(starts[-1] + size + gap))
    return starts


def self_loop_bends(position, size=PROCESSOR_SIZE):
    x = position["x"] + size[0] + SELF_LOOP_X_OFFSET
    mid = position["y"] + size[1] / 2
    return [{"x": x, "y": mid - SELF_LOOP_Y_OFFSET}, {"x": x, "y": mid + SELF_LOOP_Y_OFFSET}]


def size_of(component, kind):
    if kind == "LABEL":
        return (component.get("width", 0) or 0, component.get("height", 0) or 0)
    if kind in ("INPUT_PORT", "OUTPUT_PORT") and component.get("allowRemoteAccess"):
        return REMOTE_PORT_SIZE
    return SIZES[kind]


def _label_name(component):
    text = str(component.get("label") or "")
    return (text.splitlines() or [""])[0][:40]


def boxes(group):
    result = []
    for key, kind in COLLECTIONS:
        for component in group.get(key) or []:
            position = component.get("position") or {}
            w, h = size_of(component, kind)
            name = _label_name(component) if kind == "LABEL" else component.get("name", "")
            result.append(Box(kind, name, component.get("identifier"),
                              position.get("x", 0), position.get("y", 0), w, h))
    return result


def edge_gaps(a, b):
    gx = max(b.x - (a.x + a.w), a.x - (b.x + b.w))
    gy = max(b.y - (a.y + a.h), a.y - (b.y + b.h))
    return gx, gy


def too_close(a, b):
    gx, gy = edge_gaps(a, b)
    if a.kind == "LABEL" and b.kind == "LABEL":
        return gx < 0 and gy < 0
    return gx < MIN_GAP_X and gy < MIN_GAP_Y


def root_group(flow):
    for key in ("flowContents", "rootGroup", "flow"):
        value = flow.get(key)
        if isinstance(value, dict) and any(k in value for k, _ in COLLECTIONS):
            return value
    if any(k in flow for k, _ in COLLECTIONS):
        return flow
    raise ValueError("no process group found in flow definition")


def walk_groups(group, path=None):
    path = path or group.get("name", "")
    yield path, group
    for child in group.get("processGroups") or []:
        yield from walk_groups(child, path + " / " + child.get("name", ""))


def _connection_name(connection):
    return connection.get("name") or ",".join(connection.get("selectedRelationships") or [])


def spacing_violations(flow, *, only=None, check_bends=True):
    only = None if only is None else set(only)
    problems = []
    for path, group in walk_groups(root_group(flow)):
        items = boxes(group)
        for i, a in enumerate(items):
            for b in items[i + 1:]:
                if only is not None and a.identifier not in only and b.identifier not in only:
                    continue
                if not too_close(a, b):
                    continue
                gx, gy = edge_gaps(a, b)
                if a.kind == "LABEL" and b.kind == "LABEL":
                    tail = "overlap (horizontal gap %g, vertical gap %g)" % (gx, gy)
                else:
                    tail = ("are too close (horizontal gap %g < %d, vertical gap %g < %d)"
                            % (gx, MIN_GAP_X, gy, MIN_GAP_Y))
                problems.append("%s: %s %r at (%g,%g) and %s %r at (%g,%g) %s" % (
                    path, a.kind, a.name, a.x, a.y, b.kind, b.name, b.x, b.y, tail))
        if not check_bends:
            continue
        for connection in group.get("connections") or []:
            source = connection.get("source") or {}
            destination = connection.get("destination") or {}
            if only is not None and connection.get("identifier") not in only \
                    and source.get("id") not in only:
                continue
            bends = connection.get("bends") or []
            name = _connection_name(connection)
            if source.get("id") and source.get("id") == destination.get("id") and len(bends) < 2:
                problems.append("%s: self-loop connection %r on %r needs 2 bends"
                                % (path, name, source.get("name")))
            for bend in bends:
                bx, by = bend.get("x", 0), bend.get("y", 0)
                for box in items:
                    if box.x < bx < box.x + box.w and box.y < by < box.y + box.h:
                        problems.append("%s: bend (%g,%g) of connection %r lies inside %s %r"
                                        % (path, bx, by, name, box.kind, box.name))
    return problems


def load_canvas(path):
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _components(group):
    for _, g in walk_groups(group):
        for key, _kind in COLLECTIONS:
            for component in g.get(key) or []:
                yield component
        for connection in g.get("connections") or []:
            yield connection


def positions_by_id(flow):
    return {
        component["identifier"]: (component["position"]["x"], component["position"]["y"])
        for _, g in walk_groups(root_group(flow))
        for key, kind in COLLECTIONS
        for component in g.get(key) or []
        if "position" in component
    }


def transplant_positions(target, source):
    """Copy positions (label sizes, connection bends) from ``source`` into
    ``target`` in place, matching by identifier.  Returns changed components."""
    t_by = {c["identifier"]: c for c in _components(root_group(target))}
    s_by = {c["identifier"]: c for c in _components(root_group(source))}
    if set(t_by) != set(s_by):
        only_t = sorted(set(t_by) - set(s_by))[:5]
        only_s = sorted(set(s_by) - set(t_by))[:5]
        raise ValueError("identifier sets differ: only in target %s, only in source %s"
                         % (only_t, only_s))
    changed = 0
    for identifier, component in t_by.items():
        other = s_by[identifier]
        before = (component.get("position"), component.get("width"),
                  component.get("height"), component.get("bends"))
        if "position" in other:
            component["position"] = dict(other["position"])
        if component.get("componentType") == "LABEL":
            for key in ("width", "height"):
                if key in other:
                    component[key] = other[key]
        if component.get("componentType") == "CONNECTION" or "bends" in other:
            if "bends" in other:
                component["bends"] = [dict(b) for b in other["bends"]]
        after = (component.get("position"), component.get("width"),
                 component.get("height"), component.get("bends"))
        if before != after:
            changed += 1
    return changed
