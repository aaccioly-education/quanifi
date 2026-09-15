"""The shipped canvas and the executed examples share real processor settings."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pyquil_examples", ROOT / "tools/build_pyquil_examples.py"
)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_canvas_is_current_stopped_connected_and_has_valid_properties():
    snapshot = json.loads((ROOT / "demo/pyquil/pyquil-examples.json").read_text())
    assert snapshot == tool.snapshot()
    group = snapshot["flowContents"]
    all_components = (
        group["processors"] + group["funnels"] + group["connections"] + group["labels"]
    )
    ids = [c["identifier"] for c in all_components]
    assert len(ids) == len(set(ids))
    node_ids = {c["identifier"] for c in group["processors"] + group["funnels"]}
    assert len(group["funnels"]) == 3
    for c in group["connections"]:
        assert c["source"]["id"] in node_ids and c["destination"]["id"] in node_ids
    for p in group["processors"]:
        assert p["scheduledState"] == "ENABLED"
        if p["bundle"]["artifact"] == "python-extensions":
            cls = tool.processor_class(p["type"])
            assert p["bundle"]["version"] == cls.ProcessorDetails.version
            descriptors = {d.name: d for d in cls().getPropertyDescriptors()}
            assert set(p["properties"]) <= set(descriptors)
            for name, value in p["properties"].items():
                choices = descriptors[name].allowable_values
                assert not choices or value in choices
            outgoing = {
                r
                for c in group["connections"]
                if c["source"]["id"] == p["identifier"]
                for r in c["selectedRelationships"]
            }
            assert outgoing | set(p["autoTerminatedRelationships"]) == {
                "success",
                "failure",
                "original",
            }
            assert "failure" in outgoing


def test_run_canvas_examples_and_generate_reports(tmp_path):
    results = tool.execute(tmp_path)
    assert results["grover"]["counts"] == {"10": 256}
    assert float(results["vqe"]["attributes"]["vqe.optimal_value"]) == pytest.approx(
        -1.11803398875, abs=1e-7
    )
    assert float(results["qaoa"]["attributes"]["qaoa.optimal_value"]) == pytest.approx(
        -1, abs=1e-7
    )
    for key, record in results.items():
        saved = json.loads((ROOT / "demo/pyquil" / f"{key}.json").read_text())
        assert record == saved
        assert (tmp_path / f"pyquil-{key}.html").is_file()
        assert (tmp_path / f"{key}.qasm").is_file()
