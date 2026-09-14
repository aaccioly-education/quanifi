#!/usr/bin/env python3
"""
Adds (or replaces) the **data-driven differential testing** demo flow.

Full topology:

    GenerateFlowFile (trigger)
     └─► QuantumTestCaseSource   [test matrix -> JSON array]
          └─► SplitJson          [array -> one FlowFile per case]
               └─► EvaluateJsonPath  [JSON fields -> FlowFile attributes]
                    ├─► QiskitGroverCircuit ${grover.*}
                    │    └─► QiskitAerSimulator
                    │         └─► UpdateAttribute  [compare.label, grover.framework=qiskit]
                    │              └─────────────────────────────────────────────┐
                    └─► CirqGroverCircuit  ${grover.*}                           │
                         └─► CirqSimulator                                       │
                              └─► UpdateAttribute [compare.label, grover.framework=cirq]
                                   └─────────────────────────────────────────────┤
                                                                                  ▼
                                                              QuantumDistributionComparison
                                                               (keyed by ${test.run_id}-${test.case_id})
                                                                                  │
                                                                                  ▼
                                                              QuantumAssertion
                                                               (Hellinger ≤ 0.10, ground-truth check)
                                                                    │           │
                                                                  pass         fail
                                                                    │           │
                                                              QuanifiReport   QuanifiReport
                                                              [passed]        [failed]

NiFi MUST be stopped before running this script.

Usage:
    python3 tools/add_datadriven_flow.py [--conf /path/to/conf/flow.json.gz]

Pass --replace to remove the previous data-driven flow first (identified by
processors at Y >= 5000). Safe to run repeatedly during development.
"""

import argparse
import gzip
import json
import shutil
import uuid
from pathlib import Path
from datetime import datetime

NIFI_BUNDLE   = {"group": "org.apache.nifi", "artifact": "nifi-standard-nar", "version": "2.9.0"}
PYTHON_BUNDLE = {"group": "org.apache.nifi", "artifact": "python-extensions",  "version": "0.1.0"}
REPORTS_DIR   = "reports"

# Purple — visually distinct from existing blue/teal banners on the canvas.
MARK_STYLE = {"background-color": "#4a148c", "border-color": "#ce93d8",
              "font-color": "#ffffff", "font-size": "16px"}
NOTE_STYLE = {"background-color": "#311b92", "border-color": "#b39ddb",
              "font-color": "#ede7f6", "font-size": "12px"}

# 3-row explicit equivalence-partition table.
# Each row drives BOTH framework branches with the same parameters.
TEST_MATRIX = json.dumps([
    {"grover.marked_state": "11",  "grover.num_iterations": "1",
     "test.partition": "n=2 typical",   "test.expected": "11"},
    {"grover.marked_state": "101", "grover.num_iterations": "2",
     "test.partition": "n=3 mixed",     "test.expected": "101"},
    {"grover.marked_state": "000", "grover.num_iterations": "2",
     "test.partition": "n=3 all-zeros", "test.expected": "000"},
])

# Y-band for the whole new flow. Anything in [Y_MIN, Y_MAX] is removed on --replace.
Y_MIN, Y_MAX = 5000.0, 7000.0


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def nid():
    return str(uuid.uuid4())


def proc(*, name, ptype, bundle, properties, comments, x, y, group_id,
         auto_terminate, scheduling_period="0 sec", state="ENABLED"):
    # NiFi's flow serialization only accepts ENABLED / DISABLED / RUNNING.
    # ENABLED = present on canvas, not scheduled (i.e. "stopped-but-ready").
    return {
        "identifier": nid(), "instanceIdentifier": nid(),
        "name": name, "comments": comments,
        "position": {"x": float(x), "y": float(y)},
        "type": ptype, "bundle": bundle,
        "properties": properties, "propertyDescriptors": {}, "style": {},
        "schedulingPeriod": scheduling_period, "schedulingStrategy": "TIMER_DRIVEN",
        "executionNode": "ALL", "penaltyDuration": "30 sec", "yieldDuration": "1 sec",
        "bulletinLevel": "WARN", "runDurationMillis": 0,
        "concurrentlySchedulableTaskCount": 1,
        "autoTerminatedRelationships": auto_terminate,
        "scheduledState": state, "retryCount": 10, "retriedRelationships": [],
        "backoffMechanism": "PENALIZE_FLOWFILE", "maxBackoffPeriod": "10 mins",
        "componentType": "PROCESSOR", "groupIdentifier": group_id,
    }


