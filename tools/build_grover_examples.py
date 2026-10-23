#!/usr/bin/env python3
"""Generate the Qiskit Grover quickstart canvas.

``simple_snapshot()`` builds the default five-processor canvas written to
``demo/grover/qiskit-grover.json`` (the Docker image's default
``QUANIFI_CANVAS``, and importable into any NiFi): GenerateFlowFile ->
QiskitPhaseOracle -> QiskitGroverOperator -> QiskitAerSimulator ->
QuanifiReport, searching for ``10`` on two qubits with one Grover iteration.
The oracle and operator exchange OpenQASM 2, so either can be swapped for its
Cirq counterpart (docs/guides/INTERCHANGEABLE_GROVER_FLOW.md). Modelled on
``tools/build_qaoa_examples.py``.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from canvas_spacing import FUNNEL_SIZE, PITCH, PROCESSOR_SIZE, centered, grid_xy, stack  # noqa: E402

NAMESPACE = "https://quanifi.local/grover-examples/"
SHOTS = "1024"
SEED = "11"
ENGINE_PROPS = {"Shots": SHOTS, "Random Seed": SEED}
REPORTS_DIR = "reports/quickstart"


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, NAMESPACE + name))


def processor_class(name):
    import _harness  # noqa: F401 - initializes only the NiFi API stubs

    return getattr(importlib.import_module(name), name)


def node(
    key,
    name,
    kind,
    props,
    group,
    x,
    y,
    nifi_version,
    *,
    schedule="0 sec",
    auto_terminated=(),
    dynamic=(),
):
    standard = kind.startswith("org.apache.nifi.")
    version = (
        nifi_version if standard else processor_class(kind).ProcessorDetails.version
    )
    values = (
        {}
        if standard
        else {
            d.name: d.default_value
            for d in processor_class(kind)().getPropertyDescriptors()
            # Never bake a sensitive default (e.g. QuanifiReport's API token)
            # into a committed canvas in plain text.
            if d.default_value not in ("", None) and not getattr(d, "sensitive", False)
        }
    )
    values.update(props)
    property_descriptors = {
        n: {
            "name": n,
            "displayName": n,
            "identifiesControllerService": False,
            "sensitive": False,
            "dynamic": True,
        }
        for n in dynamic
    }
    return {
        "identifier": uid(key),
        "instanceIdentifier": uid(key + "/instance"),
        "groupIdentifier": group,
        "name": name,
        "type": kind,
        "componentType": "PROCESSOR",
        "position": {"x": x, "y": y},
        "comments": "Quanifi quickstart; right-click the group and choose Start.",
        "bundle": {
            "group": "org.apache.nifi",
            "artifact": "nifi-standard-nar" if standard else "python-extensions",
            "version": version,
        },
        "properties": values,
        "propertyDescriptors": property_descriptors,
        "style": {},
        "schedulingPeriod": schedule,
        "schedulingStrategy": "TIMER_DRIVEN",
        "executionNode": "ALL",
        "penaltyDuration": "30 sec",
        "yieldDuration": "1 sec",
        "bulletinLevel": "WARN",
        "runDurationMillis": 0,
        "concurrentlySchedulableTaskCount": 1,
        "autoTerminatedRelationships": list(auto_terminated),
        "scheduledState": "ENABLED",
        "retryCount": 0,
        "retriedRelationships": [],
        "backoffMechanism": "PENALIZE_FLOWFILE",
        "maxBackoffPeriod": "10 mins",
    }


def connection(key, source, destination, group, relationships=("success",)):
    def ref(component):
        return {
            "id": component["identifier"],
            "groupId": group,
            "name": component["name"],
            "type": component["componentType"],
            "instanceIdentifier": component["instanceIdentifier"],
        }

    return {
        "identifier": uid(key),
        "instanceIdentifier": uid(key + "/instance"),
        "groupIdentifier": group,
        "componentType": "CONNECTION",
        "name": ",".join(relationships),
        "source": ref(source),
        "destination": ref(destination),
        "selectedRelationships": list(relationships),
        "labelIndex": 0,
        "zIndex": 0,
        "bends": [],
        "backPressureObjectThreshold": 1000,
        "backPressureDataSizeThreshold": "100 MB",
        "flowFileExpiration": "0 sec",
        "prioritizers": [],
        "loadBalanceStrategy": "DO_NOT_LOAD_BALANCE",
        "loadBalanceCompression": "DO_NOT_COMPRESS",
    }


def _empty_group(gid, name, comments):
    return {
        "identifier": gid,
        "instanceIdentifier": uid(gid + "/instance"),
        "name": name,
        "componentType": "PROCESS_GROUP",
        "position": {"x": 0, "y": 0},
        "comments": comments,
        "processors": [],
        "connections": [],
        "labels": [],
        "funnels": [],
        "processGroups": [],
        "remoteProcessGroups": [],
        "inputPorts": [],
        "outputPorts": [],
        "controllerServices": [],
        "scheduledState": "ENABLED",
        "defaultFlowFileExpiration": "0 sec",
        "defaultBackPressureObjectThreshold": 1000,
        "defaultBackPressureDataSizeThreshold": "100 MB",
        "flowFileConcurrency": "UNBOUNDED",
        "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
        "executionEngine": "INHERITED",
    }


SIMPLE_GROUP_NAME = "Quanifi quickstart — Qiskit Grover"
SIMPLE_FLOW_NAME = "qiskit-grover"
SIMPLE_TARGET = "10"
SIMPLE_ITERATIONS = "1"


def simple_snapshot(nifi_version="2.9.0"):
    """A beginner's five-processor flow: trigger, phase oracle, Grover
    operator, simulate, report."""
    gid = uid("simple/group")
    group = _empty_group(gid, SIMPLE_GROUP_NAME,
                         "Find target 10 with two qubits: a phase oracle, one Grover "
                         "iteration and a local Aer simulation.")
    specs = [
        ("Start here", "org.apache.nifi.processors.standard.GenerateFlowFile",
         {"File Size": "0B", "Batch Size": "1", "Data Format": "Text",
          "Unique FlowFiles": "false"},
         "Right-click and Run Once to send one input. Start the other four processors first."),
        ("Phase oracle", "QiskitPhaseOracle",
         {"Marked State": SIMPLE_TARGET, "Output Format": "qasm2"},
         "Mark the target 10 with a -1 phase on two qubits. Change Marked State here."),
        ("Grover operator", "QiskitGroverOperator",
         {"Num Iterations": SIMPLE_ITERATIONS, "Output Format": "qasm2"},
         "Prepare the uniform superposition and apply one Grover iteration "
         "(oracle + diffuser)."),
        ("Simulate circuit", "QiskitAerSimulator", ENGINE_PROPS,
         "Run the circuit on the local Qiskit Aer simulator with 1024 shots."),
        ("View results", "QuanifiReport",
         {"Flow Name": SIMPLE_FLOW_NAME, "Reports Directory": REPORTS_DIR},
         "Open reports/quickstart/qiskit-grover.html on your host to see the result."),
    ]
    last = len(specs) - 1
    row_y = stack([110])[1]
    for i, (name, kind, props, comment) in enumerate(specs):
        px, py = grid_xy(i, 0, origin=(0, row_y))
        proc = node("simple/" + kind, name, kind, props, gid, px, py,
                    nifi_version, schedule="1 day" if i == 0 else "0 sec",
                    auto_terminated=() if i == 0 else
                    (("original", "success") if i == last else ("original",)))
        proc["comments"] = comment
        group["processors"].append(proc)
    procs = group["processors"]
    group["connections"] = [connection("simple/step-" + str(i), procs[i], procs[i + 1], gid)
                            for i in range(last)]
    # Preserve failed inputs for inspection instead of silently discarding them.
    funnel = {"identifier": uid("simple/failure"),
              "instanceIdentifier": uid("simple/failure/instance"),
              "groupIdentifier": gid, "componentType": "FUNNEL",
              "name": "Failures (inspect the queue)",
              "position": {"x": centered(grid_xy(2, 0)[0], PROCESSOR_SIZE[0], FUNNEL_SIZE[0]),
                           "y": row_y + PITCH[1]}}
    group["funnels"] = [funnel]
    for proc in procs[1:]:
        group["connections"].append(connection("simple/failure/" + proc["type"], proc,
                                               funnel, gid, relationships=("failure",)))
    group["labels"] = [{"identifier": uid("simple/label"),
                        "instanceIdentifier": uid("simple/label/instance"),
                        "groupIdentifier": gid, "componentType": "LABEL",
                        "position": {"x": 0, "y": 0}, "zIndex": 0,
                        "width": 850, "height": 110,
                        "label": "Qiskit Grover: find 10 among four possible states with a "
                                 "phase oracle and one Grover operator. Start the group to run. "
                                 "Open reports/quickstart/qiskit-grover.html. "
                                 "For another run, stop Start here and choose Run Once.",
                        "style": {"background-color": "#eaf2ff", "font-size": "18px"}}]
    return {"flowContents": group, "flowEncodingVersion": "1.0", "parameterContexts": {},
            "externalControllerServices": {}, "parameterProviders": {}}


def python_processor_types(defn):
    """type -> bundle version for every python-extensions processor, walking
    nested process groups."""
    types = {}

    def walk(group):
        for proc in group.get("processors", []):
            bundle = proc.get("bundle", {})
            if bundle.get("artifact") == "python-extensions":
                types[proc["type"]] = bundle.get("version", "")
        for child in group.get("processGroups", []):
            walk(child)

    walk(defn["flowContents"])
    return types


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/grover")
    parser.add_argument("--nifi-version", default="2.9.0")
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "qiskit-grover.json").write_text(
        json.dumps(simple_snapshot(args.nifi_version), indent=2, ensure_ascii=False) + "\n"
    )
    print("Wrote " + str(args.output / "qiskit-grover.json"))


if __name__ == "__main__":
    main()
