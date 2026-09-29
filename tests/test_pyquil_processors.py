"""Real native pyQuil processors; NiFi API only is stubbed."""

import json
import importlib

import numpy as np
import pytest

from conftest import MockContext, MockFlowFile, result_to_flowfile_merged
from pyquil_components import simulate
from quil_qasm import to_quil_program


def run(name, props=None, flowfile=None):
    cls = getattr(importlib.import_module(name), name)
    return cls().transform(MockContext(**(props or {})), flowfile or MockFlowFile())


def chain(name, props=None, flowfile=None):
    ff = flowfile or MockFlowFile()
    result = run(name, props, ff)
    assert result.relationship == "success", result.attributes
    return result_to_flowfile_merged(result, ff)


def state(ff):
    return simulate(
        to_quil_program(
            ff.getAttribute("circuit.format"), bytes(ff.getContentsAsBytes()).decode()
        )
    ).wf.reshape(-1)


@pytest.mark.parametrize(
    "target,iterations", [("10", 1), ("110", 2), ("0111", 2), ("0110", 0)]
)
def test_modular_grover_matches_existing_builder(target, iterations):
    original = chain(
        "PyquilGroverCircuit",
        {"Marked State": target, "Num Iterations": str(iterations)},
    )
    oracle = chain(
        "PyquilPhaseOracle",
        {"Marked State": "${target}"},
        MockFlowFile(attributes={"target": target, "circuit.svg": "old"}),
    )
    composed = chain(
        "PyquilGroverOperator", {"Num Iterations": str(iterations)}, oracle
    )
    np.testing.assert_allclose(
        abs(state(composed)) ** 2, abs(state(original)) ** 2, atol=1e-10
    )
    assert composed.getAttribute("circuit.svg") == ""
    result = run("QiskitAerSimulator", {"Shots": "128", "Random Seed": "42"}, composed)
    assert result.relationship == "success"
    if iterations:
        assert result.attributes["sim.top_result"] == target


@pytest.mark.parametrize(
    "name,props",
    [
        ("PyquilPhaseOracle", {"Marked State": ""}),
        ("PyquilPhaseOracle", {"Marked State": "x"}),
        ("PyquilGroverOperator", {"Num Iterations": "-1"}),
        ("PyquilGroverOperator", {"Num Iterations": "NaN"}),
    ],
)
def test_grover_failure_preserves_content(name, props):
    ff = chain("PyquilPhaseOracle")
    r = run(name, props, ff)
    assert r.relationship == "failure"
    assert r.contents == bytes(ff.getContentsAsBytes())
    assert r.attributes["grover.error"]


def ham(spec="Z0 + 0.5 X0", n="1"):
    return chain("QiskitHamiltonian", {"Hamiltonian": spec, "Num Qubits": n})


def test_ansatz_modes_recipe_roundtrip_and_parameter_binding():
    incoming = ham()
    ff = chain(
        "PyquilAnsatz",
        {"Output Mode": "attach", "Reps": "0", "Rotations": "ry"},
        incoming,
    )
    assert bytes(ff.getContentsAsBytes()) == bytes(incoming.getContentsAsBytes())
    assert ff.getAttribute("ansatz.num_parameters") == "1"
    assert ff.getAttribute("circuit.format") == ""
    recipe = json.loads(ff.getAttribute("ansatz.pyquil_json"))
    assert recipe["num_qubits"] == 1
    bound = chain(
        "PyquilAnsatz",
        {
            "Num Qubits": "2",
            "Reps": "0",
            "Rotations": "ry",
            "Parameters": "${parameters}",
        },
        MockFlowFile(attributes={"parameters": "[3.141592653589793,0]"}),
    )
    assert np.argmax(abs(state(bound))) == 2
    assert bound.getAttribute("ansatz.num_parameters") == "2"
    assert bound.getAttribute("circuit.num_parameters") == "0"


@pytest.mark.parametrize(
    "spec", ["Z0 + 0.5 Y1", '{"num_qubits":2,"terms":[["IZ",1,0],["YI",0.5,0]]}']
)
def test_expectation_asymmetric_pauli_labels(spec):
    from pyquil import Program
    from pyquil.gates import X, RX
    from quil_qasm import to_qasm2

    p = Program(X(0), RX(-np.pi / 2, 1))
    p.num_qubits = 2
    ff = MockFlowFile(to_qasm2(p), {"circuit.format": "qasm2"})
    r = run("PyquilExpectation", {"Hamiltonian": spec}, ff)
    assert r.relationship == "success", r.attributes
    assert json.loads(r.contents)["expectation"] == pytest.approx(-0.5)
    assert r.attributes["expectation.method"] == "exact_statevector"


