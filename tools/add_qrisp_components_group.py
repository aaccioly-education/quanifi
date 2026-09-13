#!/usr/bin/env python3
"""Add the 'Qrisp Components & Textbook Algorithms' Process Group to NiFi flow.json.gz.

Generates complete, runnable pipelines for all 7 Qrisp processors:
  1. Deutsch-Jozsa: QrispDeutschJozsa (balanced vs constant test)
  2. Bernstein-Vazirani: QrispBernsteinVazirani (hidden string recovery)
  3. SWAP Test: QrispSwapTest (state overlap / fidelity)
  4. Max Clique QAOA: MaxCliqueProblem -> QrispQAOA (RX mixer)
  5. Max Independent Set QAOA: MaxIndependentSetProblem -> QrispQAOA (RX mixer)
  6. Portfolio Rebalancing QAOA: PortfolioRebalancingProblem -> QrispQAOA (XY mixer)
  7. Molecular VQE: QrispHamiltonian -> QrispQCCSDAnsatz -> QrispVQE (Hartree-Fock init)

Usage:
  python3 tools/add_qrisp_components_group.py [--flow /path/to/flow.json.gz] [--nifi-version 2.11.0]
"""

import argparse
import gzip
import json
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from add_flow import make_processor, make_connection, make_label, nid

GROUP_NAME = "Qrisp Components & Textbook Algorithms"
GROUP_COMMENTS = (
    "Complete end-to-end pipelines demonstrating Qrisp-powered quantum algorithms "
    "and problem encoders: Deutsch-Jozsa, Bernstein-Vazirani, SWAP Test, Max Clique QAOA, "
    "Max Independent Set QAOA, Portfolio Rebalancing QAOA, and Molecular VQE with QCCSD Ansatz."
)
REPORTS_DIR = str(ROOT / "reports")


def create_empty_flow(nifi_version: str = "2.11.0"):
    root_id = nid()
    root_inst = nid()
    return {
        "encodingVersion": {"majorVersion": 2, "minorVersion": 0},
        "maxTimerDrivenThreadCount": 10,
        "registries": [],
        "parameterContexts": [],
        "parameterProviders": [],
        "controllerServices": [],
        "reportingTasks": [],
        "flowAnalysisRules": [],
        "connectors": [],
        "rootGroup": {
            "identifier": root_id,
            "instanceIdentifier": root_inst,
            "name": "NiFi Flow",
            "comments": "",
            "position": {"x": 0.0, "y": 0.0},
            "processGroups": [],
            "remoteProcessGroups": [],
            "processors": [],
            "inputPorts": [],
            "outputPorts": [],
            "connections": [],
            "labels": [],
            "funnels": [],
            "controllerServices": [],
            "defaultFlowFileExpiration": "0 sec",
            "defaultBackPressureObjectThreshold": 10000,
            "defaultBackPressureDataSizeThreshold": "1 GB",
            "scheduledState": "ENABLED",
            "executionEngine": "INHERITED",
            "maxConcurrentTasks": 1,
            "statelessFlowTimeout": "1 min",
            "flowFileConcurrency": "UNBOUNDED",
            "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
            "componentType": "PROCESS_GROUP",
        },
    }


