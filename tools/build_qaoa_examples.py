#!/usr/bin/env python3
"""Generate stopped NiFi flow snapshots for every QAOA processor and run
them locally.

Two independent snapshots share one set of processor configurations with
the headless execution:

- ``lanes_snapshot`` / ``execute_lanes``: one lane per QAOA processor (5
  train-only solvers + 5 fixed-angle builders), each
  ``<encoder> -> <QAOA processor> -> <engine> -> QuantumQAOAEvaluator ->
  QuanifiReport``.
- ``nxm_snapshot`` / ``execute_nxm``: the N×M matrix -- one shared
  Hamiltonian feeds the 5 fixed-angle builders, each builder's output fans
  out to all 7 counts engines, and every engine feeds one shared
  QuantumQAOAEvaluator.

No NiFi server, provider jobs or credentials are needed. Modelled on
``tools/build_pyquil_examples.py``.
"""
import argparse
import html
import importlib
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# The N×M matrix instance (matches tests/qaoa_reference.py's H_NXM /
# NXM_LAYERS / NXM_BETAS / NXM_GAMMAS exactly; kept as local literals here
# so building the snapshots never depends on the tests/ tree -- only
# ``execute_nxm``'s Hellinger check imports it, via ``_harness``).
H_NXM = "-0.8 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2"
NXM_LAYERS = 2
NXM_BETAS = [-0.67, -0.42]
NXM_GAMMAS = [1.14, 1.27]
NXM_LAYERS_STR = str(NXM_LAYERS)
NXM_BETAS_STR = str(NXM_BETAS)
NXM_GAMMAS_STR = str(NXM_GAMMAS)

DEFAULT_ENGINE = {"Shots": "1024", "Random Seed": "11"}

