#!/usr/bin/env python3
"""Generate a stopped NiFi flow snapshot and run its three examples locally.

Uses one set of processor configurations for the importable canvas and the
headless execution. No NiFi server, provider jobs or credentials are needed.
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

PIPELINES = [
    (
        "grover",
        "Modular Grover",
        "Oracle → amplification → measurement. Target 10 has probability 1.",
        [
            ("PyquilPhaseOracle", {"Marked State": "10"}),
            ("PyquilGroverOperator", {"Num Iterations": "1"}),
            ("PyquilSimulator", {"Shots": "256", "Random Seed": "42"}),
        ],
    ),
    (
        "vqe",
        "Variational eigensolver",
        "Hamiltonian → reusable trial state → optimization. Ground energy −√1.25 ≈ −1.118034.",
        [
            ("QiskitHamiltonian", {"Hamiltonian": "Z0 + 0.5 X0", "Num Qubits": "1"}),
            ("PyquilAnsatz", {"Output Mode": "attach", "Reps": "0", "Rotations": "ry"}),
            (
                "PyquilVQE",
                {
                    "Optimizer": "L_BFGS_B",
                    "Max Iterations": "100",
                    "Initial Parameters": "[2]",
                    "Shots": "256",
                    "Random Seed": "42",
                },
            ),
        ],
    ),
    (
        "qaoa",
        "QAOA for a two-node cut",
        "Minimize −½ + ½ Z0 Z1, sample the trained circuit, then score it. The two cut states 01 and 10 have energy −1.",
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
]


def uid(name):
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, "https://quanifi.local/pyquil-examples/" + name)
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
        "comments": "Local pyQuil example; run the trigger once.",
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


def snapshot(nifi_version="2.9.0"):
    gid = uid("group")
    group = {
        "identifier": gid,
        "instanceIdentifier": uid("group/instance"),
        "name": "pyQuil — Grover, VQE and QAOA",
        "componentType": "PROCESS_GROUP",
        "position": {"x": 0, "y": 0},
        "comments": "Native pyQuil example flows. Start downstream processors, then Run Once each trigger. Failure funnels retain failed FlowFiles.",
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
    for row, (key, title, description, steps) in enumerate(PIPELINES):
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
                        "Flow Name": f"pyquil-{key}",
                        "Reports Directory": "reports/pyquil",
                    },
                )
            ]
        )
        nodes = []
        for i, (kind, props) in enumerate(specs):
            name = f"Start {title}" if i == 0 else kind
            nodes.append(
                node(
                    f"{key}/{i}", name, kind, props, gid, i * 470, y + 90, nifi_version
                )
            )
        nodes[-1]["autoTerminatedRelationships"].append("success")
        group["processors"].extend(nodes)
        group["connections"].extend(
            connection(f"{key}/success/{i}", a, b, gid)
            for i, (a, b) in enumerate(zip(nodes, nodes[1:]))
        )
        funnel = {
            "identifier": uid(key + "/failure"),
            "instanceIdentifier": uid(key + "/failure/instance"),
            "groupIdentifier": gid,
            "componentType": "FUNNEL",
            "name": f"{title} failures",
            "position": {"x": 940, "y": y + 310},
        }
        group["funnels"].append(funnel)
        group["connections"].extend(
            connection(f"{key}/failure/{i}", p, funnel, gid, "failure")
            for i, p in enumerate(nodes[1:])
        )
        group["labels"].append(
            {
                "identifier": uid(key + "/label"),
                "instanceIdentifier": uid(key + "/label/instance"),
                "groupIdentifier": gid,
                "componentType": "LABEL",
                "position": {"x": 0, "y": y},
                "zIndex": 0,
                "width": 2200,
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


def execute(output):
    from _harness import MockContext, MockFlowFile
    from conftest import result_to_flowfile_merged

    records = {}
    for key, title, description, steps in PIPELINES:
        ff = MockFlowFile()
        stages = []
        for name, props in steps:
            r = processor_class(name)().transform(MockContext(**props), ff)
            if r.relationship != "success":
                raise RuntimeError(f"{name}: {r.attributes}")
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
                if k.startswith(("sim.", "vqe.", "qaoa.", "run."))
            },
        }
        record["attributes"].pop("perf.elapsed_seconds", None)
        record["attributes"].pop("qaoa.elapsed_seconds", None)
        source = attrs.get("circuit.qasm2")
        if source:
            (output / f"{key}.qasm").write_text(source)
        (output / f"{key}.json").write_text(json.dumps(record, indent=2) + "\n")
        records[key] = record
        (output / f"pyquil-{key}.html").unlink(missing_ok=True)
        r = processor_class("QuanifiReport")().transform(
            MockContext(
                **{"Flow Name": f"pyquil-{key}", "Reports Directory": str(output)}
            ),
            ff,
        )
        if r.relationship != "success":
            raise RuntimeError(f"Report: {r.attributes}")
    return records


def overview(output, records=None):
    cards = []
    for key, title, description, steps in PIPELINES:
        nodes = (
            [("GenerateFlowFile", {"Action": "Run Once"})]
            + steps
            + [("QuanifiReport", {"Report": f"pyquil-{key}.html"})]
        )
        boxes = []
        for name, props in nodes:
            settings = "".join(
                f"<div><b>{html.escape(k)}</b>: {html.escape(v)}</div>"
                for k, v in props.items()
            )
            boxes.append(
                f'<article><strong>{name}</strong><div class="settings">{settings}</div></article>'
            )
        result = ""
        if records:
            r = records[key]
            value = r["attributes"].get(
                f"{key}.optimal_value", r["attributes"].get("sim.top_result")
            )
            if key in ("vqe", "qaoa"):
                value = f"{float(value):.8f}"
            result = f'<p class="result">Executed locally · result: <b>{html.escape(value)}</b> · <a href="{key}.json">counts and settings</a> · <a href="pyquil-{key}.html">report</a></p>'
        cards.append(
            f'<section><h2>{title}</h2><p>{description}</p><div class="lane">'
            + '<span class="arrow">→</span>'.join(boxes)
            + f'</div><p class="failure">Each quantum processor/report failure → failure funnel (retained for inspection).</p>{result}</section>'
        )
    page = (
        """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>pyQuil component canvas</title><style>
