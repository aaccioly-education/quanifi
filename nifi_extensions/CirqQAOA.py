"""Trains a p-layer QAOA ansatz using a hand-rolled sympy-parameterised Cirq
circuit (Cirq has no high-level QAOA class) and exact statevector
expectations driven by scipy.optimize.minimize, and emits the trained,
bound circuit as portable OpenQASM 2.0 (the same construction as
``CirqQAOACircuit``, via ``cirq_qaoa.export``), plus
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
import cirq_qaoa

_OPTIMIZERS = ["COBYLA", "NELDER_MEAD", "POWELL", "L_BFGS_B"]

# scipy.optimize.minimize method per optimizer name
_SCIPY_METHOD = {
    "COBYLA": "COBYLA",
    "NELDER_MEAD": "Nelder-Mead",
    "POWELL": "Powell",
    "L_BFGS_B": "L-BFGS-B",
}


class CirqQAOA(FlowFileTransform):
    """
    Hand-rolled Quantum Approximate Optimization Algorithm solver for Cirq.

    Cirq has no high-level QAOA class, so the whole algorithm is built from
    primitives (the same "we provide the high-level component Cirq lacks"
    story as CirqVQE, and shared with CirqQAOACircuit via cirq_qaoa.py): a
    sympy-parameterised ansatz alternating cost layers exp(-i gamma H) (CNOT
    ladder + Rz per Z-string term) and Rx mixer layers, energies computed
    exactly from the state vector (no sampling), and
    ``scipy.optimize.minimize`` driving the 2p parameters.

    Solver stage of <any>Hamiltonian -> CirqQAOA -> <any simulator> ->
    QuantumQAOAEvaluator. Strict solver: it requires an upstream Hamiltonian
    (content, the framework-neutral wire format, or ``hamiltonian.json`` on
    the rebuild path) and that Hamiltonian must be *diagonal* (I/Z only);
    anything else routes to ``failure`` with a clear ``qaoa.error``. Because
    the wire format is shared, any <Framework>Hamiltonian can feed it.

    Train-only: this solver does not sample. The optimizer's objective is
    the *exact* statevector expectation (``qaoa.energy_method=
    exact_statevector``); it emits the trained, bound circuit as portable
    qasm2 (the same construction as CirqQAOACircuit). All sample-dependent
    results (best measurement, approximation ratio, ...) are computed
    downstream by ``QuantumQAOAEvaluator`` from any counts engine's output.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Trains a p-layer QAOA ansatz using a hand-rolled sympy-parameterised "
            "Cirq circuit (exact statevector expectations, scipy.optimize.minimize) "
            "and emits the trained, bound circuit as portable OpenQASM 2.0 (same "
            "construction as CirqQAOACircuit) with qaoa.optimal_parameters (betas "
            "then gammas). It does not sample: connect any counts simulator, then "
            "QuantumQAOAEvaluator."
        )
        tags = ["quantum", "cirq", "qaoa", "optimization", "variational"]
        dependencies = ["cirq-core>=1.0", "scipy>=1.10", "sympy"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.solver_descriptors(_OPTIMIZERS)

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    def _run(self, context, flowfile):
        import cirq
        import numpy as np
        from scipy.optimize import minimize

        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")
        initial = qc.parse_initial_parameters(props["Initial Parameters"], layers)

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)

        circuit, param_names = cirq_qaoa.symbolic_circuit(terms, n, layers)
        # The Cirq state-vector index is already q0-MSB (q0-left), so no
        # remap is needed here (unlike the raw diagonal_values LSB=q0 order).
        energy_vector = qc.energy_vector(terms, n)

        simulator = cirq.Simulator(seed=seed, dtype=np.complex128)

        def energy(point):
            resolver = cirq.ParamResolver(
                {name: float(v) for name, v in zip(param_names, point)}
            )
            state = simulator.simulate(circuit, resolver).final_state_vector
            probs = np.abs(np.asarray(state, dtype=np.complex128)) ** 2
            return float(np.dot(probs, energy_vector))

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

        source, diagram, svg = cirq_qaoa.export(terms, n, betas, gammas)
        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(
                source, profile, "cirq", "CirqQAOA", diagram, svg=svg
            ),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
            **qc.solver_attributes(
                framework="cirq",
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
        }
        return qc.success(source.encode(), attrs)
