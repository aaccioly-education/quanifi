"""The documentation group: every processor, once, and none of them a ghost.

This group exists for screenshots, so it is easy to think nothing can go wrong
with it. Two things can. A bundle version that does not match the class's
ProcessorDetails makes NiFi fail to resolve the type and the component renders
as a ghost -- present in the flow, unusable on the canvas, and not obviously
broken in a screenshot. And an auto-terminated relationship the processor does
not actually declare is a validation error on a component that was added
precisely to be photographed looking fine.

Both are asserted against the real processor classes rather than a hardcoded
list, so adding a processor to nifi_extensions/ and forgetting to re-run the
tool fails here.
"""

import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import add_all_components_group as tool


def _processor_classes():
    """Every processor class in nifi_extensions, straight from the source tree."""
    classes = {}
    for path in sorted((ROOT / "nifi_extensions").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        module = importlib.import_module(path.stem)
        for name, obj in vars(module).items():
            if not inspect.isclass(obj) or obj.__module__ != module.__name__:
                continue
            details = getattr(obj, "ProcessorDetails", None)
            if details is not None and hasattr(details, "version"):
                classes[name] = obj
    return classes


def test_group_holds_every_processor_exactly_once():
    discovered = tool.categorise(tool.discover())
    nodes, labels = tool.build_group("gid", discovered)

    expected = set(_processor_classes())
    placed = [n["type"] for n in nodes]

    assert set(placed) == expected
    assert len(placed) == len(set(placed)), "a processor was placed twice"
    assert labels, "the blocks are unlabelled, which defeats the purpose"


def test_bundle_version_matches_the_class():
    """A mismatched version is a ghost component, not an error."""
    discovered = tool.categorise(tool.discover())
    nodes, _ = tool.build_group("gid", discovered)
    classes = _processor_classes()

    for node in nodes:
        declared = classes[node["type"]].ProcessorDetails.version
        assert node["bundle"]["version"] == declared, node["type"]


def test_auto_terminated_relationships_exist_on_the_processor():
    """NiFi validates these names; an invented one shows as a config error."""
    discovered = tool.categorise(tool.discover())
    nodes, _ = tool.build_group("gid", discovered)
    classes = _processor_classes()

    for node in nodes:
        cls = classes[node["type"]]
        instance = cls()
        if hasattr(instance, "getRelationships"):
            # NiFi contributes `failure` on top of whatever the processor declares
            declared = {r.name for r in instance.getRelationships()} | {"failure"}
        else:
            declared = set(tool.DEFAULT_RELATIONSHIPS)
        assert declared, node["type"]
        assert set(node["autoTerminatedRelationships"]) <= declared, node["type"]
        assert set(node["autoTerminatedRelationships"]) == declared, (
            "%s would show a validation warning in the screenshot" % node["type"])


def test_no_property_is_set_to_the_empty_string():
    """An empty default means unset; writing it back fails typed validation."""
    discovered = tool.categorise(tool.discover())
    nodes, _ = tool.build_group("gid", discovered)
    for node in nodes:
        for name, value in node["properties"].items():
            assert value != "", "%s.%s" % (node["type"], name)


def test_nothing_is_connected_or_running():
    discovered = tool.categorise(tool.discover())
    nodes, _ = tool.build_group("gid", discovered)
    assert all(n["scheduledState"] in tool.VALID_SCHEDULED_STATES for n in nodes)
    assert all(n["scheduledState"] != "RUNNING" for n in nodes), (
        "a documentation group must not schedule anything")


def test_every_processor_lands_in_a_category():
    discovered = tool.categorise(tool.discover())
    assert all(p["category"] for p in discovered)
    uncategorised = [p["name"] for p in discovered if p["category"] == "Other"]
    assert not uncategorised, (
        "add these to docs/COMPONENTS.md or to the fallback classifier: %s"
        % uncategorised)
