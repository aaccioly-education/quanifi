"""M6: the "Qrisp Components & Textbook Algorithms" canvas tool
(``tools/add_qrisp_components_group.py``) stays wired to real processor
settings, and its three QAOA pipelines (Max Clique, Max Independent Set,
Portfolio Rebalancing) chain QrispQAOA -> QrispSimulator ->
QuantumQAOAEvaluator -> QuanifiReport -- the same train/sample/score split
every QAOA processor uses since the M5 clean break. This only exercises the
pipeline *spec* the tool builds (``build_pipeline_spec``); it is never run
against a live flow.
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import add_qrisp_components_group as tool

QAOA_PIPELINE_IDS = ["max_clique", "max_independent_set", "portfolio_rebalancing"]


def _processor_class(type_name):
    module = importlib.import_module(type_name)
    return getattr(module, type_name)


def _python_steps():
    """Every step of every pipeline whose bundle is a Python extension."""
    for pipeline in tool.build_pipeline_spec("2.9.0"):
        for step in pipeline["steps"]:
            if step["bundle"]["artifact"] == "python-extensions":
                yield pipeline, step


def test_bundle_version_matches_the_class():
    """A mismatched version is a ghost component on import, not an error."""
    for _pipeline, step in _python_steps():
        cls = _processor_class(step["type"])
        assert step["bundle"]["version"] == cls.ProcessorDetails.version, step["name"]


def test_property_names_are_declared_descriptors():
    for _pipeline, step in _python_steps():
        cls = _processor_class(step["type"])
        descriptors = {d.name for d in cls().getPropertyDescriptors()}
        assert set(step["properties"]) <= descriptors, step["name"]


def test_allowable_values_are_honoured():
    for _pipeline, step in _python_steps():
        cls = _processor_class(step["type"])
        descriptors = {d.name: d for d in cls().getPropertyDescriptors()}
        for name, value in step["properties"].items():
            choices = descriptors[name].allowable_values
            assert not choices or value in choices, (step["name"], name, value)


def test_qraoa_step_no_longer_has_a_shots_property():
    """The M5 clean break removed Shots from every QAOA solver; a stale
    Shots entry here would be silently ignored by the real processor
    (read_properties only reads declared descriptors), masking the fact
    that this canvas tool was not updated alongside the solver."""
    for pipeline in tool.build_pipeline_spec("2.9.0"):
        if pipeline["id"] not in QAOA_PIPELINE_IDS:
            continue
        for step in pipeline["steps"]:
            if step["type"] == "QrispQAOA":
                assert "Shots" not in step["properties"], pipeline["id"]
                assert "Random Seed" in step["properties"], pipeline["id"]


@pytest.mark.parametrize("pipeline_id", QAOA_PIPELINE_IDS)
def test_qaoa_pipeline_chains_solver_simulator_evaluator_report(pipeline_id):
    pipelines = {p["id"]: p for p in tool.build_pipeline_spec("2.9.0")}
    types = [step["type"] for step in pipelines[pipeline_id]["steps"]]
    assert "QrispQAOA" in types, pipeline_id
    i = types.index("QrispQAOA")
    assert types[i : i + 4] == [
        "QrispQAOA",
        "QrispSimulator",
        "QuantumQAOAEvaluator",
        "QuanifiReport",
    ], pipeline_id