body{font:16px/1.55 system-ui;background:#f3f6fa;color:#182b40;margin:0;padding:32px}main{max-width:1500px;margin:auto}h1{font-size:38px}section{padding:24px;background:white;border:1px solid #cdd8e6;border-radius:14px;margin:26px 0}.lane{display:flex;align-items:stretch;gap:10px;overflow:auto;padding:20px 0}article{min-width:195px;flex:1;border:2px solid #427fbe;border-radius:8px;padding:14px;background:#eff6ff}article strong{font-size:14px;overflow-wrap:anywhere}.settings{font-size:12px;margin-top:14px}.settings div{padding:3px 0;overflow-wrap:anywhere}.arrow{align-self:center;color:#427fbe;font-size:26px}.failure{color:#994b21;font-size:13px}.result{background:#e8f7ef;padding:14px;border-radius:8px}a{color:#1d5d9b}code{background:#e8edf4;padding:2px 6px}@media(max-width:600px){body{padding:15px}.lane{flex-direction:column}.arrow{transform:rotate(90deg)}article{min-width:0}}@media print{body{padding:0}.lane{overflow:visible}section{break-inside:avoid}}
</style><main><h1>pyQuil on the Quanifi canvas</h1><p>Three reusable flows, built from the same settings as the downloadable NiFi snapshot.</p><p><a href="pyquil-examples.json">Download the importable flow</a> · <a href="../../docs/guides/PYQUIL_COMPONENTS.md">Setup and processor reference</a></p><p>All processors import stopped. Start downstream components, then use <b>Run Once</b> on each trigger. Local results below exercise the real processors using NiFi API stubs; they are not a screenshot or a claim of live NiFi execution.</p>"""
        + "".join(cards)
        + """<p>QiskitHamiltonian supplies the shared Hamiltonian JSON only. Trial circuits, expectations and optimization use pyQuil and SciPy locally. No Forest server or provider account is required.</p></main></html>"""
    )
    (output / "index.html").write_text(page)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "demo/pyquil")
    parser.add_argument(
        "--nifi-version",
        default="2.9.0",
        help="Version of the installed nifi-standard-nar bundle",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute all three flows locally and save reports/counts",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "pyquil-examples.json").write_text(
        json.dumps(snapshot(args.nifi_version), indent=2) + "\n"
    )
    records = execute(args.output) if args.run else None
    overview(args.output, records)
    print(f"Wrote three example flows to {args.output}")


if __name__ == "__main__":
    main()
