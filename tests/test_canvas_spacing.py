"""Canvas spacing rule (tools/canvas_spacing.py) and every canvas this repo ships."""

import copy
import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import canvas_spacing as cs  # noqa: E402


def proc(identifier, x, y, name="p"):
    return {"identifier": identifier, "name": name, "componentType": "PROCESSOR",
            "position": {"x": x, "y": y}}


def label(identifier, x, y, w, h, text="note"):
    return {"identifier": identifier, "componentType": "LABEL", "label": text,
            "position": {"x": x, "y": y}, "width": w, "height": h}


def group(name="Root", **collections):
    result = {"name": name, "processors": [], "labels": [], "processGroups": [],
              "connections": []}
    result.update(collections)
    return result


def pgroup(identifier, x, y, name="g"):
    return {"identifier": identifier, "name": name, "componentType": "PROCESS_GROUP",
            "position": {"x": x, "y": y}}


def test_user_example_is_compliant():
    g = group(processors=[proc("a", -1424, -24), proc("b", -744, -24), proc("c", -1424, 264)])
    assert cs.spacing_violations({"flowContents": g}) == []


def test_pairs_just_inside_the_rule_are_flagged():
    g = group(processors=[proc("a", -1424, -24), proc("b", -752, -24), proc("c", -1424, 264)])
    assert len(cs.spacing_violations({"flowContents": g})) == 1
    g = group(processors=[proc("a", -1424, -24), proc("b", -744, -24), proc("c", -1424, 256)])
    assert len(cs.spacing_violations({"flowContents": g})) == 1


def test_diagonal_neighbour_needs_only_one_axis():
    g = group(processors=[proc("a", 0, 0), proc("b", 352 + 100, 128 + 160)])
    assert cs.spacing_violations({"flowContents": g}) == []


def test_process_groups_need_group_pitch():
    ok = group(processGroups=[pgroup("a", 0, 0), pgroup("b", *cs.grid_xy(1, 0, cs.GROUP_PITCH))])
    bad = group(processGroups=[pgroup("a", 0, 0), pgroup("b", *cs.grid_xy(1, 0, cs.PITCH))])
    assert cs.spacing_violations({"flowContents": ok}) == []
    assert len(cs.spacing_violations({"flowContents": bad})) == 1


def test_label_pairs_only_need_no_overlap():
    g = group(labels=[label("a", 0, 0, 100, 50), label("b", 100, 0, 100, 50)])
    assert cs.spacing_violations({"flowContents": g}) == []
    g = group(labels=[label("a", 0, 0, 100, 50), label("b", 99, 49, 100, 50)])
    problems = cs.spacing_violations({"flowContents": g})
    assert len(problems) == 1 and "overlap" in problems[0]


def test_label_against_processor_uses_full_rule():
    g = group(processors=[proc("p", 0, 0)], labels=[label("l", 0, -50 - 159, 300, 50)])
    assert len(cs.spacing_violations({"flowContents": g})) == 1
    g["labels"][0]["position"]["y"] = -50 - 160
    assert cs.spacing_violations({"flowContents": g}) == []


def test_nested_groups_are_checked_in_their_own_space():
    child = group("Child", processors=[proc("a", 0, 0), proc("b", 100, 0)])
    root = group("Root", processGroups=[dict(child, identifier="c", componentType="PROCESS_GROUP",
                                             position={"x": 0, "y": 0})])
    problems = cs.spacing_violations({"flowContents": root})
    assert len(problems) == 1 and "Root / Child" in problems[0]


@pytest.mark.parametrize("key", ["flowContents", "rootGroup", "flow", None])
def test_root_group_input_shapes(key):
    g = group(processors=[proc("a", 0, 0), proc("b", 10, 0)])
    flow = g if key is None else {key: g}
    assert len(cs.spacing_violations(flow)) == 1
    with pytest.raises(ValueError):
        cs.root_group({"nothing": 1})


def _loop(bends):
    p = proc("a", 0, 0, "loopy")
    c = {"identifier": "c", "name": "", "selectedRelationships": ["success"], "bends": bends,
         "source": {"id": "a", "name": "loopy"}, "destination": {"id": "a", "name": "loopy"}}
    return {"flowContents": group(processors=[p], connections=[c])}


def test_self_loop_needs_bends():
    assert any("needs 2 bends" in m for m in cs.spacing_violations(_loop([])))
    assert cs.spacing_violations(_loop(cs.self_loop_bends({"x": 0, "y": 0}))) == []


def test_bend_inside_a_box_is_flagged():
    flow = _loop(cs.self_loop_bends({"x": 0, "y": 0}))
    flow["flowContents"]["connections"][0]["bends"][0] = {"x": 10, "y": 10}
    assert any("lies inside" in m for m in cs.spacing_violations(flow))


