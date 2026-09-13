"""No processor may leave a relationship with nowhere to go.

NiFi refuses to start a processor whose relationship is neither connected nor
auto-terminated, so a single dangling relationship silently disables a whole
lane. It has now happened three times -- `Mutate carry.break @last`, the batch
result expander, and every Generation-2 submitter at once, where deleting the
Generation-1 report processor orphaned `preflight` and left all ten submitters
INVALID. Each time it was found by hand on the canvas, after a window was lost.

RELATIONSHIPS below was read off a running NiFi 2.9.0 (the `relationships`
field of each instantiated processor), not written from memory, because a
wrong entry here would fail builds for a defect that does not exist.
"""
import pytest

from tools.add_generation2_canvas import hierarchy

TRANSFORM = {"success", "failure", "original"}

RELATIONSHIPS = {
    "CirqQuantumArithmetic": TRANSFORM,
    "PennylaneQuantumArithmetic": TRANSFORM,
    "QiskitQuantumArithmetic": TRANSFORM,
    "QuantumBatchResultExpander": TRANSFORM,
    "QuantumMutator": TRANSFORM,
    "QuantumTestCaseSource": TRANSFORM,
    "Generation2EnsembleBuilder": TRANSFORM,
    "Generation2JobAdapter": TRANSFORM,
    "Generation2PseudoOracle": TRANSFORM,
    "Generation2Reporter": TRANSFORM,
    "Generation2TruthEvaluator": TRANSFORM,
    "Generation2CalibrationEvaluator": {"accepted", "rejected", "failure", "original"},
    "QuantumIBMBatchPoller": {"success", "pending", "failure", "original"},
    "QuantumIQMBatchPoller": {"success", "pending", "failure", "original"},
    "QuantumIBMBatchSubmitter": {"submitted", "preflight", "waiting", "failure", "original"},
    "QuantumIQMBatchSubmitter": {"submitted", "preflight", "waiting", "failure", "original"},
    "org.apache.nifi.processors.attributes.UpdateAttribute": {"success"},
    "org.apache.nifi.processors.standard.GenerateFlowFile": {"success"},
    "org.apache.nifi.processors.standard.PutFile": {"success", "failure"},
    "org.apache.nifi.processors.standard.EvaluateJsonPath": {"matched", "unmatched", "failure"},
    "org.apache.nifi.processors.standard.SplitJson": {"split", "original", "failure"},
    "org.apache.nifi.processors.standard.FetchFile": {
        "success", "not.found", "permission.denied", "failure"},
    "org.apache.nifi.processors.standard.ReplaceText": {"success", "failure"},
}

#: RouteOnAttribute's relationships are its dynamic properties plus `unmatched`.
ROUTE_ON_ATTRIBUTE = "org.apache.nifi.processors.standard.RouteOnAttribute"


def flatten(group):
    yield group
    for child in group.get("processGroups", []):
        yield from flatten(child)


def declared(processor):
    """Relationships this processor must route or auto-terminate.

    Python (FlowFileTransform) processors also expose `original`, but NiFi
    terminates it implicitly -- the live instance reports only `preflight` as
    invalid while leaving 68 unrouted `original` relationships alone. Java
    processors get no such treatment: SplitJson's `original` is auto-terminated
    explicitly by the builder and must stay that way.
    """
    if processor["type"] == ROUTE_ON_ATTRIBUTE:
        return set(processor["properties"]) - {"Routing Strategy"} | {"unmatched"}
    names = RELATIONSHIPS[processor["type"]]
    if not processor["type"].startswith("org.apache.nifi."):
        names = names - {"original"}
    return names


@pytest.fixture(scope="module")
def groups():
    return list(flatten(hierarchy("root")))


def test_every_processor_type_is_known(groups):
    """A new processor type must be added above, or it escapes the check."""
    unknown = {p["type"] for g in groups for p in g.get("processors", [])
               if p["type"] != ROUTE_ON_ATTRIBUTE and p["type"] not in RELATIONSHIPS}
    assert unknown == set(), unknown


def test_no_relationship_is_left_dangling(groups):
    offenders = []
    for group in groups:
        used = {}
        for connection in group.get("connections", []):
            used.setdefault(connection["source"]["id"], set()).update(
                connection["selectedRelationships"])
        for processor in group.get("processors", []):
            homeless = (declared(processor)
                        - used.get(processor["identifier"], set())
                        - set(processor["autoTerminatedRelationships"]))
            if homeless:
                offenders.append("%s / %s: %s" % (group["name"], processor["name"],
                                                  ", ".join(sorted(homeless))))
    assert offenders == [], "\n".join(offenders)


def test_the_preflight_costing_is_persisted_not_discarded(groups):
    """Preflight carries the cost model you read before spending QPU time.

    Auto-terminating it would clear the validation error and throw the report
    away, which is the wrong half of the fix.
    """
    submitters = [(g, p) for g in groups for p in g.get("processors", [])
                  if "Batch Submitter" in p["name"]]
    assert len(submitters) == 10
    for group, submitter in submitters:
        assert "preflight" not in submitter["autoTerminatedRelationships"]
        sinks = [c["destination"]["id"] for c in group["connections"]
                 if c["source"]["id"] == submitter["identifier"]
                 and "preflight" in c["selectedRelationships"]]
        assert len(sinks) == 1, group["name"]
        by_id = {p["identifier"]: p for p in group["processors"]}
        assert by_id[sinks[0]]["type"].endswith("PutFile"), group["name"]


def test_preflight_reports_do_not_overwrite_each_other(groups):
    """Ten lanes writing one filename into one directory keeps one report."""
    targets = set()
    for group in groups:
        sinks = [p for p in group.get("processors", [])
                 if p["name"] == "Preflight Cost Report"]
        for sink in sinks:
            trigger = next(p for p in group["processors"]
                           if p["name"] == "Arithmetic HW — Trigger")
            targets.add((sink["properties"]["Directory"],
                         trigger["properties"]["filename"]))
    assert len(targets) == 10, targets


def test_no_circuit_or_batch_failure_is_auto_terminated(groups):
    """Silence here stalls a batch with nothing to look at anywhere.

    On 2026-08-26 a qualification run stopped at 20 of 21 circuits. No queue
    held anything, no bulletin fired, the dead-letter funnel was empty, and the
    submitter waited forever for a circuit that had been auto-terminated out of
    existence. The lanes had been fixed for this; the preparatory groups had
    not, because the wiring was conditional on `operator` rather than on there
    being a funnel to route to.
    """
    offenders = []
    for group in groups:
        if not group.get("funnels"):
            continue
        for processor in group.get("processors", []):
            carries_work = (processor["type"].endswith("QuantumArithmetic")
                            or "Batch Submitter" in processor["name"]
                            or processor["type"] == "QuantumMutator")
            if carries_work and "failure" in processor["autoTerminatedRelationships"]:
                offenders.append("%s / %s" % (group["name"], processor["name"]))
    assert offenders == [], offenders
