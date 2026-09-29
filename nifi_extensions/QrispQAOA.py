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

"""Trains a p-layer QAOA ansatz using Qrisp's first-class ``QAOAProblem``
(``optimization_routine``, exact probabilities) and emits the trained,
bound circuit as portable OpenQASM 2.0 (the same construction as
``QrispQAOACircuit``, via ``qrisp_qaoa.export``), plus
``qaoa.optimal_parameters`` (betas then gammas, canonical order -- Qrisp's
own internal ordering is gammas then betas, converted at the boundary).
Train-only: it does not sample. Connect any counts simulator, then
``QuantumQAOAEvaluator``.
"""

import contextlib
import io
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import qrisp_qaoa

_OPTIMIZERS = ["COBYLA", "NELDER_MEAD", "POWELL"]

# scipy.optimize name per the processor's enum (Qrisp's optimization_routine
# forwards these straight to scipy.optimize.minimize).
_OPTIMIZER = {"COBYLA": "COBYLA", "NELDER_MEAD": "Nelder-Mead", "POWELL": "Powell"}


class QrispQAOA(FlowFileTransform):
    """
    Quantum Approximate Optimization Algorithm solver for Qrisp, built on
    Qrisp's first-class ``QAOAProblem`` (shared with ``QrispQAOACircuit`` via
    ``qrisp_qaoa.py`` for the cost operator, mixer and native export).

    Unlike Cirq/PennyLane (hand-rolled), Qrisp ships ``QAOAProblem``, which
    owns the hybrid loop via ``optimization_routine`` (exact probabilities,
    no sampling) -- so this solver assembles the three pieces (cost
    operator, mixer, classical cost function) from the upstream Hamiltonian
    and calls it, rather than re-implementing the optimization.

    Solver stage of <any>Hamiltonian -> QrispQAOA -> <any simulator> ->
    QuantumQAOAEvaluator. Strict solver: it requires an upstream Hamiltonian
    (content, the framework-neutral wire format, or ``hamiltonian.json`` on
    the rebuild path) and that Hamiltonian must be *diagonal* (I/Z only);
    anything else routes to ``failure`` with a clear ``qaoa.error``. Because
    the wire format is shared, any <Framework>Hamiltonian can feed it.

    ``QuantumVariable.get_measurement()`` keys are q0-left (qubit 0 =
    leftmost character), matching ``qaoa_contract.energy_vector``'s
    indexing exactly -- no bit reversal. (The pre-0.2.0 version of this
    solver reversed those keys in its classical cost function and its
    reported best measurement, which trained and reported the *mirror-image*
    Hamiltonian; that bug is fixed here by construction, not by patching the
    reversal.)

    Train-only: this solver does not sample. The optimizer's objective is
    Qrisp's exact (5-decimal-rounded) probability distribution
    (``qaoa.energy_method=exact_probabilities``); it emits the trained,
    bound circuit as portable qasm2 (the same construction as
    QrispQAOACircuit). All sample-dependent results are computed downstream
    by ``QuantumQAOAEvaluator`` from any counts engine's output.
    """

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Trains a p-layer QAOA ansatz using Qrisp's QAOAProblem.optimization_routine "
            "(exact, Qrisp-rounded probabilities) and emits the trained, bound circuit as "
            "portable OpenQASM 2.0 (same construction as QrispQAOACircuit) with "
            "qaoa.optimal_parameters (betas then gammas). It does not sample: connect any "
            "counts simulator, then QuantumQAOAEvaluator."
        )
        tags = ["quantum", "qrisp", "qaoa", "optimization", "variational"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.solver_descriptors(_OPTIMIZERS, with_mixer=True)

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    def _run(self, context, flowfile):
        import numpy as np
        from qrisp import QuantumVariable
        from qrisp.qaoa import QAOAProblem

        props = qc.read_properties(self, context, flowfile)
        layers = qc.parse_layers(props["Layers"])
        maxiter = qc.parse_max_iterations(props["Max Iterations"])
        seed = qc.parse_seed(props["Random Seed"])
        opt_name = qc.parse_choice(props["Optimizer"], _OPTIMIZERS, "Optimizer")
        initial = qc.parse_initial_parameters(props["Initial Parameters"], layers)
        mixer_name = props["Mixer Type"]

        terms, n, raw = qc.read_cost_hamiltonian(flowfile)

        # get_measurement() keys are q0-left, exactly qaoa_contract's own
        # indexing -- no reversal (see class docstring: this is the fix for
        # the pre-0.2.0 mirror-training bug).
        energies = qc.energy_vector(terms, n)

        # Sorted-order summation plus rounding to 12 decimals: measured
        # (scripts in the M5g session scratchpad) that Qrisp's exact-
        # probability backend differs by up to 2.22e-16 (1 ulp) between
        # separately-compiled circuits at the *same* theta, on 24/300 random
        # theta points. That 1-ulp jitter is invisible on its own, but
        # scipy's Fortran-backed COBYLA/POWELL amplify it over ~100
        # iterations into macroscopic (0.06-0.2 rad) parameter drift on a
        # large fraction of back-to-back in-process runs (the M5f finding).
        # Iterating in sorted-key order alone does not remove the drift (it
        # is genuine floating-point non-associativity, not dict-ordering
        # noise); rounding the returned energy to 12 decimals does: 40
        # seeded COBYLA repeats, each after an unrelated run, gave exactly
        # 0.0 parameter drift. Qrisp already rounds probabilities to 5
        # decimals, so this loses no real resolution.
        def cl_cost_function(res):
            return round(
                float(
                    sum(p * energies[int(bits, 2)] for bits, p in sorted(res.items()))
                ),
                12,
            )

        problem = QAOAProblem(
            cost_operator=qrisp_qaoa.cost_operator(terms),
            mixer=qrisp_qaoa.mixer(mixer_name),
            cl_cost_function=cl_cost_function,
            callback=True,
        )

        # Qrisp's own "random" init_type draws from the *global* NumPy RNG
        # (``np.random.rand(2*depth) * np.pi/2``) only when ``init_point`` is
        # None -- and NiFi runs many FlowFiles in one long-lived Python
        # process, so seeding the global RNG here is not reproducible: other
        # code running between ``np.random.seed(seed)`` and Qrisp's own draw
        # can consume global RNG state. Instead we draw the initial point
        # ourselves from a local generator, in Qrisp's own layout
        # ([gamma_0.., beta_0..]) and distribution (uniform on [0, pi/2)),
        # and always pass it as an explicit ``init_point`` so Qrisp's
        # global-RNG branch is never reached, seeded or not.
        if initial is None:
            init = np.random.default_rng(seed).uniform(0.0, np.pi / 2, 2 * layers)
        else:
            init = np.asarray(
                qrisp_qaoa.to_qrisp_theta(initial[:layers], initial[layers:])
            )

        started = time.perf_counter()
        # Suppress Qrisp's tqdm progress bar: NiFi's py4j bridge uses stdout,
        # so printing there corrupts the channel ("null response" crash).
        with contextlib.redirect_stdout(io.StringIO()):
            theta, fun = problem.optimization_routine(
                lambda: QuantumVariable(n),
                layers,
                {"shots": None},
                "random",
                init,
                _OPTIMIZER[opt_name],
                {"maxiter": maxiter},
            )
            betas, gammas = qrisp_qaoa.from_qrisp_theta(theta, layers)
            source, diagram, _rebased = qrisp_qaoa.export(
                terms, n, betas, gammas, mixer_name
            )
        elapsed = time.perf_counter() - started

        evals = len(problem.optimization_costs)
        optimal_value = float(fun)
        converged = evals < maxiter

        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(source, profile, "qrisp", "QrispQAOA", diagram),
            **qc.builder_attributes(raw, n, layers, betas, gammas, mixer=mixer_name),
            **qc.solver_attributes(
                framework="qrisp",
                betas=betas,
                gammas=gammas,
                optimal_value=optimal_value,
                energy_method="exact_probabilities",
                energy_shots="",
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
