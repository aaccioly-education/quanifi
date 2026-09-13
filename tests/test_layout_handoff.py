"""The qualified layout moves between groups as a file, never by hand.

Two jobs on independently searched layouts are not comparable -- on IBM, two
runs twelve minutes apart moved one implementation's control success from 0.78
to 0.16. So the campaign pins one layout per vendor, and pinning it used to mean
pasting the same list into four submitters and remembering to. On 2026-08-29 a
calibration job was fired with `Fixed Layout` still blank.

Qualification now publishes the layout it qualified, and every later job in that
vendor's track reads it. A missing file stops the run: the alternative -- falling
back to a fresh search -- is the exact failure the pinning exists to prevent, and
it would be silent.
"""
import pytest

from tools.add_generation2_canvas import hierarchy

FETCH = "org.apache.nifi.processors.standard.FetchFile"


def flatten(group):
    yield group
    for child in group.get("processGroups", []):
        yield from flatten(child)


@pytest.fixture(scope="module")
def groups():
    return {g["name"]: g for g in flatten(hierarchy("root")) if g.get("processors")}


def names(group):
    return {p["name"]: p for p in group["processors"]}


def routed(group, processor, relationship):
    """Destinations for one relationship, or [] if it goes nowhere."""
    return [c["destination"]["id"] for c in group["connections"]
            if c["source"]["id"] == processor["identifier"]
            and relationship in c["selectedRelationships"]]


QUALIFY = ["Qualification — IBM", "Qualification — IQM"]
CONSUME = ["Calibration — IBM", "Calibration — IQM",
           "carry.break — IBM", "carry.break — IQM",
           "gate.remove — IBM", "gate.remove — IQM",
           "rotation.perturb — IBM", "rotation.perturb — IQM"]


@pytest.mark.parametrize("name", QUALIFY)
def test_only_an_accepted_layout_is_published(name, groups):
    """A layout that failed the eligibility gate must never be published."""
    group = groups[name]
    by_name = names(group)
    evaluator = next(p for p in group["processors"] if p["name"].endswith("Eligibility"))
    filename, content = by_name["Layout Filename"], by_name["Layout Content"]
    # `accepted` fans out: the reporter still reports, and the layout is
    # published alongside it. What matters is that `rejected` does not.
    assert filename["identifier"] in routed(group, evaluator, "accepted")
    assert filename["identifier"] not in routed(group, evaluator, "rejected")
    assert routed(group, filename, "success") == [content["identifier"]]
    assert routed(group, content, "success") == [by_name["Publish Layout"]["identifier"]]
    assert filename["properties"]["filename"].endswith(".json")


@pytest.mark.parametrize("name", CONSUME)
def test_every_later_job_pins_the_published_layout(name, groups):
    group = groups[name]
    submitter = next(p for p in group["processors"] if "Batch Submitter" in p["name"])
    assert submitter["properties"]["Fixed Layout"] == "${arithmetic.fixed_layout}"


@pytest.mark.parametrize("name", CONSUME)
def test_a_missing_layout_stops_the_run(name, groups):
    """not.found must be routed. Auto-terminating it resumes the old silence."""
    group = groups[name]
    fetch = names(group)["Fetch Layout"]
    assert fetch["type"] == FETCH
    for relationship in ("not.found", "permission.denied", "failure"):
        assert relationship not in fetch["autoTerminatedRelationships"], relationship
        assert routed(group, fetch, relationship), relationship


@pytest.mark.parametrize("name", CONSUME)
def test_the_layout_is_fetched_before_the_cases_are_built(name, groups):
    """The attribute has to exist before any circuit reaches the submitter."""
    group = groups[name]
    by_name = names(group)
    trigger, fetch = by_name["Arithmetic HW — Trigger"], by_name["Fetch Layout"]
    hoist, source = by_name["Hoist Layout"], by_name["Arithmetic HW — Test Cases"]
    assert routed(group, trigger, "success") == [fetch["identifier"]]
    assert routed(group, fetch, "success") == [hoist["identifier"]]
    assert routed(group, hoist, "matched") == [source["identifier"]]


@pytest.mark.parametrize("name", CONSUME)
def test_the_trigger_no_longer_reaches_the_source_directly(name, groups):
    """The old edge must be gone, or the layout can be bypassed."""
    group = groups[name]
    by_name = names(group)
    assert by_name["Arithmetic HW — Test Cases"]["identifier"] not in routed(
        group, by_name["Arithmetic HW — Trigger"], "success")
