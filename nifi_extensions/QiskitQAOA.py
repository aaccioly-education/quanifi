"""Trains a p-layer QAOA ansatz using ``qiskit_algorithms.QAOA`` and emits
the trained, bound circuit as portable OpenQASM 2.0 (the same construction as
``QiskitQAOACircuit``, via ``qiskit_qaoa.export``), plus
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
import qiskit_qaoa

_OPTIMIZERS = ["COBYLA", "SPSA", "NELDER_MEAD", "L_BFGS_B"]


class QiskitQAOA(FlowFileTransform):
    """
    Quantum Approximate Optimization Algorithm solver for Qiskit, built on
    ``qiskit_algorithms.QAOA``.

    Solver stage of <any>Hamiltonian -> QiskitQAOA -> <any simulator> ->
    QuantumQAOAEvaluator. Unlike VQE, QAOA derives its ansatz from the
    problem itself (alternating cost-layer exp(-i gamma H) and mixer-layer
    exp(-i beta X) blocks), so there is no separate ansatz stage: the cost
    Hamiltonian on the FlowFile is the whole problem definition.

    Strict solver: it requires an upstream Hamiltonian (content, the
    framework-neutral wire format with ``hamiltonian.format =
    sparse_pauli_op_json``, or ``hamiltonian.json`` on the rebuild path) and
    that Hamiltonian must be *diagonal* (I/Z terms only -- a classical cost
    function); anything else routes to ``failure`` with a clear
    ``qaoa.error``. Because the wire format is shared, any
    <Framework>Hamiltonian can feed this solver.

    This solver does not sample: ``qiskit_algorithms.QAOA``'s objective is
    itself *sampled* via a seeded ``StatevectorSampler`` at its default 1024
    shots during training (``qaoa.energy_method=sampled_statevector``,
    ``qaoa.energy_shots="1024"``) -- it is not an exact-statevector
    objective. The trained angles are bound into the same ``qaoa_ansatz``
    construction used by ``QiskitQAOACircuit`` and emitted as portable
    qasm2. All sample-dependent results (best measurement, approximation
    ratio, exact optimum, ...) are computed downstream by
    ``QuantumQAOAEvaluator`` from any counts engine's output.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Trains a p-layer QAOA ansatz using qiskit_algorithms.QAOA (a seeded "
            "StatevectorSampler, sampled objective at 1024 shots) and emits the "
            "trained, bound circuit as portable OpenQASM 2.0 (same construction as "
            "QiskitQAOACircuit) with qaoa.optimal_parameters (betas then gammas). "
            "It does not sample: connect any counts simulator, then "
            "QuantumQAOAEvaluator."
        )
        tags = ["quantum", "qiskit", "qaoa", "optimization", "variational"]
        dependencies = ["qiskit>=2.0.0,<2.5", "qiskit-algorithms>=0.4.0"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.solver_descriptors(_OPTIMIZERS)

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    @staticmethod
    def _make_optimizer(name, maxiter):
        from qiskit_algorithms.optimizers import COBYLA, SPSA, NELDER_MEAD, L_BFGS_B

        if name == "SPSA":
            return SPSA(maxiter=maxiter)
        if name == "NELDER_MEAD":
            return NELDER_MEAD(maxiter=maxiter)
        if name == "L_BFGS_B":
            return L_BFGS_B(maxiter=maxiter)
        return COBYLA(maxiter=maxiter)

    def _run(self, context, flowfile):
        import numpy as np
        from qiskit.primitives import StatevectorSampler
        from qiskit_algorithms.minimum_eigensolvers import QAOA

        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")
        initial = qc.parse_initial_parameters(props["Initial Parameters"], layers)

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)

        if seed is not None:
            from qiskit_algorithms.utils import algorithm_globals

            algorithm_globals.random_seed = seed

        initial_point = None if initial is None else np.asarray(initial)
        solver = QAOA(
            StatevectorSampler(seed=seed),
            self._make_optimizer(opt_name, maxiter),
            reps=layers,
            initial_point=initial_point,
        )

        started = time.perf_counter()
        result = solver.compute_minimum_eigenvalue(qiskit_qaoa.cost_operator(terms, n))
        elapsed = time.perf_counter() - started

        point = np.real(np.asarray(result.optimal_point, dtype=float))
        betas, gammas = qc.split_parameters(point, layers)
        optimal_value = float(np.real(result.eigenvalue))
        evals = (
            int(result.cost_function_evals)
            if result.cost_function_evals is not None
            else maxiter
        )
        converged = evals < maxiter

        source, diagram = qiskit_qaoa.export(terms, n, betas, gammas)
        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(source, profile, "qiskit", "QiskitQAOA", diagram),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
            **qc.solver_attributes(
                framework="qiskit",
                betas=betas,
                gammas=gammas,
                optimal_value=optimal_value,
                energy_method="sampled_statevector",
                energy_shots="1024",
                optimizer=opt_name,
                max_iterations=maxiter,
                cost_function_evals=evals,
                num_iterations=evals,
                converged=converged,
                optimizer_message=None,
                seed=seed,
                elapsed_seconds=elapsed,
            ),
        }
        return qc.success(source.encode(), attrs)
