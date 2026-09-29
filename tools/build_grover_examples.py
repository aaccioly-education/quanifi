#!/usr/bin/env python3
"""Generate the stopped Quanifi quickstart canvas (Grover 3x3) and run the
same matrix headlessly.

Three Grover builders (Qiskit, Cirq, Qrisp) each feed three counts engines
(QiskitAerSimulator, CirqSimulator, QrispSimulator) over OpenQASM 2. Every
cell feeds a shared QuantumConsensusOracle (K=9) and a shared QuanifiReport;
the oracle's pass/fail verdict also feeds the report and an
AttributesToJSON -> PutFile tail that writes the verdict JSON to
reports/quickstart/results/.

``snapshot()`` builds the flow definition written to
``demo/grover/grover-3x3.json`` (committed, and importable into any NiFi).
``execute()`` runs the same matrix in-process with the nifiapi test stubs, no
NiFi/JVM required. Modelled on ``tools/build_qaoa_examples.py``.
"""
from __future__ import annotations

import argparse
import importlib
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

NAMESPACE = "https://quanifi.local/grover-examples/"
GROUP_NAME = "Quanifi quickstart — Grover 3×3"
MARKED_STATE = "110"
NUM_ITERATIONS = "2"
SHOTS = "1024"
SEED = "11"
BUILDERS = [
    ("QiskitGroverCircuit", "QiskitGrover"),
    ("CirqGroverCircuit", "CirqGrover"),
    ("QrispGroverCircuit", "QrispGrover"),
]
ENGINES = ["QiskitAerSimulator", "CirqSimulator", "QrispSimulator"]
BUILDER_PROPS = {
    "Marked State": "${test.expected}",
    "Num Iterations": NUM_ITERATIONS,
    "Output Format": "qasm2",
}
ENGINE_PROPS = {"Shots": SHOTS, "Random Seed": SEED}
REPORTS_DIR = "reports/quickstart"
RESULTS_DIR = REPORTS_DIR + "/results"
FLOW_NAME = "grover-3x3"
ORACLE_PROPS = {
    "Expected Branches": "9",
    "Flow Name": FLOW_NAME,
    "Reports Directory": REPORTS_DIR,
    "State Directory": REPORTS_DIR + "/tmp/consensus-state",
    "Check Ground Truth": "true",
}
REPORT_PROPS = {"Flow Name": FLOW_NAME, "Reports Directory": REPORTS_DIR}
RESULT_ATTRIBUTES = [
    "assert.verdict",
    "assert.reason",
    "consensus.branches",
    "consensus.majority_top",
    "consensus.majority_count",
    "consensus.dissenters",
    "consensus.max_hellinger",
    "consensus.expected",
    "consensus.branches_json",
    "test.run_id",
    "test.case_id",
    "test.expected",
]
TRIGGER_PROPS = {
    "File Size": "0B",
    "Batch Size": "1",
    "Data Format": "Text",
    "Unique FlowFiles": "false",
    "test.run_id": "${UUID()}",
    "test.case_id": "grover-" + MARKED_STATE,
    "test.expected": MARKED_STATE,
}
DYNAMIC_TRIGGER_PROPERTIES = ("test.run_id", "test.case_id", "test.expected")
MAX_HELLINGER = 0.10


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
            if d.default_value not in ("", None)
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


LABEL_TEXT = (
    "Grover 3×3 — three builders (Qiskit, Cirq, Qrisp) × three simulators (Aer, "
    "Cirq, Qrisp) over OpenQASM 2. Right-click the group → Start: the trigger fires "
    "once, QuantumConsensusOracle votes (expect PASS, every top |110⟩). Results: "
    "reports/quickstart/ on your host. Change the target in the trigger's "
    "test.expected property."
)


