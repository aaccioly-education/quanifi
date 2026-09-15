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


def solve(props, flowfile, kind, build, num_parameters, terms, n, extra=None):
    import numpy as np
    from scipy.optimize import minimize
    from pyquil_components import angles, expectation, sample

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
    started = time.perf_counter()
    result = minimize(
        lambda values: expectation(build(values), terms),
        point,
        method=methods[props["Optimizer"]],
        options={"maxiter": maxiter},
    )
    point = angles(result.x, num_parameters, "Optimal Parameters")
    if not np.isfinite(result.fun):
        raise ValueError("Optimizer returned a nonfinite energy")
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
    if kind == "qaoa":
        from pauli_dsl import diagonal_values

        costs = diagonal_values(terms, n)  # index uses qubit 0 as least significant

        def energy(bits):
            return float(costs[int(bits[::-1], 2)])

        best = min(counts, key=lambda bits: (energy(bits), bits))
        minimum, maximum = float(costs.min()), float(costs.max())
        attrs.update(
            {
                "qaoa.best_measurement": best,
                "qaoa.best_value": repr(energy(best)),
                "qaoa.exact_minimum": repr(minimum),
                "qaoa.approximation_ratio": repr(
                    (maximum - energy(best)) / (maximum - minimum)
                    if maximum > minimum
                    else 1.0
                ),
            }
        )
    return success(json.dumps(counts).encode(), attrs)
