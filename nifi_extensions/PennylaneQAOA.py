"""Trains a p-layer QAOA ansatz using PennyLane's ``qml.qaoa`` layers
(``cost_layer``/``mixer_layer``/``x_mixer``) with exact analytic
expectations driven by scipy.optimize.minimize, and emits the trained,
bound circuit as portable OpenQASM 2.0 (the same construction as
``PennylaneQAOACircuit``, via ``pennylane_qaoa.export``), plus
``qaoa.optimal_parameters`` (betas then gammas). Train-only: it does not
sample. Connect any counts simulator, then ``QuantumQAOAEvaluator``.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import pennylane_qaoa

_OPTIMIZERS = ["COBYLA", "NELDER_MEAD", "POWELL", "L_BFGS_B"]

# scipy.optimize.minimize method per optimizer name
_SCIPY_METHOD = {
    "COBYLA": "COBYLA",
    "NELDER_MEAD": "Nelder-Mead",
    "POWELL": "Powell",
    "L_BFGS_B": "L-BFGS-B",
}


class PennylaneQAOA(FlowFileTransform):
    """
    Quantum Approximate Optimization Algorithm solver for PennyLane, built on
    the ``qml.qaoa`` module (``cost_layer`` / ``mixer_layer`` / ``x_mixer``,
    shared with ``PennylaneQAOACircuit`` via ``pennylane_qaoa.py``).

    Solver stage of <any>Hamiltonian -> PennylaneQAOA -> <any simulator> ->
    QuantumQAOAEvaluator. PennyLane provides the QAOA layer structure but
    not the classical loop, so the 2p parameters are driven by
    ``scipy.optimize.minimize`` with exact (analytic) expectation values
    from ``default.qubit``, mirroring CirqQAOA.

    Strict solver: it requires an upstream Hamiltonian (content, the
    framework-neutral wire format, or ``hamiltonian.json`` on the rebuild
    path) and that Hamiltonian must be *diagonal* (I/Z only); anything else
    routes to ``failure`` with a clear ``qaoa.error``. Because the wire
    format is shared, any <Framework>Hamiltonian can feed it.

    Train-only: this solver does not sample. The optimizer's objective is
    the exact analytic expectation (``qaoa.energy_method=
    exact_statevector``); it emits the trained, bound circuit as portable
    qasm2 (the same construction as PennylaneQAOACircuit). All
    sample-dependent results are computed downstream by
    ``QuantumQAOAEvaluator`` from any counts engine's output.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Trains a p-layer QAOA ansatz using PennyLane's qml.qaoa layers "
            "(exact analytic expectations on default.qubit, scipy.optimize.minimize) "
            "and emits the trained, bound circuit as portable OpenQASM 2.0 (same "
            "construction as PennylaneQAOACircuit) with qaoa.optimal_parameters "
            "(betas then gammas). It does not sample: connect any counts "
            "simulator, then QuantumQAOAEvaluator."
        )
        tags = ["quantum", "pennylane", "qaoa", "optimization", "variational"]
        dependencies = ["pennylane>=0.40", "scipy>=1.10"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.solver_descriptors(_OPTIMIZERS)

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    def _run(self, context, flowfile):
        import numpy as np
        from scipy.optimize import minimize

        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")
        initial = qc.parse_initial_parameters(props["Initial Parameters"], layers)

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)

        qnode = pennylane_qaoa.energy_function(terms, n, layers)

        def energy(point):
            return float(qnode(point))

        if initial is None:
            initial_point = np.random.default_rng(seed).uniform(
                -np.pi, np.pi, 2 * layers
            )
        else:
            initial_point = np.asarray(initial)

        started = time.perf_counter()
        result = minimize(
            energy,
            initial_point,
            method=_SCIPY_METHOD[opt_name],
            options={"maxiter": maxiter},
        )
        elapsed = time.perf_counter() - started

        point = np.real(np.asarray(result.x, dtype=float))
        betas, gammas = qc.split_parameters(point, layers)
        optimal_value = float(result.fun)
        evals = int(getattr(result, "nfev", 0) or 0)
        converged = bool(getattr(result, "success", False))
        message = str(getattr(result, "message", "") or "")

        source, diagram, dropped = pennylane_qaoa.export(terms, n, betas, gammas)
        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(
                source, profile, "pennylane", "PennylaneQAOA", diagram
            ),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
            **qc.solver_attributes(
                framework="pennylane",
                betas=betas,
                gammas=gammas,
                optimal_value=optimal_value,
                energy_method="exact_statevector",
                energy_shots="",
                optimizer=opt_name,
                max_iterations=maxiter,
                cost_function_evals=evals,
                num_iterations=evals,
                converged=converged,
                optimizer_message=message,
                seed=seed,
                elapsed_seconds=elapsed,
            ),
            "circuit.global_phase_dropped": "true" if dropped else "false",
        }
        return qc.success(source.encode(), attrs)
