"""Adding to the canvas must never move what is already on it.

`data_canvas_baseline_positions.json` is the builder's own output, frozen. Any
later change is required to find empty space rather than shift a component that
already had a slot, because re-learning where everything sits costs more than
most changes are worth. The layout publish/fetch stage was added under this
rule and moved nothing.

The baseline is the builder's grid, not a snapshot of the live canvas: on
2026-08-29 eleven builder boxes in `Calibration — IQM` had been dragged off-grid
by hand (x from -1424 to 2360) while the other nine groups matched the grid
exactly, and a rebuild normalises them. Comparing against the live canvas would
have made that pre-existing drift look like damage from whatever change came
next.

Regenerate only when a move is deliberate, and say so in the commit.
"""
import json
from pathlib import Path

import pytest

from tools.add_generation2_canvas import hierarchy

BASELINE = json.loads((Path(__file__).parent / "data_canvas_baseline_positions.json").read_text())


def flatten(group):
    yield group
    for child in group.get("processGroups", []):
        yield from flatten(child)


@pytest.fixture(scope="module")
def built():
    return {g["name"]: {p["name"]: [p["position"]["x"], p["position"]["y"]]
                        for p in g.get("processors", [])}
            for g in flatten(hierarchy("root")) if g.get("processors")}


def test_every_baseline_group_still_exists(built):
    assert set(BASELINE) <= set(built), sorted(set(BASELINE) - set(built))


def test_no_existing_processor_moved(built):
    moved = []
    for group, processors in BASELINE.items():
        for name, position in processors.items():
            now = built.get(group, {}).get(name)
            if now is None:
                moved.append("%s / %s: GONE" % (group, name))
            elif now != position:
                moved.append("%s / %s: %s -> %s" % (group, name, position, now))
    assert moved == [], moved


def test_additions_do_not_overlap_anything(built):
    """New components must land in space that was genuinely free."""
    width, height = 352, 104
    for group, processors in built.items():
        items = list(processors.items())
        for index, (name, (x, y)) in enumerate(items):
            for other, (ox, oy) in items[index + 1:]:
                assert not (abs(x - ox) < width and abs(y - oy) < height), (
                    "%s: %s overlaps %s" % (group, name, other))
