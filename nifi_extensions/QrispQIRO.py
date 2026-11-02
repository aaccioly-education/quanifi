# Quanifi — quantum-computing components for Apache NiFi
# Copyright (C) 2026 Neilson Ramalho
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License, version 3, as published by
# the Free Software Foundation. This program is distributed WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the license along with this program; if not,
# see <https://www.gnu.org/licenses/>. Commercial licensing is also available:
# see COMMERCIAL.md at the repository root.

"""Qrisp QIRO (Quantum-Informed Recursive Optimization) for Maximum
Independent Set, as a train-only solver with the same contract as
``QrispQAOA``. The algorithm lives in ``qrisp_qiro.py``.
"""

import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import qrisp_qiro

_OPTIMIZERS = ["COBYLA", "NELDER_MEAD", "POWELL"]


class QrispQIRO(FlowFileTransform):
    """
    Quantum-Informed Recursive Optimization (Finzgar et al., PRX Quantum 5,
    020327) for Maximum Independent Set, built on Qrisp's own QIRO components.

    QIRO runs QAOA, reads the strongest one- or two-qubit correlation from the
    result, uses it to fix or remove vertices (shrinking the graph), and
    repeats on the smaller problem. Quanifi wraps Qrisp's components
    unmodified (notably ``create_max_indep_replacement_routine``); it only
    adds a seeded initial point per stage, exact probabilities, and a step
    trace.

    Pipeline: MaxIndependentSetProblem -> QrispQIRO -> <any counts simulator>
    -> QuantumQAOAEvaluator -> QuanifiReport. Train-only: it does not sample.
    The output circuit is the final stage's trained circuit over all n
    qubits (fixed vertices prepared with X, removed vertices left in |0>),
    and ``hamiltonian.json`` stays the ORIGINAL problem, so the evaluator
    scores against the original graph. ``qiro.*`` attributes carry the trace.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Quantum-Informed Recursive Optimization (QIRO) for Maximum Independent Set "
            "using Qrisp's own QIRO components: train a QAOA, fix or remove vertices "
            "from the strongest correlation, shrink the graph and repeat. Emits the "
            "final stage's trained circuit as portable OpenQASM 2.0 plus a qiro.* step "
            "trace. It does not sample: connect any counts simulator, then "
            "QuantumQAOAEvaluator."
        )
        tags = [
            "quantum",
            "qrisp",
            "qiro",
            "qaoa",
            "optimization",
            "max_independent_set",
        ]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5", "networkx"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        from nifiapi.properties import (
            ExpressionLanguageScope,
            PropertyDescriptor,
            StandardValidators,
        )

        el = ExpressionLanguageScope.FLOWFILE_ATTRIBUTES
        self.descriptors = [
            PropertyDescriptor(
                name="Layers",
                description="QAOA depth p used in every QIRO stage, 1..16.",
                required=True,
                default_value="3",
                validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
                expression_language_scope=el,
            ),
            PropertyDescriptor(
                name="Recursions",
                description=(
                    "Number of QIRO reduction steps (each fixes or removes vertices, "
                    "then re-trains on the smaller graph), 0..n-1. 0 is plain QAOA."
                ),
                required=True,
                default_value="3",
                validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
                expression_language_scope=el,
            ),
            PropertyDescriptor(
                name="Max Iterations",
                description="Optimizer iterations per stage (Qrisp's default is 50).",
                required=True,
                default_value="50",
                validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
                expression_language_scope=el,
            ),
            PropertyDescriptor(
                name="Optimizer",
                description="Classical optimizer driving the angle search of each stage.",
                required=True,
                default_value="COBYLA",
                allowable_values=list(_OPTIMIZERS),
            ),
            PropertyDescriptor(
                name="Random Seed",
                description=(
                    "Seed for the initial angles of every stage, 0..4294967295. "
                    "Unset = nondeterministic."
                ),
                required=False,
                default_value="11",
                validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
                expression_language_scope=el,
            ),
            PropertyDescriptor(
                name="Edges",
                description=(
                    "Optional JSON edge list override, e.g. [[0, 1], [1, 2]]. Empty = "
                    "derive the graph from the upstream Hamiltonian (every two-qubit "
                    "Z-Z term is an edge; nodes are 0..n-1)."
                ),
                required=False,
                validators=[],
                expression_language_scope=el,
            ),
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    def _run(self, context, flowfile):
        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")

        problem_type = (flowfile.getAttribute("hamiltonian.problem_type") or "").strip()
        if problem_type and problem_type != qrisp_qiro.MIS:
            raise ValueError(
                "QrispQIRO supports hamiltonian.problem_type=max_independent_set "
                "only, got {!r}".format(problem_type)
            )

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)
        recursions = qc.parse_int(
            props["Recursions"], "Recursions", 0, max(n - 1, 0)
        )

        spec = (props.get("Edges") or "").strip()
        edges = (
            qrisp_qiro.parse_edges(spec, n)
            if spec
            else qrisp_qiro.edges_from_terms(terms)
        )

        result = qrisp_qiro.run_quietly(
            n, edges, layers, recursions, maxiter, opt_name, seed
        )
        with contextlib.redirect_stdout(io.StringIO()):
            source, diagram = qrisp_qiro.export(result, n, layers)
        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        steps = result["steps"]
        top_bits, top_p = result["top"]
        evals = sum(result["stage_evals"])
        converged = all(e < maxiter for e in result["stage_evals"])
        betas, gammas = result["betas"], result["gammas"]

        attrs = {
            **qc.circuit_attributes(source, profile, "qrisp", "QrispQIRO", diagram),
            **qc.builder_attributes(raw, n, layers, betas, gammas, mixer="QIRO_RX"),
            **qc.solver_attributes(
                framework="qrisp",
                betas=betas,
                gammas=gammas,
                optimal_value=result["optimal_value"],
                energy_method="exact_probabilities",
                energy_shots="",
                optimizer=opt_name,
                max_iterations=maxiter,
                cost_function_evals=evals,
                num_iterations=evals,
                converged=converged,
                optimizer_message=result["stop_note"],
                seed=seed,
                elapsed_seconds=result["elapsed"],
            ),
            "qiro.problem_type": qrisp_qiro.MIS,
            "qiro.recursions_requested": str(recursions),
            "qiro.recursions_done": str(len(steps)),
            "qiro.steps": json.dumps(steps),
            "qiro.fixed_in_set": json.dumps(result["fixed"]),
            "qiro.excluded": json.dumps(result["excluded"]),
            "qiro.remaining_nodes": json.dumps(result["remaining_nodes"]),
            "qiro.remaining_edges": json.dumps(result["remaining_edges"]),
            "qiro.active_qubits": json.dumps(result["remaining_nodes"]),
            "qiro.graph_edges": json.dumps([list(e) for e in edges]),
            "qiro.internal_top": json.dumps(
                {
                    "bitstring": top_bits,
                    "probability": float(top_p),
                    "vertices": [i for i, c in enumerate(top_bits) if c == "1"],
                }
            ),
            "qiro.stage_energies": json.dumps(result["stage_energies"]),
            "qiro.replacement_routine": qrisp_qiro.REPLACEMENT_ROUTINE,
            "qiro.trace_svg": qrisp_qiro.trace_svg(n, edges, steps),
        }
        return qc.success(source.encode(), attrs)