# (key, title, description, steps). Every lane ends with
# QuantumQAOAEvaluator {} (appended here) then QuanifiReport (appended in
# lanes_snapshot/execute_lanes, same pattern as build_pyquil_examples.py).
LANES = [
    (
        "qiskit-qaoa",
        "QiskitQAOA — triangle MaxCut",
        "Train QiskitQAOA on the default triangle MaxCut Hamiltonian, sample the trained circuit on CirqSimulator, then score it.",
        [
            ("MaxCutProblem", {}),
            (
                "QiskitQAOA",
                {"Layers": "2", "Max Iterations": "150", "Random Seed": "11"},
            ),
            ("CirqSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "cirq-qaoa",
        "CirqQAOA — QUBO default",
        "Train CirqQAOA on the default QUBO-derived Hamiltonian, sample on QrispSimulator, then score it.",
        [
            ("QuboToHamiltonian", {}),
            (
                "CirqQAOA",
                {"Layers": "2", "Max Iterations": "150", "Random Seed": "11"},
            ),
            ("QrispSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "pennylane-qaoa",
        "PennylaneQAOA — Max Independent Set (3-node path)",
        "Train PennylaneQAOA on a 3-node path-graph Max Independent Set Hamiltonian, sample on QSharpSimulator, then score it.",
        [
            (
                "MaxIndependentSetProblem",
                {"Edges": "[[0, 1], [1, 2]]", "Penalty Factor": "2.0"},
            ),
            (
                "PennylaneQAOA",
                {"Layers": "2", "Max Iterations": "150", "Random Seed": "11"},
            ),
            ("QSharpSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "qrisp-qaoa",
        "QrispQAOA — portfolio rebalancing (XY mixer)",
        "Train QrispQAOA with the Hamming-weight-preserving XY mixer on the default portfolio rebalancing Hamiltonian, sample on PennylaneSimulator, then score it.",
        [
            ("PortfolioRebalancingProblem", {}),
            (
                "QrispQAOA",
                {
                    "Layers": "2",
                    "Mixer Type": "XY",
                    "Max Iterations": "100",
                    "Random Seed": "11",
                },
            ),
            ("PennylaneSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "pyquil-qaoa",
        "PyquilQAOA — two-node cut",
        "Train PyquilQAOA on -1/2 + 1/2 Z0 Z1 (cut states 01 and 10 have energy -1), sample on PyquilSimulator, then score it.",
        [
            (
                "QiskitHamiltonian",
                {"Hamiltonian": "-0.5 + 0.5 Z0 Z1", "Num Qubits": "2"},
            ),
            (
                "PyquilQAOA",
                {
                    "Layers": "1",
                    "Optimizer": "L_BFGS_B",
                    "Max Iterations": "100",
                    "Initial Parameters": "[-0.3,1]",
                    "Random Seed": "42",
                },
            ),
            ("PyquilSimulator", {"Shots": "256", "Random Seed": "42"}),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "qiskit-qaoa-circuit",
        "QiskitQAOACircuit — fixed N×M angles",
        "Build the bound QiskitQAOACircuit at the N×M matrix's fixed angles, sample on QiskitAerSimulator, then score it.",
        [
            ("QiskitHamiltonian", {"Hamiltonian": H_NXM, "Num Qubits": "3"}),
            (
                "QiskitQAOACircuit",
                {
                    "Layers": NXM_LAYERS_STR,
                    "Betas": NXM_BETAS_STR,
                    "Gammas": NXM_GAMMAS_STR,
                },
            ),
            ("QiskitAerSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "cirq-qaoa-circuit",
        "CirqQAOACircuit — fixed N×M angles",
        "Build the bound CirqQAOACircuit at the N×M matrix's fixed angles, sample on QSharpSimulator, then score it.",
        [
            ("CirqHamiltonian", {"Hamiltonian": H_NXM, "Num Qubits": "3"}),
            (
                "CirqQAOACircuit",
                {
                    "Layers": NXM_LAYERS_STR,
                    "Betas": NXM_BETAS_STR,
                    "Gammas": NXM_GAMMAS_STR,
                },
            ),
            ("QSharpSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "pennylane-qaoa-circuit",
        "PennylaneQAOACircuit — triangle MaxCut, default angles",
        "Build the bound PennylaneQAOACircuit at its default single-layer angles on the triangle MaxCut Hamiltonian, sample on QiskitAerSimulator, then score it.",
        [
            ("MaxCutProblem", {}),
            ("PennylaneQAOACircuit", {}),
            ("QiskitAerSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "qrisp-qaoa-circuit",
        "QrispQAOACircuit — fixed N×M angles",
        "Build the bound QrispQAOACircuit (RX mixer) at the N×M matrix's fixed angles, sample on CirqSimulator, then score it.",
        [
            ("QrispHamiltonian", {"Hamiltonian": H_NXM, "Num Qubits": "3"}),
            (
                "QrispQAOACircuit",
                {
                    "Layers": NXM_LAYERS_STR,
                    "Betas": NXM_BETAS_STR,
                    "Gammas": NXM_GAMMAS_STR,
                },
            ),
            ("CirqSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
    (
        "pyquil-qaoa-circuit",
        "PyquilQAOACircuit — Max Clique, default angles",
        "Build the bound PyquilQAOACircuit at its default single-layer angles on the 4-node Max Clique Hamiltonian, sample on QrispSimulator, then score it.",
        [
            (
                "MaxCliqueProblem",
                {
                    "Edges": "[[0, 1], [1, 2], [2, 0], [2, 3]]",
                    "Penalty Factor": "2.0",
                },
            ),
            ("PyquilQAOACircuit", {}),
            ("QrispSimulator", DEFAULT_ENGINE),
            ("QuantumQAOAEvaluator", {}),
        ],
    ),
]

BUILDERS = [
    "QiskitQAOACircuit",
    "CirqQAOACircuit",
    "PennylaneQAOACircuit",
    "QrispQAOACircuit",
    "PyquilQAOACircuit",
]

# 5 seeded engines at 4096 shots, Braket unseeded at 4096 shots, PyquilSimulator
# (the one slow engine, ~13.6 ms/shot) at 256 shots.
ENGINES = [
    ("QiskitAerSimulator", {"Shots": "4096", "Random Seed": "11"}),
    ("CirqSimulator", {"Shots": "4096", "Random Seed": "11"}),
    ("QrispSimulator", {"Shots": "4096", "Random Seed": "11"}),
    ("PennylaneSimulator", {"Shots": "4096", "Random Seed": "11"}),
    ("BraketSimulator", {"Shots": "4096"}),
    ("QSharpSimulator", {"Shots": "4096", "Random Seed": "11"}),
    ("PyquilSimulator", {"Shots": "256", "Random Seed": "11"}),
]


def uid(name):
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, "https://quanifi.local/qaoa-examples/" + name)
    )


def processor_class(name):
    import _harness  # noqa: F401 - initializes only the NiFi API stubs

    return getattr(importlib.import_module(name), name)


def node(key, name, kind, props, group, x, y, nifi_version):
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
    return {
        "identifier": uid(key),
        "instanceIdentifier": uid(key + "/instance"),
        "groupIdentifier": group,
        "name": name,
        "type": kind,
        "componentType": "PROCESSOR",
        "position": {"x": x, "y": y},
        "comments": "Local QAOA example; start downstream processors, then Run Once the trigger.",
        "bundle": {
            "group": "org.apache.nifi",
            "artifact": "nifi-standard-nar" if standard else "python-extensions",
            "version": version,
        },
        "properties": values,
        "propertyDescriptors": {},
        "style": {},
        "schedulingPeriod": "1 min" if standard else "0 sec",
        "schedulingStrategy": "TIMER_DRIVEN",
        "executionNode": "ALL",
        "penaltyDuration": "30 sec",
        "yieldDuration": "1 sec",
        "bulletinLevel": "WARN",
        "runDurationMillis": 0,
        "concurrentlySchedulableTaskCount": 1,
        "autoTerminatedRelationships": [] if standard else ["original"],
        "scheduledState": "ENABLED",
        "retryCount": 0,
        "retriedRelationships": [],
        "backoffMechanism": "PENALIZE_FLOWFILE",
        "maxBackoffPeriod": "10 mins",
    }


def connection(key, source, destination, group, relationship="success"):
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
        "name": relationship,
        "source": ref(source),
        "destination": ref(destination),
        "selectedRelationships": [relationship],
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


def _empty_group(gid, name, comments, nifi_version=None):
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


def lanes_snapshot(nifi_version="2.9.0"):
    """One lane per QAOA processor (5 solvers + 5 builders); 10 rows of
    500 px, each with its own failure funnel and title label, same layout
    as build_pyquil_examples.py's snapshot()."""
    gid = uid("lanes/group")
    group = _empty_group(
        gid,
        "QAOA — one lane per processor",
        "Ten independent QAOA lanes: five train-only solvers and five fixed-angle "
        "builders, each ending in QuantumQAOAEvaluator then QuanifiReport. Start "
        "downstream processors, then Run Once each trigger. Failure funnels retain "
        "failed FlowFiles.",
    )
    for row, (key, title, description, steps) in enumerate(LANES):
        y = row * 500
        specs = (
            [
                (
                    "org.apache.nifi.processors.standard.GenerateFlowFile",
                    {
                        "File Size": "0B",
                        "Batch Size": "1",
                        "Data Format": "Text",
                        "Unique FlowFiles": "false",
                    },
                )
            ]
            + steps
            + [
                (
                    "QuanifiReport",
                    {
                        "Flow Name": f"qaoa-{key}",
                        "Reports Directory": "reports/qaoa",
                    },
                )
            ]
        )
        nodes = []
        for i, (kind, props) in enumerate(specs):
            name = f"Start {title}" if i == 0 else kind
            nodes.append(
                node(
                    f"lanes/{key}/{i}",
                    name,
                    kind,
                    props,
                    gid,
                    i * 470,
                    y + 90,
                    nifi_version,
                )
            )
        nodes[-1]["autoTerminatedRelationships"].append("success")
        group["processors"].extend(nodes)
        group["connections"].extend(
            connection(f"lanes/{key}/success/{i}", a, b, gid)
            for i, (a, b) in enumerate(zip(nodes, nodes[1:]))
        )
        funnel = {
            "identifier": uid(f"lanes/{key}/failure"),
            "instanceIdentifier": uid(f"lanes/{key}/failure/instance"),
            "groupIdentifier": gid,
            "componentType": "FUNNEL",
            "name": f"{title} failures",
            "position": {"x": len(specs) * 470, "y": y + 310},
        }
        group["funnels"].append(funnel)
        group["connections"].extend(
            connection(f"lanes/{key}/failure/{i}", p, funnel, gid, "failure")
            for i, p in enumerate(nodes[1:])
        )
        group["labels"].append(
            {
                "identifier": uid(f"lanes/{key}/label"),
                "instanceIdentifier": uid(f"lanes/{key}/label/instance"),
                "groupIdentifier": gid,
                "componentType": "LABEL",
                "position": {"x": 0, "y": y},
                "zIndex": 0,
                "width": len(specs) * 470 - 60,
                "height": 65,
                "label": title + " — " + description,
                "style": {"background-color": "#eaf2ff", "font-size": "18px"},
            }
        )
    return {
        "flowContents": group,
        "flowEncodingVersion": "1.0",
        "parameterContexts": {},
        "externalControllerServices": {},
        "parameterProviders": {},
    }


def nxm_snapshot(nifi_version="2.9.0"):
    """5 fixed-angle builders x 7 counts engines: one shared Hamiltonian
    fans out to the 5 builders, every builder fans out to all 7 engines,
    and every engine feeds one shared QuantumQAOAEvaluator -> QuanifiReport.
    Columns at x = 0 (trigger), 470 (Hamiltonian), 940 (builders, y step
    220), 1410 (engines, y step 220), 1880 (evaluator), 2350 (report); one
    failure funnel below; one title label at the top.
    """
    gid = uid("nxm/group")
    group = _empty_group(
        gid,
        "QAOA N×M — 5 builders × 7 engines",
        "One Hamiltonian, five fixed-angle QAOA builders (the NxM rows), seven "
        "counts engines (the NxM columns), one shared QuantumQAOAEvaluator and "
        "QuanifiReport. Start downstream processors, then Run Once the trigger. "
        "The single failure funnel retains failed FlowFiles from any of the 15 "
        "non-trigger processors.",
    )
    y0 = 90
    step = 220

    trigger = node(
        "nxm/trigger",
        "Start QAOA N×M",
        "org.apache.nifi.processors.standard.GenerateFlowFile",
        {
            "File Size": "0B",
            "Batch Size": "1",
            "Data Format": "Text",
            "Unique FlowFiles": "false",
        },
        gid,
        0,
        y0,
        nifi_version,
    )
    ham = node(
        "nxm/hamiltonian",
        "QiskitHamiltonian",
        "QiskitHamiltonian",
        {"Hamiltonian": H_NXM, "Num Qubits": "3"},
        gid,
        470,
        y0,
        nifi_version,
    )
    processors = [trigger, ham]
    connections = [connection("nxm/trigger-hamiltonian", trigger, ham, gid)]

    builder_nodes = []
    for i, name in enumerate(BUILDERS):
        n = node(
            f"nxm/builder/{name}",
            name,
            name,
            {
                "Layers": NXM_LAYERS_STR,
                "Betas": NXM_BETAS_STR,
                "Gammas": NXM_GAMMAS_STR,
            },
            gid,
            940,
            y0 + i * step,
            nifi_version,
        )
        builder_nodes.append(n)
        processors.append(n)
        connections.append(connection(f"nxm/hamiltonian-builder/{i}", ham, n, gid))

    engine_nodes = []
    for i, (name, props) in enumerate(ENGINES):
        n = node(
            f"nxm/engine/{name}",
            name,
            name,
            props,
            gid,
            1410,
            y0 + i * step,
            nifi_version,
        )
        engine_nodes.append(n)
        processors.append(n)

    for b in builder_nodes:
        for e in engine_nodes:
            connections.append(
                connection(f"nxm/edge/{b['type']}/{e['type']}", b, e, gid)
            )

    mid_y = y0 + (len(ENGINES) - 1) * step // 2
    evaluator = node(
        "nxm/evaluator",
        "QuantumQAOAEvaluator",
        "QuantumQAOAEvaluator",
        {},
        gid,
        1880,
        mid_y,
        nifi_version,
    )
    processors.append(evaluator)
    for i, e in enumerate(engine_nodes):
        connections.append(connection(f"nxm/engine-evaluator/{i}", e, evaluator, gid))

    report = node(
        "nxm/report",
        "QuanifiReport",
        "QuanifiReport",
        {"Flow Name": "qaoa-nxm", "Reports Directory": "reports/qaoa"},
        gid,
        2350,
        mid_y,
        nifi_version,
    )
    report["autoTerminatedRelationships"].append("success")
    processors.append(report)
    connections.append(connection("nxm/evaluator-report", evaluator, report, gid))

    funnel = {
        "identifier": uid("nxm/failure"),
        "instanceIdentifier": uid("nxm/failure/instance"),
        "groupIdentifier": gid,
        "componentType": "FUNNEL",
        "name": "QAOA N×M failures",
        "position": {"x": 940, "y": y0 + len(ENGINES) * step + 80},
    }
    non_trigger = [ham] + builder_nodes + engine_nodes + [evaluator, report]
    connections.extend(
        connection(f"nxm/failure/{i}", p, funnel, gid, "failure")
        for i, p in enumerate(non_trigger)
    )

    group["processors"] = processors
    group["connections"] = connections
    group["funnels"] = [funnel]
    group["labels"] = [
        {
            "identifier": uid("nxm/label"),
            "instanceIdentifier": uid("nxm/label/instance"),
            "groupIdentifier": gid,
            "componentType": "LABEL",
            "position": {"x": 0, "y": 0},
            "zIndex": 0,
            "width": 2350,
            "height": 65,
            "label": "QAOA N×M — 5 builders × 7 engines: does every construction "
            "and every counts engine agree with the exact distribution?",
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


def execute_lanes(output):
    from _harness import MockContext, MockFlowFile
    from conftest import result_to_flowfile_merged

    lanes_dir = output / "lanes"
    lanes_dir.mkdir(parents=True, exist_ok=True)

    records = {}
    for key, title, description, steps in LANES:
        ff = MockFlowFile()
        stages = []
        for name, props in steps:
            r = processor_class(name)().transform(MockContext(**props), ff)
            if r.relationship != "success":
                raise RuntimeError(f"{key}/{name}: {r.attributes}")
            ff = result_to_flowfile_merged(r, ff)
            stages.append(
                {"processor": name, "properties": props, "relationship": r.relationship}
            )
        attrs = ff.getAttributes()
        record = {
            "description": description,
            "stages": stages,
            "counts": json.loads(bytes(ff.getContentsAsBytes())),
            "attributes": {
                k: v
                for k, v in attrs.items()
                if k.startswith(("qaoa.", "sim.", "builder.", "run."))
            },
        }
        record["attributes"].pop("qaoa.elapsed_seconds", None)
        record["attributes"].pop("perf.elapsed_seconds", None)
        source = attrs.get("circuit.qasm2")
        if source:
            (lanes_dir / f"{key}.qasm").write_text(source)
        (lanes_dir / f"{key}.json").write_text(json.dumps(record, indent=2) + "\n")
        records[key] = record
        (lanes_dir / f"qaoa-{key}.html").unlink(missing_ok=True)
        r = processor_class("QuanifiReport")().transform(
            MockContext(
                **{"Flow Name": f"qaoa-{key}", "Reports Directory": str(lanes_dir)}
            ),
            ff,
        )
        if r.relationship != "success":
            raise RuntimeError(f"{key}/Report: {r.attributes}")
    return records


def execute_nxm(output, engines=None):
    from _harness import MockContext, MockFlowFile
    from conftest import result_to_flowfile_merged
    import qaoa_reference as qref

    nxm_dir = output / "nxm"
    nxm_dir.mkdir(parents=True, exist_ok=True)

    engine_list = (
        ENGINES
        if engines is None
        else [(name, props) for name, props in ENGINES if name in engines]
    )

    ref_terms, ref_n = qref.terms_of(H_NXM, 3)
    ref_probs = qref.reference_probabilities(ref_terms, ref_n, NXM_BETAS, NXM_GAMMAS)

    ham_res = processor_class("QiskitHamiltonian")().transform(
        MockContext(**{"Hamiltonian": H_NXM, "Num Qubits": "3"}), MockFlowFile()
    )
    if ham_res.relationship != "success":
        raise RuntimeError(f"QiskitHamiltonian: {ham_res.attributes}")
    ham_ff = result_to_flowfile_merged(ham_res, MockFlowFile())

    rows = []
    for builder in BUILDERS:
        b_res = processor_class(builder)().transform(
            MockContext(
                **{
                    "Layers": NXM_LAYERS_STR,
                    "Betas": NXM_BETAS_STR,
                    "Gammas": NXM_GAMMAS_STR,
                }
            ),
            ham_ff,
        )
        if b_res.relationship != "success":
            raise RuntimeError(f"{builder}: {b_res.attributes}")
        builder_ff = result_to_flowfile_merged(b_res, ham_ff)
        b_attrs = builder_ff.getAttributes()
        (nxm_dir / f"{builder}.qasm").write_text(b_attrs["circuit.qasm2"])

        for engine, props in engine_list:
            e_res = processor_class(engine)().transform(
                MockContext(**props), builder_ff
            )
            if e_res.relationship != "success":
                raise RuntimeError(f"{builder}/{engine}: {e_res.attributes}")
            merged = result_to_flowfile_merged(e_res, builder_ff)
            ev_res = processor_class("QuantumQAOAEvaluator")().transform(
                MockContext(), merged
            )
            if ev_res.relationship != "success":
                raise RuntimeError(f"{builder}/{engine}/evaluator: {ev_res.attributes}")

            counts = json.loads(bytes(e_res.contents))
            row = {
                "builder": builder,
                "builder_framework": b_attrs.get("builder.framework", ""),
                "engine": engine,
                "engine_framework": merged.getAttribute("sim.framework") or "",
                "shots": props.get("Shots", ""),
                "seed": merged.getAttribute("run.seed") or "",
                "counts": counts,
                "hellinger": qref.hellinger(counts, ref_probs),
                "qaoa.best_measurement": ev_res.attributes.get("qaoa.best_measurement"),
                "sampled_expectation": ev_res.attributes.get(
                    "qaoa.sampled_expectation"
                ),
                "approximation_ratio": ev_res.attributes.get(
                    "qaoa.approximation_ratio"
                ),
                "optimal_probability": ev_res.attributes.get(
                    "qaoa.optimal_probability"
                ),
                "circuit.gate_counts": json.loads(
                    b_attrs.get("circuit.gate_counts") or "{}"
                ),
                "circuit.depth": b_attrs.get("circuit.depth", ""),
            }
            rows.append(row)

    (nxm_dir / "summary.json").write_text(json.dumps(rows, indent=2) + "\n")

    engine_names = [name for name, _ in engine_list]
    lines = [
        "# QAOA N×M matrix — Hellinger distance to the exact distribution",
        "",
        "H_NXM = `-0.8 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2`, Layers 2, "
        "Betas [-0.67, -0.42], Gammas [1.14, 1.27]. Ground state `001`.",
        "",
        "| builder \\ engine | " + " | ".join(engine_names) + " |",
        "|---" * (len(engine_names) + 1) + "|",
    ]
    by_cell = {(r["builder"], r["engine"]): r for r in rows}
    for builder in BUILDERS:
        cells = []
        for engine in engine_names:
            cell = by_cell.get((builder, engine))
            cells.append("%.4f" % cell["hellinger"] if cell else "-")
        lines.append("| " + builder + " | " + " | ".join(cells) + " |")
    lines += [
        "",
        "Notes: BraketSimulator is unseeded (statistical agreement only, never "
        "compared cell-for-cell against a saved value). PyquilSimulator runs at "
        "256 shots (~13.6 ms/shot); every other engine runs at 4096 shots.",
    ]
    (nxm_dir / "summary.md").write_text("\n".join(lines) + "\n")

    (nxm_dir / "qaoa-nxm.html").unlink(missing_ok=True)
    report_res = processor_class("QuanifiReport")().transform(
        MockContext(**{"Flow Name": "qaoa-nxm", "Reports Directory": str(nxm_dir)}),
        result_to_flowfile_merged(ev_res, merged) if rows else MockFlowFile(),
    )
    if rows and report_res.relationship != "success":
        raise RuntimeError(f"N×M Report: {report_res.attributes}")

    return rows


def overview(output, lane_records=None, nxm_rows=None):
    cards = []
    for key, title, description, steps in LANES:
        nodes = (
            [("GenerateFlowFile", {"Action": "Run Once"})]
            + steps
            + [("QuanifiReport", {"Report": f"lanes/qaoa-{key}.html"})]
        )
        boxes = []
        for name, props in nodes:
            settings = "".join(
                f"<div><b>{html.escape(k)}</b>: {html.escape(str(v))}</div>"
                for k, v in props.items()
            )
            boxes.append(
                f'<article><strong>{name}</strong><div class="settings">{settings}</div></article>'
            )
        result = ""
        if lane_records:
            r = lane_records[key]
            a = r["attributes"]
            best = a.get("qaoa.best_measurement", "")
            ratio = a.get("qaoa.approximation_ratio", "")
            optimal_value = a.get("qaoa.optimal_value")
            bits = [
                f"best <b>{html.escape(best)}</b>",
                f"approximation ratio <b>{html.escape(ratio)}</b>",
            ]
            if optimal_value:
                bits.append(f"optimal value <b>{float(optimal_value):.6f}</b>")
            result = (
                '<p class="result">Executed locally · '
                + " · ".join(bits)
                + f' · <a href="lanes/{key}.json">counts and settings</a> · '
                f'<a href="lanes/qaoa-{key}.html">report</a></p>'
            )
        cards.append(
            f'<section><h2>{title}</h2><p>{description}</p><div class="lane">'
            + '<span class="arrow">→</span>'.join(boxes)
            + f'</div><p class="failure">Each quantum processor/report failure → failure funnel (retained for inspection).</p>{result}</section>'
        )

    nxm_card = ""
    if nxm_rows:
        engine_names = [name for name, _ in ENGINES]
        by_cell = {(r["builder"], r["engine"]): r for r in nxm_rows}
        rows_html = ""
        for builder in BUILDERS:
            cells = ""
            for engine in engine_names:
                cell = by_cell.get((builder, engine))
                text = "%.4f" % cell["hellinger"] if cell else "—"
                cells += f"<td>{text}</td>"
            rows_html += f"<tr><th>{builder}</th>{cells}</tr>"
        header = "".join(f"<th>{e}</th>" for e in engine_names)
        nxm_card = (
            "<section><h2>QAOA N×M — 5 builders × 7 engines</h2>"
            "<p>Hellinger distance to the exact distribution for "
            "H_NXM = -0.8 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2, Layers 2, "
            "Betas [-0.67, -0.42], Gammas [1.14, 1.27]. Lower is better; "
            "BraketSimulator is unseeded, PyquilSimulator runs at 256 shots "
            "(all others at 4096).</p>"
            f'<table class="nxm"><tr><th></th>{header}</tr>{rows_html}</table>'
            '<p><a href="nxm/summary.json">full matrix</a> · '
            '<a href="nxm/summary.md">markdown table</a> · '
            '<a href="nxm/qaoa-nxm.html">report</a></p></section>'
        )
    elif (output / "nxm" / "summary.md").exists():
        nxm_card = (
            "<section><h2>QAOA N×M — 5 builders × 7 engines</h2>"
            '<p><a href="nxm/summary.json">full matrix</a> · '
            '<a href="nxm/summary.md">markdown table</a> · '
            '<a href="nxm/qaoa-nxm.html">report</a></p></section>'
        )

    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QAOA component canvas</title><style>
body{font:16px/1.55 system-ui;background:#f3f6fa;color:#182b40;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:38px}section{padding:24px;background:white;border:1px solid #cdd8e6;border-radius:14px;margin:26px 0}.lane{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:20px 0}article{min-width:195px;flex:1;border:2px solid #427fbe;border-radius:8px;padding:14px;background:#eff6ff}article strong{font-size:14px;overflow-wrap:anywhere}.settings{font-size:12px;margin-top:14px}.settings div{padding:3px 0;overflow-wrap:anywhere}.arrow{align-self:center;color:#427fbe;font-size:26px}.failure{color:#994b21;font-size:13px}.result{background:#e8f7ef;padding:14px;border-radius:8px}a{color:#1d5d9b}code{background:#e8edf4;padding:2px 6px}table.nxm{border-collapse:collapse;font-size:13px}table.nxm th,table.nxm td{border:1px solid #cdd8e6;padding:6px 10px;text-align:right}table.nxm th{background:#eaf2ff;text-align:center}table.nxm th:first-child{text-align:left}@media(max-width:600px){body{padding:15px}.lane{flex-direction:column}.arrow{transform:rotate(90deg)}article{min-width:0}}@media print{body{padding:0}.lane{overflow:visible}section{break-inside:avoid}}
</style><main><h1>QAOA on the Quanifi canvas</h1><p>Ten reusable lanes (one per QAOA processor) plus an N×M matrix (5 builders × 7 engines), built from the same settings as the two downloadable NiFi snapshots.</p><p><a href="qaoa-lanes.json">Download the lanes flow</a> · <a href="qaoa-nxm.json">Download the N×M flow</a> · <a href="../../docs/guides/QAOA_COMPONENTS.md">Setup and processor reference</a></p><p>All processors import stopped. Start downstream components, then use <b>Run Once</b> on each trigger. Local results below exercise the real processors using NiFi API stubs; they are not a screenshot or a claim of live NiFi execution.</p>"""
        + "".join(cards)
        + nxm_card
        + """<p>Every builder and solver emits portable OpenQASM 2.0 plus hamiltonian.json, so any of the seven counts engines can sample it and QuantumQAOAEvaluator can score it. No provider account is required for any lane above.</p></main></html>"""
    )
    (output / "index.html").write_text(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/qaoa")
    parser.add_argument(
        "--nifi-version",
        default="2.9.0",
        help="Version of the installed nifi-standard-nar bundle",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute all ten lanes locally and save reports/counts",
    )
    parser.add_argument(
        "--nxm",
        action="store_true",
        help="Execute the 5x7 builder x engine matrix locally and save the summary",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "qaoa-lanes.json").write_text(
        json.dumps(lanes_snapshot(args.nifi_version), indent=2) + "\n"
    )
    (args.output / "qaoa-nxm.json").write_text(
        json.dumps(nxm_snapshot(args.nifi_version), indent=2) + "\n"
    )
    lane_records = execute_lanes(args.output) if args.run else None
    nxm_rows = execute_nxm(args.output) if args.nxm else None
    overview(args.output, lane_records, nxm_rows)
    print(f"Wrote QAOA example flows to {args.output}")


if __name__ == "__main__":
    main()