def snapshot(nifi_version="2.9.0"):
    gid = uid("group")
    group = _empty_group(
        gid,
        GROUP_NAME,
        "Quanifi quickstart: 3 Grover builders x 3 counts engines, a K=9 "
        "consensus vote and an HTML report. All components start STOPPED.",
    )

    trigger = node(
        "trigger",
        "Start Grover 3×3",
        "org.apache.nifi.processors.standard.GenerateFlowFile",
        TRIGGER_PROPS,
        gid,
        0,
        350,
        nifi_version,
        schedule="1 day",
        dynamic=DYNAMIC_TRIGGER_PROPERTIES,
    )

    builder_nodes = {}
    for i, (btype, _bcomp) in enumerate(BUILDERS):
        builder_nodes[btype] = node(
            "builder/" + btype,
            btype,
            btype,
            BUILDER_PROPS,
            gid,
            470,
            90 + i * 260,
            nifi_version,
            auto_terminated=("original",),
        )

    engine_nodes = {}
    for i, etype in enumerate(ENGINES):
        engine_nodes[etype] = node(
            "engine/" + etype,
            etype,
            etype,
            ENGINE_PROPS,
            gid,
            940,
            90 + i * 260,
            nifi_version,
            auto_terminated=("original",),
        )

    oracle = node(
        "oracle",
        "QuantumConsensusOracle",
        "QuantumConsensusOracle",
        ORACLE_PROPS,
        gid,
        1410,
        350,
        nifi_version,
        auto_terminated=("waiting", "original"),
    )
    report = node(
        "report",
        "QuanifiReport",
        "QuanifiReport",
        REPORT_PROPS,
        gid,
        1880,
        90,
        nifi_version,
        auto_terminated=("success", "original"),
    )
    tojson = node(
        "tojson",
        "Verdict to JSON",
        "org.apache.nifi.processors.standard.AttributesToJSON",
        {
            "Attributes List": ",".join(RESULT_ATTRIBUTES),
            "Destination": "flowfile-content",
            "Include Core Attributes": "false",
            "JSON Handling Strategy": "NESTED",
            "Pretty Print": "true",
        },
        gid,
        1880,
        610,
        nifi_version,
    )
    putfile = node(
        "putfile",
        "Write verdict",
        "org.apache.nifi.processors.standard.PutFile",
        {
            "Directory": RESULTS_DIR,
            "Conflict Resolution Strategy": "replace",
            "Create Missing Directories": "true",
        },
        gid,
        2350,
        610,
        nifi_version,
        auto_terminated=("success",),
    )

    group["processors"] = (
        [trigger]
        + [builder_nodes[t] for t, _ in BUILDERS]
        + [engine_nodes[e] for e in ENGINES]
        + [oracle, report, tojson, putfile]
    )

    connections = []
    for i, (btype, _bcomp) in enumerate(BUILDERS):
        connections.append(
            connection("trigger->" + btype, trigger, builder_nodes[btype], gid)
        )
    for btype, _bcomp in BUILDERS:
        for etype in ENGINES:
            connections.append(
                connection(
                    "{}->{}".format(btype, etype),
                    builder_nodes[btype],
                    engine_nodes[etype],
                    gid,
                )
            )
    for etype in ENGINES:
        connections.append(
            connection(etype + "->oracle", engine_nodes[etype], oracle, gid)
        )
        connections.append(
            connection(etype + "->report", engine_nodes[etype], report, gid)
        )
    connections.append(
        connection(
            "oracle->report", oracle, report, gid, relationships=("pass", "fail")
        )
    )
    connections.append(
        connection(
            "oracle->tojson", oracle, tojson, gid, relationships=("pass", "fail")
        )
    )
    connections.append(connection("tojson->putfile", tojson, putfile, gid))

    funnel = {
        "identifier": uid("failure"),
        "instanceIdentifier": uid("failure/instance"),
        "groupIdentifier": gid,
        "componentType": "FUNNEL",
        "name": "Failures (inspect the queue)",
        "position": {"x": 1410, "y": 900},
    }
    group["funnels"] = [funnel]

    failure_sources = (
        [builder_nodes[t] for t, _ in BUILDERS]
        + [engine_nodes[e] for e in ENGINES]
        + [oracle, report, tojson, putfile]
    )
    for src in failure_sources:
        connections.append(
            connection(
                src["name"] + "->failure",
                src,
                funnel,
                gid,
                relationships=("failure",),
            )
        )

    group["connections"] = connections
    group["labels"] = [
        {
            "identifier": uid("label"),
            "instanceIdentifier": uid("label/instance"),
            "groupIdentifier": gid,
            "componentType": "LABEL",
            "position": {"x": 0, "y": 0},
            "zIndex": 0,
            "width": 2600,
            "height": 65,
            "label": LABEL_TEXT,
            "style": {"background-color": "#eaf2ff", "font-size": "18px"},
        }
    ]

    return {
        "flowContents": group,
        "flowEncodingVersion": "1.0",
        "parameterContexts": {},
        "externalControllerServices": {},
        "parameterProviders": {},
    }


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


