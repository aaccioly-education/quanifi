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

"""Small NiFi adapters shared by the pyQuil processors (not a processor)."""

import json
import time

from nifiapi.flowfiletransform import FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, ExpressionLanguageScope

from pyquil_components import circuit_depth, hamiltonian, integer


def descriptors(specs):
    """Called inside processor __init__, after the JVM has been assigned."""
    return [
        PropertyDescriptor(
            name=name,
            description=description,
            default_value=default,
            required=True,
            allowable_values=choices,
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        for name, default, description, choices in specs
    ]


def properties(processor, context, flowfile):
    return {
        d.name: context.getProperty(d).evaluateAttributeExpressions(flowfile).getValue()
        for d in processor.descriptors
    }


def guarded(processor, context, flowfile, function, prefix):
    try:
        return function(properties(processor, context, flowfile), flowfile)
    except Exception as exc:
        processor.logger.error(f"{type(processor).__name__}: {exc}")
        return FlowFileTransformResult(
            relationship="failure",
            contents=bytes(flowfile.getContentsAsBytes()),
            attributes={f"{prefix}.error": str(exc)},
        )


def success(contents, attrs):
    return FlowFileTransformResult(
        relationship="success", contents=contents, attributes=attrs
    )


def circuit_result(program, component, algorithm, extra=None):
    from quil_qasm import to_qasm2

    source = to_qasm2(program)
    instructions = list(program.instructions)
    attrs = {
        "circuit.format": "qasm2",
        "circuit.qasm2": source,
        "circuit.qasm3": "",
        "circuit.cirq_json": "",
        "circuit.svg": "",
        "circuit.diagram": str(program),
        "circuit.num_qubits": str(program.num_qubits),
        "circuit.depth": str(circuit_depth(instructions)),
        "circuit.gate_count": str(len(instructions)),
        "circuit.nonlocal_gates": str(sum(len(i.qubits) > 1 for i in instructions)),
        "circuit.t_count": "",
        "circuit.framework": "pyquil",
        "circuit.bit_order": "q0_left",
        "circuit.algorithm": algorithm,
        "circuit.num_parameters": "0",
        "circuit.marked_state": "",
        "circuit.num_iterations": "",
        "builder.framework": "pyquil",
        "builder.component": component,
        "mime.type": "text/plain",
        "report.type": "circuit",
    }
    attrs.update(extra or {})
    return success(source.encode(), attrs)


def read_circuit(flowfile):
    from quil_qasm import to_quil_program

    program = to_quil_program(
        flowfile.getAttribute("circuit.format"),
        bytes(flowfile.getContentsAsBytes()).decode(),
    )
    integer(program.num_qubits, "Circuit qubits")
    return program


def qaoa_export(terms, n, betas, gammas):
    """Returns ``(qasm2_source, text_diagram)`` for the native pyQuil QAOA
    program. Used by both ``PyquilQAOACircuit`` (fixed angles) and
    ``PyquilQAOA`` (trained angles), so the two stay byte-identical for the
    same terms/angles."""
    from pyquil_components import qaoa
    from quil_qasm import to_qasm2

    program = qaoa(terms, n, betas, gammas)
    return to_qasm2(program), str(program)


def read_hamiltonian(flowfile):
    if flowfile.getAttribute("hamiltonian.format") != "sparse_pauli_op_json":
        raise ValueError("Expected upstream hamiltonian.format=sparse_pauli_op_json")
    raw = bytes(flowfile.getContentsAsBytes()).decode()
    terms, n = hamiltonian(raw)
    return terms, n, raw


SOLVER_SPECS = [
    (
        "Optimizer",
        "COBYLA",
        "SciPy local minimizer; convergence is reported separately from successful execution.",
        ["COBYLA", "POWELL", "L_BFGS_B"],
    ),
    (
        "Max Iterations",
        "100",
        "Optimizer budget, 1..10000. COBYLA counts function evaluations; other methods count iterations.",
        None,
    ),
    (
        "Shots",
        "1024",
        "Final histogram only (1..1000000); optimization uses exact local statevector expectations.",
        None,
    ),
    (
        "Initial Parameters",
        "random",
        "random, zeros, or JSON/comma-separated values in the documented parameter order.",
        None,
    ),
    (
        "Random Seed",
        "42",
        "Nonnegative seed for initial parameters and final sampling (0..4294967295).",
        None,
    ),
]


def minimize_energy(build, point, terms, optimizer, maxiter):
    """Runs scipy.optimize.minimize against the exact pyQuil local-expectation
    objective for ``terms``, starting from ``point``. Shared by ``solve``
    (PyquilVQE) and the PyquilQAOA solver, so both stay on exactly the same
    optimizer plumbing and error messages.

    Returns ``(point, result, started)``: the optimizer's final parameters
    (validated finite-length via ``angles``), the raw scipy ``OptimizeResult``
    and the ``time.perf_counter()`` timestamp taken just before ``minimize``
    was called (so callers can report elapsed time consistently).
    """
    import numpy as np
    from scipy.optimize import minimize
    from pyquil_components import angles, expectation

    methods = {"COBYLA": "COBYLA", "POWELL": "Powell", "L_BFGS_B": "L-BFGS-B"}
    if optimizer not in methods:
        raise ValueError("Unsupported Optimizer")
    started = time.perf_counter()
    result = minimize(
        lambda values: expectation(build(values), terms),
        point,
        method=methods[optimizer],
        options={"maxiter": maxiter},
    )
    point = angles(result.x, len(point), "Optimal Parameters")
    if not np.isfinite(result.fun):
        raise ValueError("Optimizer returned a nonfinite energy")
    return point, result, started


def solve(props, flowfile, kind, build, num_parameters, terms, n, extra=None):
    import numpy as np
    from pyquil_components import angles, sample

    maxiter = integer(props["Max Iterations"], "Max Iterations", maximum=10000)
    shots = integer(props["Shots"], "Shots", maximum=1_000_000)
    seed = integer(props["Random Seed"], "Random Seed", minimum=0, maximum=2**32 - 1)
    methods = {"COBYLA": "COBYLA", "POWELL": "Powell", "L_BFGS_B": "L-BFGS-B"}
    if props["Optimizer"] not in methods:
        raise ValueError("Unsupported Optimizer")
    initial = props["Initial Parameters"].strip()
    if initial == "random":
        point = np.random.default_rng(seed).uniform(-np.pi, np.pi, num_parameters)
    elif initial == "zeros":
        point = np.zeros(num_parameters)
    else:
        point = angles(initial, num_parameters, "Initial Parameters")
    point, result, started = minimize_energy(
        build, point, terms, props["Optimizer"], maxiter
    )
    program = build(point)
    counts = sample(program, shots, seed)
    output = circuit_result(program, f"Pyquil{kind.upper()}", kind, extra)
    attrs = output.attributes
    attrs.update(
        {
            f"{kind}.framework": "pyquil",
            f"{kind}.optimal_value": repr(float(result.fun)),
            f"{kind}.optimal_parameters": json.dumps(point.tolist()),
            f"{kind}.cost_function_evals": str(result.nfev),
            f"{kind}.max_iterations": str(maxiter),
            f"{kind}.converged": str(bool(result.success)).lower(),
            f"{kind}.optimizer_message": str(result.message),
            f"{kind}.optimizer": props["Optimizer"],
            f"{kind}.num_qubits": str(n),
            f"{kind}.shots": str(shots),
            f"{kind}.energy_method": "exact_statevector",
            f"{kind}.error": "",
            "sim.shots": str(shots),
            "sim.top_result": next(iter(counts)),
            "sim.top_probability": repr(next(iter(counts.values())) / shots),
            "sim.framework": "pyquil",
            "sim.component": f"Pyquil{kind.upper()}",
            "sim.backend": "NumpyWavefunctionSimulator",
            "sim.bit_order": "q0_left",
            "sim.noise_model": "none",
            "sim.noise_params": "",
            "sim.error": "",
            "run.seed": str(seed),
            "mime.type": "application/json",
            "report.type": "simulation",
            "perf.elapsed_seconds": repr(time.perf_counter() - started),
        }
    )
    return success(json.dumps(counts).encode(), attrs)