def build_pipeline_spec(nifi_version: str = "2.11.0"):
    std_bundle = {
        "group": "org.apache.nifi",
        "artifact": "nifi-standard-nar",
        "version": nifi_version,
    }
    py_bundle = {
        "group": "org.apache.nifi",
        "artifact": "python-extensions",
        "version": "0.1.0",
    }
    report_bundle = {
        "group": "org.apache.nifi",
        "artifact": "python-extensions",
        "version": "0.3.1",
    }

    pipelines = [
        {
            "id": "deutsch_jozsa",
            "title": "Deutsch-Jozsa Algorithm — Distinguishes constant from balanced boolean functions",
            "desc": "Single-query evaluation of balanced boolean oracle over 3 qubits",
            "style": {"background-color": "#1f2937", "border-color": "#3b82f6", "font-color": "#93c5fd"},
            "steps": [
                {
                    "name": "DJ — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Qrisp Deutsch-Jozsa",
                    "type": "QrispDeutschJozsa",
                    "bundle": py_bundle,
                    "properties": {
                        "Function Type": "balanced",
                        "Num Qubits": "3",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "DJ — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-deutsch-jozsa",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "bernstein_vazirani",
            "title": "Bernstein-Vazirani Algorithm — Recovers hidden bitstring s in O(1) queries",
            "desc": "Recovers secret s='1011' using phase kickback and Hadamard transform",
            "style": {"background-color": "#1f2937", "border-color": "#8b5cf6", "font-color": "#c4b5fd"},
            "steps": [
                {
                    "name": "BV — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Qrisp Bernstein-Vazirani",
                    "type": "QrispBernsteinVazirani",
                    "bundle": py_bundle,
                    "properties": {
                        "Secret Bitstring": "1011",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "BV — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-bernstein-vazirani",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "swap_test",
            "title": "SWAP Test — Measures quantum state overlap |⟨ψ|φ⟩|² via ancilla Fredkin gate",
            "desc": "Computes fidelity between |0⟩ and |0⟩ states with 1024 measurement shots",
            "style": {"background-color": "#1f2937", "border-color": "#10b981", "font-color": "#6ee7b7"},
            "steps": [
                {
                    "name": "SWAP — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Qrisp SWAP Test",
                    "type": "QrispSwapTest",
                    "bundle": py_bundle,
                    "properties": {
                        "State A": "zero",
                        "State B": "zero",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "SWAP — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-swap-test",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "max_clique",
            "title": "Maximum Clique QAOA — Encodes graph complement into Cost Hamiltonian",
            "desc": "Finds maximum clique on 4-node graph via QAOA with standard RX mixer",
            "style": {"background-color": "#1f2937", "border-color": "#f59e0b", "font-color": "#fcd34d"},
            "steps": [
                {
                    "name": "MaxClique — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Max Clique Encoder",
                    "type": "MaxCliqueProblem",
                    "bundle": py_bundle,
                    "properties": {
                        "Edges": "[[0, 1], [1, 2], [2, 0], [2, 3]]",
                        "Penalty Factor": "2.0",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Qrisp QAOA (MaxClique)",
                    "type": "QrispQAOA",
                    "bundle": py_bundle,
                    "properties": {
                        "Layers": "1",
                        "Mixer Type": "RX",
                        "Optimizer": "COBYLA",
                        "Max Iterations": "15",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "MaxClique — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-qaoa-max-clique",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "max_independent_set",
            "title": "Maximum Independent Set QAOA — Graph vertex independence optimization",
            "desc": "Finds MIS on 3-node path graph via QAOA with edge conflict penalties",
            "style": {"background-color": "#1f2937", "border-color": "#ec4899", "font-color": "#fbcfe8"},
            "steps": [
                {
                    "name": "MIS — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "MIS Encoder",
                    "type": "MaxIndependentSetProblem",
                    "bundle": py_bundle,
                    "properties": {
                        "Edges": "[[0, 1], [1, 2]]",
                        "Penalty Factor": "2.0",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Qrisp QAOA (MIS)",
                    "type": "QrispQAOA",
                    "bundle": py_bundle,
                    "properties": {
                        "Layers": "1",
                        "Mixer Type": "RX",
                        "Optimizer": "COBYLA",
                        "Max Iterations": "15",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "MIS — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-qaoa-mis",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "portfolio_rebalancing",
            "title": "Portfolio Optimization QAOA — Mean-variance objective with XY Mixer",
            "desc": "Selects optimal 2 assets out of 3 under risk-return trade-off and budget K=2",
            "style": {"background-color": "#1f2937", "border-color": "#06b6d4", "font-color": "#67e8f9"},
            "steps": [
                {
                    "name": "Portfolio — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Portfolio Encoder",
                    "type": "PortfolioRebalancingProblem",
                    "bundle": py_bundle,
                    "properties": {
                        "Expected Returns": "[0.1, 0.2, 0.15]",
                        "Covariance Matrix": "[[0.05, 0.01, 0.02], [0.01, 0.04, 0.01], [0.02, 0.01, 0.06]]",
                        "Risk Factor": "0.5",
                        "Budget": "2",
                        "Penalty Factor": "3.0",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Qrisp QAOA (Portfolio XY)",
                    "type": "QrispQAOA",
                    "bundle": py_bundle,
                    "properties": {
                        "Layers": "1",
                        "Mixer Type": "XY",
                        "Optimizer": "COBYLA",
                        "Max Iterations": "15",
                        "Shots": "1024",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Portfolio — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-qaoa-portfolio",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
        {
            "id": "qccsd_vqe",
            "title": "Molecular VQE with QCCSD — Unitary Coupled Cluster with Hartree-Fock State",
            "desc": "Solves ground-state energy of 4-qubit Hamiltonian via QCCSD ansatz and VQE",
            "style": {"background-color": "#1f2937", "border-color": "#e11d48", "font-color": "#fda4af"},
            "steps": [
                {
                    "name": "QCCSD — Trigger",
                    "type": "org.apache.nifi.processors.standard.GenerateFlowFile",
                    "bundle": std_bundle,
                    "properties": {"File Size": "0B", "Batch Size": "1", "Data Format": "Text"},
                    "auto_terminate": [],
                    "sched": "1 min",
                },
                {
                    "name": "Qrisp Hamiltonian",
                    "type": "QrispHamiltonian",
                    "bundle": py_bundle,
                    "properties": {
                        "Hamiltonian": "Z0 + Z1 + 0.5 Z2 + 0.5 Z3",
                        "Num Qubits": "4",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Qrisp QCCSD Ansatz",
                    "type": "QrispQCCSDAnsatz",
                    "bundle": py_bundle,
                    "properties": {
                        "Num Spin Orbitals": "4",
                        "Num Electrons": "2",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "Qrisp VQE (QCCSD)",
                    "type": "QrispVQE",
                    "bundle": py_bundle,
                    "properties": {
                        "Optimizer": "COBYLA",
                        "Max Iterations": "20",
                        "Shots": "256",
                        "Initial Parameters": "zeros",
                    },
                    "auto_terminate": ["failure"],
                    "sched": "0 sec",
                },
                {
                    "name": "QCCSD — Report",
                    "type": "QuanifiReport",
                    "bundle": report_bundle,
                    "properties": {
                        "Flow Name": "qrisp-vqe-qccsd",
                        "Reports Directory": REPORTS_DIR,
                    },
                    "auto_terminate": ["success", "failure"],
                    "sched": "0 sec",
                },
            ],
        },
    ]
    return pipelines


def add_qrisp_components_group(flow_path: Path, nifi_version: str = "2.11.0", scheduled_state: str = "ENABLED"):
    if flow_path.exists():
        backup = flow_path.with_name(
            f"{flow_path.name}.bak_qrisp_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        shutil.copy2(flow_path, backup)
        print(f"Backup created: {backup}")
        with gzip.open(flow_path, "rt", encoding="utf-8") as f:
            flow_data = json.load(f)
    else:
        print(f"Creating new flow structure for {flow_path}")
        flow_data = create_empty_flow(nifi_version)

    root = flow_data["rootGroup"]
    existing_pgs = root.setdefault("processGroups", [])
    for idx, pg in enumerate(existing_pgs):
        if pg.get("name") == GROUP_NAME:
            print(f"Removing existing group '{GROUP_NAME}'...")
            existing_pgs.pop(idx)
            break

    group_id = nid()
    group_inst_id = nid()
    group = {
        "identifier": group_id,
        "instanceIdentifier": group_inst_id,
        "name": GROUP_NAME,
        "comments": GROUP_COMMENTS,
        "position": {"x": 300.0, "y": 300.0},
        "processGroups": [],
        "remoteProcessGroups": [],
        "processors": [],
        "inputPorts": [],
        "outputPorts": [],
        "connections": [],
        "labels": [],
        "funnels": [],
        "controllerServices": [],
        "defaultFlowFileExpiration": "0 sec",
        "defaultBackPressureObjectThreshold": 10000,
        "defaultBackPressureDataSizeThreshold": "1 GB",
        "scheduledState": scheduled_state,
        "executionEngine": "INHERITED",
        "maxConcurrentTasks": 1,
        "statelessFlowTimeout": "1 min",
        "flowFileConcurrency": "UNBOUNDED",
        "flowFileOutboundPolicy": "STREAM_WHEN_AVAILABLE",
        "componentType": "PROCESS_GROUP",
        "groupIdentifier": root["instanceIdentifier"],
    }

    pipelines = build_pipeline_spec(nifi_version)
    all_processors = []
    all_connections = []
    all_labels = []

    start_x = 100.0
    start_y = 120.0
    row_height = 260.0
    col_width = 440.0

    z_index = 0
    for row_idx, pipeline in enumerate(pipelines):
        cur_y = start_y + row_idx * row_height
        steps = pipeline["steps"]

        # Label above row
        label_text = f"⬡ {pipeline['title']} ⬡\n{pipeline['desc']}"
        lbl = make_label(
            text=label_text,
            x=start_x,
            y=cur_y - 65.0,
            width=len(steps) * col_width - 60.0,
            height=50.0,
            group_id=group_inst_id,
            style=pipeline.get("style", {}),
        )
        all_labels.append(lbl)

        row_procs = []
        for col_idx, step in enumerate(steps):
            cur_x = start_x + col_idx * col_width
            is_trigger = col_idx == 0
            proc = make_processor(
                name=step["name"],
                proc_type=step["type"],
                bundle=step["bundle"],
                properties=step.get("properties", {}),
                x=cur_x,
                y=cur_y,
                group_id=group_inst_id,
                scheduling_period=step.get("sched", "0 sec"),
                auto_terminate=step.get("auto_terminate", []),
                scheduled_state=scheduled_state if not is_trigger else "DISABLED",
                run_duration_millis=0,
            )
            row_procs.append(proc)
            all_processors.append(proc)

        # Connect chain
        for i in range(len(row_procs) - 1):
            z_index += 1
            conn = make_connection(
                src=row_procs[i],
                src_rel="success",
                dst=row_procs[i + 1],
                group_id=group_inst_id,
                z_index=z_index,
            )
            all_connections.append(conn)

    group["processors"] = all_processors
    group["connections"] = all_connections
    group["labels"] = all_labels

    existing_pgs.append(group)

    # Validate UUID uniqueness
    seen_ids = set()
    for item in (
        group["processors"]
        + group["connections"]
        + group["labels"]
        + existing_pgs
        + root.get("processors", [])
        + root.get("connections", [])
    ):
        for key in ("identifier", "instanceIdentifier"):
            val = item.get(key)
            if val:
                if val in seen_ids:
                    raise ValueError(f"Duplicate ID found: {val}")
                seen_ids.add(val)

    flow_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(flow_path, "wt", encoding="utf-8") as f:
        json.dump(flow_data, f, indent=2)

    print(f"Successfully wrote flow to {flow_path}")
    print(f"Added Process Group '{GROUP_NAME}' with:")
    print(f"  - {len(all_processors)} processors across {len(pipelines)} pipelines")
    print(f"  - {len(all_connections)} connections")
    print(f"  - {len(all_labels)} section labels")


def main():
    parser = argparse.ArgumentParser(description="Add Qrisp Components Process Group to NiFi")
    parser.add_argument(
        "--flow",
        type=Path,
        default=Path("~/projects/nifi/nifi-2.11.0/conf/flow.json.gz"),
        help="Target flow.json.gz path",
    )
    parser.add_argument(
        "--nifi-version",
        type=str,
        default="2.11.0",
        help="Standard NAR bundle version (default: 2.11.0)",
    )
    parser.add_argument(
        "--run-state",
        type=str,
        default="ENABLED",
        choices=["ENABLED", "RUNNING", "DISABLED"],
        help="Scheduled state for processors (default: ENABLED)",
    )
    args = parser.parse_args()

    # Auto-detect nifi version if contained in flow path
    if "2.9" in str(args.flow):
        args.nifi_version = "2.9.0"
    elif "2.10" in str(args.flow):
        args.nifi_version = "2.10.0"

    add_qrisp_components_group(args.flow, args.nifi_version, args.run_state)


if __name__ == "__main__":
    main()