def execute(output: Path) -> dict:
    import _harness  # noqa: F401
    from conftest import MockContext, MockFlowFile, result_to_flowfile_merged

    shutil.rmtree(output / "tmp", ignore_errors=True)
    for name in ("grover-3x3.html", "grover-3x3-consensus.html"):
        p = output / name
        if p.exists():
            p.unlink()

    def run(kind, props, flowfile, stage):
        result = processor_class(kind)().transform(MockContext(**props), flowfile)
        if result.relationship != "success":
            raise RuntimeError("{}: {}".format(stage, result.attributes))
        return result

    trigger_ff = MockFlowFile(
        b"",
        {
            "test.run_id": "local",
            "test.case_id": "grover-" + MARKED_STATE,
            "test.expected": MARKED_STATE,
        },
    )

    # Build all 9 cells once; keep each cell's merged FlowFile for both the
    # per-cell report and the oracle vote below.
    cells = []
    cell_flowfiles = []
    for btype, bcomp in BUILDERS:
        b_result = run(btype, BUILDER_PROPS, trigger_ff, "builder " + btype)
        builder_ff = result_to_flowfile_merged(b_result, trigger_ff)
        for etype in ENGINES:
            e_result = run(etype, ENGINE_PROPS, builder_ff, "engine " + etype)
            cell_ff = result_to_flowfile_merged(e_result, builder_ff)
            counts = json.loads(bytes(e_result.contents))
            cells.append(
                {
                    "builder": bcomp,
                    "engine": etype,
                    "top": e_result.attributes.get("sim.top_result"),
                    "top_probability": float(
                        e_result.attributes.get("sim.top_probability", 0.0)
                    ),
                    "counts": counts,
                }
            )
            cell_flowfiles.append(cell_ff)
            run(
                "QuanifiReport",
                {"Flow Name": FLOW_NAME, "Reports Directory": str(output)},
                cell_ff,
                "report for " + bcomp + "/" + etype,
            )

    # Feed all 9 cells to the same oracle slot (State Directory persists the
    # partial vote to disk between calls, the same way NiFi's per-instance
    # process would). Only the 9th call is expected to vote.
    oracle_ctx_props = dict(ORACLE_PROPS)
    oracle_ctx_props["Reports Directory"] = str(output)
    oracle_ctx_props["State Directory"] = str(output / "tmp" / "consensus-state")

    oracle_result = None
    for i, cell_ff in enumerate(cell_flowfiles):
        result = processor_class("QuantumConsensusOracle")().transform(
            MockContext(**oracle_ctx_props), cell_ff
        )
        if i < len(cell_flowfiles) - 1:
            if result.relationship != "waiting":
                raise RuntimeError("oracle branch {}: {}".format(i, result.attributes))
        else:
            if result.relationship not in ("pass", "fail"):
                raise RuntimeError("oracle final branch: {}".format(result.attributes))
            oracle_result = result

    merged_attrs = result_to_flowfile_merged(
        oracle_result, cell_flowfiles[-1]
    ).getAttributes()
    doc = {k: merged_attrs.get(k, "") for k in RESULT_ATTRIBUTES}
    doc["consensus.branches_json"] = json.loads(doc["consensus.branches_json"])

    verdict_ff = result_to_flowfile_merged(oracle_result, cell_flowfiles[-1])
    run(
        "QuanifiReport",
        {"Flow Name": FLOW_NAME, "Reports Directory": str(output)},
        verdict_ff,
        "report for verdict",
    )

    summary = {"cells": cells, "result": doc}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/grover")
    parser.add_argument("--nifi-version", default="2.9.0")
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute the 3x3 matrix locally and print the results table",
    )
    parser.add_argument(
        "--run-output", type=Path, default=ROOT / "reports/quickstart-local"
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "grover-3x3.json").write_text(
        json.dumps(snapshot(args.nifi_version), indent=2, ensure_ascii=False) + "\n"
    )
    print("Wrote {}".format(args.output / "grover-3x3.json"))

    if args.run:
        args.run_output.mkdir(parents=True, exist_ok=True)
        result = execute(args.run_output)
        by_cell = {(c["builder"], c["engine"]): c for c in result["cells"]}
        header = ["builder \\ engine"] + ENGINES
        print(" | ".join(header))
        for _, bcomp in BUILDERS:
            row = [bcomp]
            for etype in ENGINES:
                cell = by_cell.get((bcomp, etype))
                row.append(cell["top"] if cell else "-")
            print(" | ".join(row))
        print(
            "verdict={} branches={} max_hellinger={}".format(
                result["result"]["assert.verdict"],
                result["result"]["consensus.branches"],
                result["result"]["consensus.max_hellinger"],
            )
        )


if __name__ == "__main__":
    main()