def test_helpers():
    assert cs.stack([65, 128, 48]) == [0, 232, 520, 728]
    assert cs.grid_xy(2, 1) == (1360, 288)
    assert cs.grid_xy(1, 1, cs.GROUP_PITCH) == (712, 336)
    assert cs.centered(0, 352, 48) == 152
    assert cs.self_loop_bends({"x": 0, "y": 0}) == [{"x": 477, "y": 39}, {"x": 477, "y": 89}]
    assert cs.snap_up(201) == 208 and cs.snap_up(200) == 200


def test_only_restricts_reports():
    g = group(processors=[proc("a", 0, 0), proc("b", 10, 0), proc("c", 5000, 0)])
    assert cs.spacing_violations({"flowContents": g}, only={"c"}) == []
    assert len(cs.spacing_violations({"flowContents": g}, only={"a"})) == 1


def test_transplant_round_trip_and_mismatch():
    old = {"flowContents": group(processors=[proc("a", 0, 0), proc("b", 10, 0)],
                                 labels=[label("l", 0, 0, 10, 10)])}
    new = copy.deepcopy(old)
    new["flowContents"]["processors"][1]["position"] = {"x": 700, "y": 0}
    new["flowContents"]["labels"][0].update(width=99, height=98)
    assert cs.transplant_positions(old, new) == 2
    assert old == new
    assert cs.transplant_positions(old, new) == 0
    new["flowContents"]["processors"].append(proc("z", 0, 0))
    with pytest.raises(ValueError):
        cs.transplant_positions(old, new)


def test_load_canvas_reads_gz(tmp_path):
    path = tmp_path / "f.json.gz"
    with gzip.open(path, "wt") as handle:
        json.dump({"a": 1}, handle)
    assert cs.load_canvas(path) == {"a": 1}


# --- every canvas this repo builds or commits --------------------------------

def _builders():
    import build_grover_examples as grover
    import build_pyquil_examples as pyquil
    import build_qaoa_examples as qaoa

    return [grover.simple_snapshot, pyquil.snapshot, qaoa.lanes_snapshot, qaoa.nxm_snapshot]


@pytest.mark.parametrize("index", range(4))
def test_builder_snapshots_are_compliant(index):
    assert cs.spacing_violations(_builders()[index]()) == []


def _committed_canvases():
    try:
        out = subprocess.run(["git", "ls-files", "demo"], cwd=ROOT, capture_output=True,
                             text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return []
    found = []
    for name in out:
        path = ROOT / name
        if path.suffix == ".json" and path.is_file() and "flowContents" in path.read_text():
            found.append(name)
    return found


@pytest.mark.parametrize("name", _committed_canvases() or [pytest.param("", marks=pytest.mark.skip("git unavailable"))])
def test_committed_demo_canvases_are_compliant(name):
    assert cs.spacing_violations(cs.load_canvas(ROOT / name)) == []


def _empty_flow(path):
    root = {"identifier": "r", "instanceIdentifier": "ri", "name": "Root", "processors": [],
            "connections": [], "labels": [], "funnels": [], "processGroups": []}
    with gzip.open(path, "wt") as handle:
        json.dump({"rootGroup": root}, handle)
    return path


def test_add_flow_chain(tmp_path):
    import add_flow

    path = _empty_flow(tmp_path / "flow.json.gz")
    add_flow.add_chain(path, add_flow.QISKIT_GROVER_CHAIN, 456.0, 200.0)
    assert cs.spacing_violations(cs.load_canvas(path)) == []
    with pytest.raises(ValueError):
        add_flow.add_chain(path, add_flow.QISKIT_GROVER_CHAIN, 0.0, 0.0, h_gap=536.0)


def test_add_vqe_flow(tmp_path):
    import add_vqe_flow

    path = _empty_flow(tmp_path / "flow.json.gz")
    assert add_vqe_flow.main(path) == 0
    assert cs.spacing_violations(cs.load_canvas(path)) == []


def test_add_datadriven_flow(tmp_path):
    import add_datadriven_flow

    path = _empty_flow(tmp_path / "flow.json.gz")
    assert add_datadriven_flow.build(path)
    assert cs.spacing_violations(cs.load_canvas(path)) == []


def test_add_qrisp_components_group(tmp_path):
    import add_qrisp_components_group as tool

    path = tmp_path / "new.json.gz"
    tool.add_qrisp_components_group(path, "2.9.0")
    assert cs.spacing_violations(cs.load_canvas(path)) == []


def test_add_all_components_group():
    import add_all_components_group as tool

    nodes, labels = tool.build_group("gid", tool.categorise(tool.discover()))
    flow = {"processors": nodes, "labels": labels}
    assert cs.spacing_violations(flow) == []