def test_vqe_finds_known_ground_energy_and_reproducible_counts():
    ff = chain(
        "PyquilAnsatz", {"Output Mode": "attach", "Reps": "0", "Rotations": "ry"}, ham()
    )
    props = {
        "Optimizer": "L_BFGS_B",
        "Max Iterations": "100",
        "Initial Parameters": "[2]",
        "Shots": "128",
        "Random Seed": "12",
    }
    r = run("PyquilVQE", props, ff)
    assert r.relationship == "success", r.attributes
    assert float(r.attributes["vqe.optimal_value"]) == pytest.approx(
        -np.sqrt(1.25), abs=1e-7
    )
    assert r.attributes["vqe.converged"] == "true"
    assert r.attributes["vqe.energy_method"] == "exact_statevector"
    assert r.contents == run("PyquilVQE", props, ff).contents
    assert sum(json.loads(r.contents).values()) == 128
    # Independently evaluate the exported optimum using Qiskit, including label order.
    from qiskit import qasm2
    from qiskit.quantum_info import Statevector, SparsePauliOp

    qc = qasm2.loads(r.attributes["circuit.qasm2"])
    energy = Statevector.from_instruction(qc).expectation_value(
        SparsePauliOp.from_list([("Z", 1), ("X", 0.5)])
    )
    assert energy.real == pytest.approx(
        float(r.attributes["vqe.optimal_value"]), abs=1e-8
    )


def test_qaoa_circuit_can_feed_expectation_and_simulator():
    # Minimize negative cut: -1/2 + (Z0 Z1)/2, ground energy -1.
    ff = chain(
        "PyquilQAOACircuit",
        {"Betas": "-0.39269908169872414", "Gammas": "1.5707963267948966"},
        ham("-0.5 + 0.5 Z0 Z1", "2"),
    )
    ev = run("PyquilExpectation", flowfile=ff)
    assert ev.relationship == "success", ev.attributes
    assert float(ev.attributes["expectation.value"]) == pytest.approx(-1)
    counts = run("PyquilSimulator", {"Shots": "64", "Random Seed": "42"}, ff)
    assert counts.relationship == "success", counts.attributes
    assert set(json.loads(counts.contents)) <= {"01", "10"}


def test_qaoa_solver_minimizes_and_exports_same_circuit_as_builder():
    cost = ham("-0.5 + 0.5 Z0 Z1", "2")
    props = {
        "Layers": "1",
        "Max Iterations": "100",
        "Initial Parameters": "[-0.3,1]",
        "Optimizer": "L_BFGS_B",
    }
    r = run("PyquilQAOA", props, cost)
    assert r.relationship == "success", r.attributes
    assert float(r.attributes["qaoa.optimal_value"]) == pytest.approx(-1, abs=1e-7)

    # Sample the trained circuit and score it downstream (train-only: the
    # solver itself emits no sim.*/qaoa.best_* keys any more).
    merged = result_to_flowfile_merged(r, cost)
    engine_res = run("PyquilSimulator", {"Shots": "128", "Random Seed": "42"}, merged)
    assert engine_res.relationship == "success", engine_res.attributes
    engine_merged = result_to_flowfile_merged(engine_res, merged)
    ev = run("QuantumQAOAEvaluator", flowfile=engine_merged)
    assert ev.relationship == "success", ev.attributes
    assert float(ev.attributes["qaoa.best_value"]) == -1
    assert ev.attributes["qaoa.best_measurement"] in ("01", "10")
    assert float(ev.attributes["qaoa.approximation_ratio"]) == 1

    # The builder reproduces the trained circuit exactly from qaoa.* EL.
    built = run(
        "PyquilQAOACircuit",
        {
            "Layers": "${qaoa.layers}",
            "Betas": "${qaoa.betas}",
            "Gammas": "${qaoa.gammas}",
        },
        merged,
    )
    assert built.relationship == "success", built.attributes
    assert built.attributes["circuit.qasm2"] == r.attributes["circuit.qasm2"]

    assert r.contents == run("PyquilQAOA", props, cost).contents


def test_solver_budget_exhaustion_is_explicit_not_false_convergence():
    ff = chain("PyquilAnsatz", {"Output Mode": "attach"}, ham("Z0 + Z1", "2"))
    r = run("PyquilVQE", {"Max Iterations": "1"}, ff)
    assert r.relationship == "success", r.attributes
    assert r.attributes["vqe.converged"] == "false"
    assert r.attributes["vqe.optimizer_message"]