def conn(src, rel, dst, group_id, z):
    def ep(p):
        return {"id": p["identifier"], "type": "PROCESSOR", "groupId": group_id,
                "name": p["name"], "comments": "",
                "instanceIdentifier": p["instanceIdentifier"]}
    return {
        "identifier": nid(), "instanceIdentifier": nid(), "name": "",
        "source": ep(src), "destination": ep(dst),
        "labelIndex": 0, "zIndex": z, "selectedRelationships": [rel],
        "backPressureObjectThreshold": 10000, "backPressureDataSizeThreshold": "1 GB",
        "flowFileExpiration": "0 sec", "prioritizers": [], "bends": [],
        "loadBalanceStrategy": "DO_NOT_LOAD_BALANCE", "partitioningAttribute": "",
        "loadBalanceCompression": "DO_NOT_COMPRESS",
        "componentType": "CONNECTION", "groupIdentifier": group_id,
    }


def label(text, x, y, w, h, group_id, style, z=2):
    return {
        "identifier": nid(), "instanceIdentifier": nid(),
        "position": {"x": float(x), "y": float(y)},
        "label": text, "zIndex": z, "width": float(w), "height": float(h),
        "style": style, "componentType": "LABEL", "groupIdentifier": group_id,
    }


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build(flow_path, replace=False):
    path = Path(flow_path)
    flow = json.load(gzip.open(path, "rb"))
    root = flow["rootGroup"]
    gid  = root["instanceIdentifier"]

    # Guard: prevent duplicate runs without --replace.
    if any(p["name"] == "QuantumTestCaseSource" for p in root["processors"]) and not replace:
        print("QuantumTestCaseSource already on canvas. Pass --replace to rebuild.")
        return False

    backup = path.with_suffix(f".{datetime.now():%Y%m%d_%H%M%S}.bak.gz")
    shutil.copy2(path, backup)
    print(f"Backup: {backup}")

    if replace:
        before = len(root["processors"])
        root["processors"] = [p for p in root["processors"]
                              if not (Y_MIN <= p["position"]["y"] <= Y_MAX)]
        root["labels"]     = [l for l in root["labels"]
                              if not (Y_MIN <= l["position"]["y"] <= Y_MAX)]
        # Remove connections that referenced deleted processors.
        kept_ids = {p["identifier"] for p in root["processors"]}
        root["connections"] = [c for c in root["connections"]
                               if c["source"]["id"] in kept_ids
                               and c["destination"]["id"] in kept_ids]
        print(f"Removed {before - len(root['processors'])} old processors.")

    z = max((c.get("zIndex", 0) for c in root["connections"]), default=0)

    # ── Layout constants ────────────────────────────────────────────────────
    X0  = 200.0   # left edge
    GAP = 560.0   # horizontal gap between processors

    # Row Y positions
    Y_TOP    = 5300.0   # front-end shared row
    Y_QISKIT = 5080.0   # Qiskit branch (above centre)
    Y_CIRQ   = 5520.0   # Cirq branch   (below centre)
    Y_MID    = 5300.0   # assertion + comparison row (re-merges)

    # X columns
    X_GEN    = X0
    X_SRC    = X0 + 1 * GAP
    X_SPLIT  = X0 + 2 * GAP
    X_EVAL   = X0 + 3 * GAP
    X_CIRC   = X0 + 4 * GAP   # circuit builders (branch split)
    X_SIM    = X0 + 5 * GAP   # simulators
    X_UPD    = X0 + 6 * GAP   # UpdateAttribute (stamps compare.label)
    X_CMP    = X0 + 7 * GAP   # QuantumDistributionComparison (fan-in)
    X_ASSERT = X0 + 8 * GAP   # QuantumAssertion
    X_RPT    = X0 + 9 * GAP   # QuanifiReport ×2 (pass / fail)

    # ── Front-end (shared) ──────────────────────────────────────────────────
    gen = proc(
        name="GenerateFlowFile", ptype="org.apache.nifi.processors.standard.GenerateFlowFile",
        bundle=NIFI_BUNDLE,
        properties={"File Size": "0B", "Batch Size": "1", "Unique FlowFiles": "false",
                    "Character Set": "UTF-8", "Data Format": "Text"},
        comments=(
            "TRIGGER. Fires every 30 s to start a test run. "
            "Right-click → 'Run Once' for a single controlled run; "
            "stop after one trigger so reports don't accumulate duplicate cards."
        ),
        x=X_GEN, y=Y_TOP, group_id=gid, auto_terminate=[],
        scheduling_period="30 sec")

    src = proc(
        name="QuantumTestCaseSource", ptype="QuantumTestCaseSource", bundle=PYTHON_BUNDLE,
        properties={"Test Matrix": TEST_MATRIX, "Case ID Prefix": "grover",
                    "Partition Label": "", "Run ID": ""},
        comments=(
            "TEST TABLE. Expands the Test Matrix into a JSON array — one object per "
            "equivalence-class case. Each object carries the circuit parameters "
            "(grover.marked_state, grover.num_iterations), a partition label, the "
            "ground-truth test.expected, and is auto-stamped with test.case_id + a "
            "shared test.run_id (used downstream to correlate the two framework results "
            "for the same case). Switch the matrix to a JSON object for a Cartesian "
            "product: {\"grover.marked_state\":[\"00\",\"11\"],\"grover.num_iterations\":[\"1\",\"2\"]}."
        ),
        x=X_SRC, y=Y_TOP, group_id=gid, auto_terminate=["failure"])

    split = proc(
        name="SplitJson", ptype="org.apache.nifi.processors.standard.SplitJson",
        bundle=NIFI_BUNDLE,
        properties={"JsonPath Expression": "$",
                    "Null Value Representation": "empty string"},
        comments=(
            "FAN-OUT. Splits the JSON array into one FlowFile per case "
            "(JsonPath '$' = each top-level array element). "
            "3 rows → 3 FlowFiles on the 'split' relationship. "
            "'original' + 'failure' are auto-terminated."
        ),
        x=X_SPLIT, y=Y_TOP, group_id=gid, auto_terminate=["original", "failure"])

    evalp = proc(
        name="EvaluateJsonPath", ptype="org.apache.nifi.processors.standard.EvaluateJsonPath",
        bundle=NIFI_BUNDLE,
        properties={
            "Destination":              "flowfile-attribute",
            "Return Type":              "json",
            "Path Not Found Behavior":  "ignore",
            "Null Value Representation": "empty string",
            "grover.marked_state":      "$['grover.marked_state']",
            "grover.num_iterations":    "$['grover.num_iterations']",
            "test.case_id":             "$['test.case_id']",
            "test.run_id":              "$['test.run_id']",
            "test.partition":           "$['test.partition']",
            "test.expected":            "$['test.expected']",
        },
        comments=(
            "HOIST. Reads each field from the case JSON object and writes it as a "
            "FlowFile attribute: ${grover.marked_state}, ${test.case_id}, etc. "
            "After this step the case params are in the attribute map and the "
            "framework processors can read them via Expression Language. "
            "NiFi then CLONES the FlowFile — one copy per downstream connection — "
            "so both framework branches receive an identical copy."
        ),
        x=X_EVAL, y=Y_TOP, group_id=gid, auto_terminate=["unmatched", "failure"])

    # ── Qiskit branch ───────────────────────────────────────────────────────
    qcirc = proc(
        name="QiskitGroverCircuit", ptype="QiskitGroverCircuit", bundle=PYTHON_BUNDLE,
        properties={"Marked State": "${grover.marked_state}",
                    "Num Iterations": "${grover.num_iterations}",
                    "Insert Barriers": "false", "Output Format": "qasm3"},
        comments=(
            "QISKIT BRANCH — circuit builder. "
            "Properties use ${grover.*} Expression Language, NOT literals: the "
            "test table drives every case through this single box. "
            "Output: QASM3 circuit bytes on the FlowFile content + circuit.* attributes."
        ),
        x=X_CIRC, y=Y_QISKIT, group_id=gid, auto_terminate=["original", "failure"])

    qsim = proc(
        name="QiskitAerSimulator", ptype="QiskitAerSimulator", bundle=PYTHON_BUNDLE,
        properties={"Shots": "1024", "Noise Model": "none", "Readout Error Rate": "0.0",
                    "1-Qubit Error Rate": "0.001", "2-Qubit Error Rate": "0.01",
                    "T1 (us)": "50", "T2 (us)": "70", "Gate Time (ns)": "100",
                    "Backend Name": "fake_manila"},
        comments="Runs the Qiskit circuit on AerSimulator (ideal, 1024 shots). Output: counts JSON.",
        x=X_SIM, y=Y_QISKIT, group_id=gid, auto_terminate=["original", "failure"])

    # UpdateAttribute stamps two things needed by the downstream comparison:
    #   compare.label  — unique per case so QuantumDistributionComparison pairs correctly
    #   grover.framework — appears in the comparison report as the framework label
    qupd = proc(
        name="UpdateAttribute [qiskit]",
        ptype="org.apache.nifi.processors.attributes.UpdateAttribute",
        bundle=NIFI_BUNDLE,
        properties={
            "compare.label":    "${test.run_id}-${test.case_id}",
            "grover.framework": "qiskit",
        },
        comments=(
            "ROUTING STAMP (Qiskit side). Sets two attributes: "
            "(1) compare.label = '${test.run_id}-${test.case_id}' — the unique slot key "
            "that QuantumDistributionComparison uses to pair this result with its Cirq "
            "counterpart. Using run_id + case_id means concurrent test cases never "
            "cross-pair, even across repeated 30-second triggers. "
            "(2) grover.framework = 'qiskit' — read by QuantumDistributionComparison "
            "as the label for this side of the comparison report."
        ),
        x=X_UPD, y=Y_QISKIT, group_id=gid, auto_terminate=[])

    # ── Cirq branch ─────────────────────────────────────────────────────────
    ccirc = proc(
        name="CirqGroverCircuit", ptype="CirqGroverCircuit", bundle=PYTHON_BUNDLE,
        properties={"Marked State": "${grover.marked_state}",
                    "Num Iterations": "${grover.num_iterations}",
                    "Insert Barriers": "false", "Output Format": "qasm2"},
        comments=(
            "CIRQ BRANCH — circuit builder. "
            "Same ${grover.*} EL properties as the Qiskit branch: identical inputs, "
            "different framework. This guarantees the comparison is fair. "
            "Output format is qasm2 (Cirq's serialisable interchange format)."
        ),
        x=X_CIRC, y=Y_CIRQ, group_id=gid, auto_terminate=["original", "failure"])

    csim = proc(
        name="CirqSimulator", ptype="CirqSimulator", bundle=PYTHON_BUNDLE,
        properties={"Shots": "1024", "Noise Model": "none",
                    "Readout Error Rate": "0.0", "Error Probability": "0.01",
                    "Damping Gamma": "0.05"},
        comments="Runs the Cirq circuit (1024 shots). Output: counts JSON.",
        x=X_SIM, y=Y_CIRQ, group_id=gid, auto_terminate=["original", "failure"])

    cupd = proc(
        name="UpdateAttribute [cirq]",
        ptype="org.apache.nifi.processors.attributes.UpdateAttribute",
        bundle=NIFI_BUNDLE,
        properties={
            "compare.label":    "${test.run_id}-${test.case_id}",
            "grover.framework": "cirq",
        },
        comments=(
            "ROUTING STAMP (Cirq side). Same compare.label expression as the Qiskit "
            "UpdateAttribute: both sides of the same case share the same slot key, so "
            "QuantumDistributionComparison pairs them correctly regardless of arrival "
            "order. grover.framework = 'cirq' labels this side of the report."
        ),
        x=X_UPD, y=Y_CIRQ, group_id=gid, auto_terminate=[])

    # ── Fan-in: comparison ──────────────────────────────────────────────────
    cmp = proc(
        name="QuantumDistributionComparison", ptype="QuantumDistributionComparison",
        bundle=PYTHON_BUNDLE,
        properties={
            "Reports Directory":  REPORTS_DIR,
            "Flow Name":          "datadriven-grover",
            "State Directory":    f"{REPORTS_DIR}/tmp/quanifi_compare_state",
            "Comparison Label":   "${compare.label}",
            "Framework Label":    "${grover.framework}",
        },
        comments=(
            "DIFFERENTIAL ORACLE. Receives the Qiskit and Cirq results for the SAME "
            "test case (paired by 'Comparison Label' = '${compare.label}' = "
            "'${test.run_id}-${test.case_id}'). "
            "First arrival is stored to disk; second arrival triggers the comparison. "
            "Computes Hellinger distance, Total Variation, and Fidelity. "
            "The 'failure' relationship = 'first arrival stored, waiting' — "
            "auto-terminate it. 'success' = comparison complete; flows to QuantumAssertion."
        ),
        x=X_CMP, y=Y_MID, group_id=gid, auto_terminate=["failure"])

    # ── Assertion ───────────────────────────────────────────────────────────
    assertion = proc(
        name="QuantumAssertion", ptype="QuantumAssertion", bundle=PYTHON_BUNDLE,
        properties={
            "Hellinger Threshold": "0.10",
            "Check Ground Truth":  "true",
            "Reports Directory":   REPORTS_DIR,
            "Flow Name":           "datadriven-grover",
        },
        comments=(
            "ASSERTION GATE. Applies two checks to each comparison result: "
            "(1) Differential: Hellinger distance ≤ 0.10 (frameworks agree). "
            "(2) Ground-truth: compare.top_result_a == test.expected (correct answer). "
            "Verdict: PASS / FAIL / DISAGREE. "
            "Routes 'pass' → passing report, 'fail' → failure report. "
            "Appends a row to datadriven-grover-assertion.html for every case. "
            "Adjust Hellinger Threshold for noisy / low-shot runs (0.15-0.25)."
        ),
        x=X_ASSERT, y=Y_MID, group_id=gid, auto_terminate=[])

    # ── Final reports (pass / fail) ─────────────────────────────────────────
    rpt_pass = proc(
        name="QuanifiReport [passed]", ptype="QuanifiReport", bundle=PYTHON_BUNDLE,
        properties={"Reports Directory": REPORTS_DIR,
                    "Flow Name":         "datadriven-grover-passed"},
        comments=(
            "PASS REPORT. Writes datadriven-grover-passed.html. "
            "Each card = one test case where both frameworks agreed and the answer "
            "matched the ground truth. Shows the distribution bar chart and metrics."
        ),
        x=X_RPT, y=Y_QISKIT, group_id=gid, auto_terminate=["success", "failure"])

    rpt_fail = proc(
        name="QuanifiReport [failed]", ptype="QuanifiReport", bundle=PYTHON_BUNDLE,
        properties={"Reports Directory": REPORTS_DIR,
                    "Flow Name":         "datadriven-grover-failed"},
        comments=(
            "FAIL REPORT. Writes datadriven-grover-failed.html. "
            "Each card = one test case that FAILED or DISAGREED. "
            "Inspect assert.reason to see which check failed and why."
        ),
        x=X_RPT, y=Y_CIRQ, group_id=gid, auto_terminate=["success", "failure"])

    # ── Wire everything ─────────────────────────────────────────────────────
    procs = [gen, src, split, evalp,
             qcirc, qsim, qupd,
             ccirc, csim, cupd,
             cmp, assertion, rpt_pass, rpt_fail]

    conns = [
        # Front-end chain
        conn(gen,   "success", src,   gid, z + 1),
        conn(src,   "success", split, gid, z + 2),
        conn(split, "split",   evalp, gid, z + 3),
        # Fan-out: one FlowFile copy per branch
        conn(evalp, "matched", qcirc, gid, z + 4),
        conn(evalp, "matched", ccirc, gid, z + 5),
        # Qiskit branch
        conn(qcirc, "success", qsim,  gid, z + 6),
        conn(qsim,  "success", qupd,  gid, z + 7),
        # Cirq branch
        conn(ccirc, "success", csim,  gid, z + 8),
        conn(csim,  "success", cupd,  gid, z + 9),
        # Fan-in: both branches → comparison (keyed by compare.label)
        conn(qupd,  "success", cmp,   gid, z + 10),
        conn(cupd,  "success", cmp,   gid, z + 11),
        # Assertion → reports
        conn(cmp,   "success", assertion, gid, z + 12),
        conn(assertion, "pass", rpt_pass, gid, z + 13),
        conn(assertion, "fail", rpt_fail, gid, z + 14),
    ]

    labels = [
        label(
            "⬡  DATA-DRIVEN DIFFERENTIAL TESTING  "
            "—  test table drives both frameworks; assertion checks Hellinger + ground truth",
            X0 - 60, Y_TOP - 320, 5400, 80, gid, MARK_STYLE),
        label(
            "FRONT-END:  GenerateFlowFile fires → QuantumTestCaseSource expands the "
            "3-row partition table → SplitJson makes one FlowFile per case → "
            "EvaluateJsonPath hoists each field into FlowFile attributes. "
            "NiFi then CLONES the FlowFile to both framework branches.",
            X0 - 40, Y_TOP + 150, 2600, 130, gid, NOTE_STYLE),
        label(
            "FRAMEWORK BRANCHES:  Both read ${grover.marked_state} and "
            "${grover.num_iterations} from attributes — identical params, different "
            "framework. UpdateAttribute stamps compare.label = ${test.run_id}-${test.case_id} "
            "so the downstream comparison pairs results by case, not by arrival order.",
            X0 + 3.5 * GAP, Y_TOP + 360, 2400, 130, gid, NOTE_STYLE),
        label(
            "ASSERTION LAYER:  QuantumDistributionComparison pairs the two framework "
            "results for each case (keyed by compare.label). Computes Hellinger / TV / "
            "Fidelity. QuantumAssertion applies (1) H ≤ 0.10 differential check and "
            "(2) top_result == test.expected ground-truth check. Verdict: PASS / FAIL / "
            "DISAGREE. Routes to separate pass / fail HTML reports.",
            X0 + 6.5 * GAP, Y_TOP + 260, 2000, 160, gid, NOTE_STYLE),
    ]

    root["processors"].extend(procs)
    root["connections"].extend(conns)
    root["labels"].extend(labels)

    with gzip.open(path, "wb") as f:
        f.write(json.dumps(flow, indent=2).encode("utf-8"))

    # Integrity check
    ids = {p["identifier"] for p in root["processors"]}
    bad = [c for c in root["connections"]
           if c["source"]["id"] not in ids or c["destination"]["id"] not in ids]
    if bad:
        print(f"WARNING: {len(bad)} dangling connection(s) — check the flow!")
    else:
        print("Connection integrity OK.")

    print(f"\nAdded {len(procs)} processors, {len(conns)} connections, {len(labels)} labels.")
    for p in procs:
        print(f"  {p['name']:<35} @ ({p['position']['x']:.0f}, {p['position']['y']:.0f})")
    return True


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", required=True,
                    help="Path to the target NiFi conf/flow.json.gz")
    ap.add_argument("--replace", action="store_true",
                    help="Remove existing data-driven flow (Y>=5000) before adding")
    args = ap.parse_args()

    if build(args.conf, replace=args.replace):
        print("\nDone. Start NiFi and scroll to the purple 'DATA-DRIVEN DIFFERENTIAL "
              "TESTING' banner. Run once with GenerateFlowFile → Run Once.")
