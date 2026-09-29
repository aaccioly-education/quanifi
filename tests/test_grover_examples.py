"""The shipped Grover 3x3 quickstart canvas matches the generator, and the
generator's headless execution actually runs the demo matrix."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "build_grover_examples", ROOT / "tools/build_grover_examples.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_snapshot_is_current():
    snapshot = json.loads((ROOT / "demo/grover/grover-3x3.json").read_text())
    assert snapshot == tool.snapshot()


def test_snapshot_structure():
    snapshot = tool.snapshot()
    group = snapshot["flowContents"]

    python_procs = [
        p for p in group["processors"] if p["bundle"]["artifact"] == "python-extensions"
    ]
    assert len(group["processors"]) == 11
    assert len(python_procs) == 8
    assert len(group["connections"]) == 31
    assert len(group["funnels"]) == 1
    assert len(group["labels"]) == 1

    all_components = (
        group["processors"] + group["funnels"] + group["connections"] + group["labels"]
    )
    ids = [c["identifier"] for c in all_components]
    assert len(ids) == len(set(ids))

    node_ids = {c["identifier"] for c in group["processors"] + group["funnels"]}
    for c in group["connections"]:
        assert c["source"]["id"] in node_ids
        assert c["destination"]["id"] in node_ids

    for p in group["processors"]:
        assert p["scheduledState"] != "RUNNING"

    assert group["name"] == tool.GROUP_NAME

    by_name = {p["name"]: p for p in group["processors"]}
    trigger = by_name["Start Grover 3×3"]
    assert trigger["schedulingPeriod"] == "1 day"
    for p in group["processors"]:
        if p is not trigger:
            assert p["schedulingPeriod"] == "0 sec"

    dynamic_names = {
        n for n, d in trigger["propertyDescriptors"].items() if d.get("dynamic") is True
    }
    assert dynamic_names == set(tool.DYNAMIC_TRIGGER_PROPERTIES)


def test_python_processors_match_classes():
    snapshot = tool.snapshot()
    group = snapshot["flowContents"]
    python_procs = [
        p for p in group["processors"] if p["bundle"]["artifact"] == "python-extensions"
    ]

    outgoing_by_proc = {}
    autoterm_by_proc = {}
    for p in python_procs:
        outgoing_by_proc[p["identifier"]] = set()
        autoterm_by_proc[p["identifier"]] = set(p["autoTerminatedRelationships"])
    for c in group["connections"]:
        src = c["source"]["id"]
        if src in outgoing_by_proc:
            outgoing_by_proc[src].update(c["selectedRelationships"])

    funnel_id = group["funnels"][0]["identifier"]
    proc_by_id = {p["identifier"]: p for p in python_procs}

    for p in python_procs:
        cls = tool.processor_class(p["type"])
        assert p["bundle"]["version"] == cls.ProcessorDetails.version

        instance = cls()
        descriptor_names = {d.name for d in instance.getPropertyDescriptors()}
        for prop_name, value in p["properties"].items():
            assert prop_name in descriptor_names
        by_name = {d.name: d for d in instance.getPropertyDescriptors()}
        for prop_name, value in p["properties"].items():
            allowed = by_name[prop_name].allowable_values
            if allowed:
                assert value in allowed or value.startswith("${")

        outgoing = outgoing_by_proc[p["identifier"]]
        autoterm = autoterm_by_proc[p["identifier"]]
        if p["type"] == "QuantumConsensusOracle":
            expected = {"pass", "fail", "waiting", "failure", "original"}
        else:
            expected = {"success", "failure", "original"}
        assert (outgoing | autoterm) == expected

        # failure always drains to the funnel (directly, or via autoTerminate
        # for the standard tail processors which are checked separately).
        failure_conns = [
            c
            for c in group["connections"]
            if c["source"]["id"] == p["identifier"]
            and "failure" in c["selectedRelationships"]
        ]
        for c in failure_conns:
            assert c["destination"]["id"] == funnel_id

    # each builder -> 3 distinct engines
    builder_ids = {
        proc_by_id[pid]["type"]: pid
        for pid in proc_by_id
        if proc_by_id[pid]["type"] in dict(tool.BUILDERS)
    }
    engine_ids = {
        proc_by_id[pid]["type"]: pid
        for pid in proc_by_id
        if proc_by_id[pid]["type"] in tool.ENGINES
    }
    for btype, bid in builder_ids.items():
        dests = {
            c["destination"]["id"]
            for c in group["connections"]
            if c["source"]["id"] == bid and "success" in c["selectedRelationships"]
        }
        assert dests == set(engine_ids.values())

    # each engine success -> both the oracle and the report
    oracle_id = next(
        p["identifier"]
        for p in group["processors"]
        if p["type"] == "QuantumConsensusOracle"
    )
    report_id = next(
        p["identifier"] for p in group["processors"] if p["type"] == "QuanifiReport"
    )
    for etype, eid in engine_ids.items():
        dests = {
            c["destination"]["id"]
            for c in group["connections"]
            if c["source"]["id"] == eid and "success" in c["selectedRelationships"]
        }
        assert dests == {oracle_id, report_id}


def test_standard_processor_properties():
    snapshot = tool.snapshot()
    group = snapshot["flowContents"]
    by_name = {p["name"]: p for p in group["processors"]}

    tojson = by_name["Verdict to JSON"]
    assert tojson["properties"] == {
        "Attributes List": ",".join(tool.RESULT_ATTRIBUTES),
        "Destination": "flowfile-content",
        "Include Core Attributes": "false",
        "JSON Handling Strategy": "NESTED",
        "Pretty Print": "true",
    }

    putfile = by_name["Write verdict"]
    assert putfile["properties"] == {
        "Directory": tool.RESULTS_DIR,
        "Conflict Resolution Strategy": "replace",
        "Create Missing Directories": "true",
    }


def test_canvas_types_are_in_processor_list():
    """Every Python processor used by the demo must ship in the runtime image."""
    qb_spec = importlib.util.spec_from_file_location(
        "quanifi_build", ROOT / "docker/nifi/quanifi_build.py"
    )
    qb = importlib.util.module_from_spec(qb_spec)
    qb_spec.loader.exec_module(qb)

    snapshot = tool.snapshot()
    canvas_types = set(tool.python_processor_types(snapshot))
    processor_list = set(
        qb.read_processor_list(
            str(ROOT / "docker/processors.txt"), ROOT / "nifi_extensions"
        )
    )
    assert canvas_types <= processor_list


def test_execute_matrix(tmp_path):
    result = tool.execute(tmp_path)

    assert len(result["cells"]) == 9
    for cell in result["cells"]:
        assert cell["top"] == "110"
        assert cell["top_probability"] >= 0.9

    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    import quickstart_smoke  # noqa: E402

    assert quickstart_smoke.evaluate_result(result["result"]) == []

    html = (tmp_path / "grover-3x3.html").read_text()
    assert html.count('class="run-card"') == 10
    assert (tmp_path / "grover-3x3-consensus.html").exists()