@pytest.mark.parametrize("swaps", ["true", "false"])
def test_qft_append_inverse_restores_state_and_idle_qubits(swaps):
    initial = chain(
        "PyquilAnsatz",
        {
            "Num Qubits": "3",
            "Reps": "0",
            "Rotations": "ry",
            "Parameters": "[3.141592653589793,0,0]",
        },
    )
    forward = chain(
        "PyquilQFT", {"Input Mode": "append", "Include Swaps": swaps}, initial
    )
    inverse = chain(
        "PyquilQFT",
        {"Input Mode": "append", "Inverse": "true", "Include Swaps": swaps},
        forward,
    )
    np.testing.assert_allclose(
        abs(state(inverse)) ** 2, abs(state(initial)) ** 2, atol=1e-10
    )
    assert inverse.getAttribute("circuit.num_qubits") == "3"


@pytest.mark.parametrize(
    "name,props,source",
    [
        ("PyquilAnsatz", {"Reps": "-1"}, "empty"),
        ("PyquilAnsatz", {"Num Qubits": "17"}, "empty"),
        ("PyquilAnsatz", {"Parameters": "[NaN]"}, "empty"),
        ("PyquilAnsatz", {"Output Mode": "attach"}, "empty"),
        (
            "PyquilExpectation",
            {"Hamiltonian": '{"num_qubits":2,"terms":[["IZ",1,2]]}'},
            "circuit",
        ),
        ("PyquilExpectation", {"Hamiltonian": "X0 Y0"}, "circuit"),
        ("PyquilVQE", {}, "ham"),
        ("PyquilVQE", {"Shots": "0"}, "ansatz"),
        ("PyquilVQE", {"Initial Parameters": "[NaN]"}, "ansatz"),
        ("PyquilVQE", {"Random Seed": "-1"}, "ansatz"),
        ("PyquilQAOA", {}, "ham"),  # ham contains X
        ("PyquilQAOACircuit", {}, "ham"),
        ("PyquilQAOACircuit", {"Layers": "2"}, "diagonal"),
        ("PyquilQAOACircuit", {"Betas": "[NaN]"}, "diagonal"),
        (
            "PyquilQAOA",
            {"Initial Parameters": "[1]"},
            "diagonal",
        ),  # default Layers 2 -> 2p=4
        ("PyquilQAOA", {"Random Seed": "-1"}, "diagonal"),
        ("PyquilQAOA", {"Layers": "17"}, "diagonal"),
        ("PyquilQFT", {"Num Qubits": "0"}, "empty"),
        ("PyquilQFT", {"Input Mode": "append"}, "empty"),
    ],
)
def test_invalid_inputs_route_failure(name, props, source):
    ff = {
        "empty": lambda: MockFlowFile("retain me"),
        "ham": ham,
        "diagonal": lambda: ham("Z0"),
        "ansatz": lambda: chain("PyquilAnsatz", {"Output Mode": "attach"}, ham()),
        "circuit": lambda: chain("PyquilAnsatz"),
    }[source]()
    r = run(name, props, ff)
    assert r.relationship == "failure", r.attributes
    assert r.contents == bytes(ff.getContentsAsBytes())
    assert any(k.endswith(".error") and v for k, v in r.attributes.items())


def test_simulator_clears_stale_seed_and_noise_metadata():
    ff = chain(
        "PyquilPhaseOracle",
        flowfile=MockFlowFile(
            attributes={
                "run.seed": "old",
                "sim.noise_params": "old",
                "sim.error": "old",
            }
        ),
    )
    result = run("PyquilSimulator", {"Shots": "4"}, ff)
    assert result.relationship == "success", result.attributes
    assert result.attributes["run.seed"] == ""
    assert result.attributes["sim.noise_params"] == ""
    assert result.attributes["sim.error"] == ""


@pytest.mark.parametrize("shots", ["0", "-2", "1000001", "NaN"])
def test_simulator_bad_shots_preserves_input(shots):
    ff = chain("PyquilPhaseOracle")
    r = run("PyquilSimulator", {"Shots": shots}, ff)
    assert r.relationship == "failure"
    assert r.contents == bytes(ff.getContentsAsBytes())


def test_vqe_rejects_mismatched_recipe_width():
    incoming = ham("Z0", "1")
    ff = chain("PyquilAnsatz", {"Output Mode": "attach"}, incoming)
    attrs = ff.getAttributes()
    recipe = json.loads(attrs["ansatz.pyquil_json"])
    recipe["num_qubits"] = 2
    attrs["ansatz.pyquil_json"] = json.dumps(recipe)
    r = run("PyquilVQE", flowfile=MockFlowFile(bytes(ff.getContentsAsBytes()), attrs))
    assert r.relationship == "failure"
    assert "widths" in r.attributes["vqe.error"]
