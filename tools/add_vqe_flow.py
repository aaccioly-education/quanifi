"""One-shot: append the decomposed VQE example pipeline + a pink section header
to the NiFi canvas (conf/flow.json.gz). Run only with NiFi stopped.

Pipeline: StartVQE(GenerateFlowFile) -> QiskitHamiltonian -> QiskitAnsatz
          -> QiskitVQE -> QuanifiReport

Idempotent guard: refuses to run twice (checks for the pink header label).
"""
import gzip
import json
import uuid
import argparse
from pathlib import Path

FLOW = None
GROUP = "22f0b342-2baf-3551-b141-115bd78a1933"
REPORTS_DIR = "reports"
HAM = "Z 0 + Z 1 + 0.5 X 0 X 1"

PY_BUNDLE = {"group": "org.apache.nifi", "artifact": "python-extensions", "version": "0.1.0"}
STD_BUNDLE = {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "2.9.0"}


def nid():
    return str(uuid.uuid4())


def base_proc(name, ptype, bundle, props, autoterm, sched="0 sec", rundur=25):
    return {
        "identifier": nid(),
        "instanceIdentifier": nid(),
        "name": name,
        "comments": "",
        "position": {"x": 0.0, "y": 0.0},
        "type": ptype,
        "bundle": bundle,
        "properties": props,
        "propertyDescriptors": {},
        "style": {},
        "schedulingPeriod": sched,
        "schedulingStrategy": "TIMER_DRIVEN",
        "executionNode": "ALL",
        "penaltyDuration": "30 sec",
        "yieldDuration": "1 sec",
        "bulletinLevel": "WARN",
        "runDurationMillis": rundur,
        "concurrentlySchedulableTaskCount": 1,
        "autoTerminatedRelationships": autoterm,
        # flow.json.gz scheduledState accepts only ENABLED/DISABLED/RUNNING.
        # ENABLED = present and valid but not running; the user starts it on the canvas.
        "scheduledState": "ENABLED",
        "retryCount": 10,
        "retriedRelationships": [],
        "backoffMechanism": "PENALIZE_FLOWFILE",
        "maxBackoffPeriod": "10 mins",
        "componentType": "PROCESSOR",
        "groupIdentifier": GROUP,
    }


def conn(src, dst):
    def ref(p):
        return {"id": p["identifier"], "type": "PROCESSOR", "groupId": GROUP,
                "name": p["name"], "comments": "", "instanceIdentifier": p["instanceIdentifier"]}
    return {
        "identifier": nid(), "instanceIdentifier": nid(), "name": "",
        "source": ref(src), "destination": ref(dst),
        "labelIndex": 0, "zIndex": 0, "selectedRelationships": ["success"],
        "backPressureObjectThreshold": 10000, "backPressureDataSizeThreshold": "1 GB",
        "flowFileExpiration": "0 sec", "prioritizers": [], "bends": [],
        "loadBalanceStrategy": "DO_NOT_LOAD_BALANCE", "partitioningAttribute": "",
        "loadBalanceCompression": "DO_NOT_COMPRESS", "componentType": "CONNECTION",
        "groupIdentifier": GROUP,
    }


def label(x, y, w, h, text, style):
    return {"identifier": nid(), "instanceIdentifier": nid(),
            "position": {"x": float(x), "y": float(y)}, "label": text, "zIndex": 1,
            "width": float(w), "height": float(h), "style": style,
            "componentType": "LABEL", "groupIdentifier": GROUP}


def main(flow):
    with gzip.open(flow) as f:
        d = json.load(f)
    rg = d["rootGroup"]

    if any("VARIATIONAL EXAMPLES" in l.get("label", "") for l in rg["labels"]):
        print("ABORT: VARIATIONAL EXAMPLES section already present. Nothing changed.")
        return 1

    Y = 4720.0
    start = base_proc("StartVQE", "org.apache.nifi.processors.standard.GenerateFlowFile",
                      STD_BUNDLE,
                      {"File Size": "0B", "Batch Size": "1", "Unique FlowFiles": "false",
                       "Character Set": "UTF-8", "Data Format": "Text"},
                      [], sched="30 sec", rundur=0)
    start["position"] = {"x": 200.0, "y": Y}

    ham = base_proc("QiskitHamiltonian", "QiskitHamiltonian", PY_BUNDLE,
                    {"Hamiltonian": HAM, "Num Qubits": "0"}, ["original", "failure"])
    ham["position"] = {"x": 840.0, "y": Y}

    ans = base_proc("QiskitAnsatz", "QiskitAnsatz", PY_BUNDLE,
                    {"Ansatz Type": "efficient_su2", "Reps": "2",
                     "Entanglement": "full", "Num Qubits": "2"}, ["original", "failure"])
    ans["position"] = {"x": 1480.0, "y": Y}

    vqe = base_proc("QiskitVQE", "QiskitVQE", PY_BUNDLE,
                    {"Hamiltonian": HAM, "Ansatz Type": "efficient_su2", "Reps": "2",
                     "Entanglement": "full", "Optimizer": "COBYLA", "Max Iterations": "100",
                     "Shots": "1024", "Initial Parameters": "random"}, ["original", "failure"])
    vqe["position"] = {"x": 2120.0, "y": Y}

    rep = base_proc("QuanifiReport", "QuanifiReport", PY_BUNDLE,
                    {"Flow Name": "qiskit-vqe-decomposed", "Reports Directory": REPORTS_DIR},
                    ["success", "failure"])
    rep["position"] = {"x": 2760.0, "y": Y}

    procs = [start, ham, ans, vqe, rep]
    conns = [conn(start, ham), conn(ham, ans), conn(ans, vqe), conn(vqe, rep)]

    header = label(80, 4500, 3000, 90,
                   "⬡  VARIATIONAL EXAMPLES  ⬡  —  Decomposed VQE: "
                   "Hamiltonian → Ansatz → Solver → Report",
                   {"background-color": "#880e4f", "border-color": "#ec407a",
                    "font-color": "#ffffff", "font-size": "16px"})
    sub = label(80, 4610, 3000, 70,
                "12  QiskitHamiltonian → QiskitAnsatz → QiskitVQE → QuanifiReport "
                "— variational ground-state energy of Z 0 + Z 1 + 0.5 X 0 X 1. "
                "Start StartVQE to run (first run pip-installs qiskit-algorithms, ~30s).",
                {"background-color": "#3a0a23", "border-color": "#ec407a",
                 "font-color": "#f8bbd0", "font-size": "11px"})

    rg["processors"].extend(procs)
    rg["connections"].extend(conns)
    rg["labels"].extend([header, sub])

    # Uniqueness check across all component identifiers.
    ids = []
    for coll in (rg["processors"], rg["connections"], rg["labels"], rg.get("funnels", [])):
        for c in coll:
            ids.append(c["identifier"])
            ids.append(c["instanceIdentifier"])
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        print("ABORT: duplicate identifiers:", dupes)
        return 2

    with gzip.open(flow, "wt", encoding="utf-8") as f:
        json.dump(d, f)

    print("Added: 5 processors, 4 connections, 2 labels.")
    print("Totals -> processors:{} connections:{} labels:{}".format(
        len(rg["processors"]), len(rg["connections"]), len(rg["labels"])))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", required=True, type=Path,
                        help="Path to the target NiFi conf/flow.json.gz")
    raise SystemExit(main(parser.parse_args().conf))
