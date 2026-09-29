"""Trains a p-layer QAOA ansatz using the native pyQuil program builder
(``pyquil_components.qaoa``), exact local-expectation objectives driven by
scipy.optimize.minimize (via ``pyquil_processor.minimize_energy``, shared
with PyquilVQE), and emits the trained, bound circuit as portable OpenQASM
2.0 (the same construction as ``PyquilQAOACircuit``, via
``pyquil_processor.qaoa_export``), plus ``qaoa.optimal_parameters`` (betas
then gammas). Train-only: it does not sample. Connect any counts simulator,
then ``QuantumQAOAEvaluator``.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import pyquil_processor

_OPTIMIZERS = ["COBYLA", "POWELL", "L_BFGS_B"]


class PyquilQAOA(FlowFileTransform):
    """
    Quantum Approximate Optimization Algorithm solver for pyQuil, built on
    the native ``pyquil_components.qaoa`` program builder (shared with
    ``PyquilQAOACircuit`` via ``pyquil_processor.qaoa_export``) and the same
    ``pyquil_processor.minimize_energy`` optimizer plumbing PyquilVQE uses.

    Solver stage of <any>Hamiltonian -> PyquilQAOA -> <any simulator> ->
    QuantumQAOAEvaluator. Strict solver: it requires an upstream Hamiltonian
    (content, the framework-neutral wire format, or ``hamiltonian.json`` on
    the rebuild path) and that Hamiltonian must be *diagonal* (I/Z only);
    anything else routes to ``failure`` with a clear ``qaoa.error``. Because
    the wire format is shared, any <Framework>Hamiltonian can feed it.

    Train-only: this solver does not sample. The optimizer's objective is
    the *exact* local-expectation value pyQuil computes from the statevector
    (``qaoa.energy_method=exact_statevector``); it emits the trained, bound
    circuit as portable qasm2 (the same construction as PyquilQAOACircuit).
    All sample-dependent results (best measurement, approximation ratio,
    ...) are computed downstream by ``QuantumQAOAEvaluator`` from any counts
    engine's output.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Trains a p-layer QAOA ansatz using the native pyQuil program builder "
            "(exact local-expectation objectives, scipy.optimize.minimize) and "
            "emits the trained, bound circuit as portable OpenQASM 2.0 (same "
            "construction as PyquilQAOACircuit) with qaoa.optimal_parameters (betas "
            "then gammas). It does not sample: connect any counts simulator, then "
            "QuantumQAOAEvaluator."
        )
        tags = ["quantum", "pyquil", "rigetti", "qaoa", "optimization", "variational"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26", "scipy>=1.14"]

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
        from pyquil_components import qaoa

        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")
        initial = qc.parse_initial_parameters(props["Initial Parameters"], layers)

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)

        def build(point):
            return qaoa(terms, n, point[:layers], point[layers:])

        if initial is None:
            point0 = np.random.default_rng(seed).uniform(-np.pi, np.pi, 2 * layers)
        else:
            point0 = np.asarray(initial)

        point, result, started = pyquil_processor.minimize_energy(
            build, point0, terms, opt_name, maxiter
        )
        elapsed_perf = time.perf_counter() - started

        betas, gammas = qc.split_parameters(np.asarray(point, dtype=float), layers)
        optimal_value = float(result.fun)
        evals = int(getattr(result, "nfev", 0) or 0)
        converged = bool(getattr(result, "success", False))
        message = str(getattr(result, "message", "") or "")

        source, diagram = pyquil_processor.qaoa_export(terms, n, betas, gammas)
        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(source, profile, "pyquil", "PyquilQAOA", diagram),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
            **qc.solver_attributes(
                framework="pyquil",
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
                elapsed_seconds=elapsed_perf,
            ),
        }
        return qc.success(source.encode(), attrs)
