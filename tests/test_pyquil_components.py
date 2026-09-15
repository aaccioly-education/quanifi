"""Independent mathematical checks for native pyQuil primitives and bridges."""

import json
import math

import numpy as np
import pytest
from pyquil import Program
from pyquil.gates import CPHASE, RX, RY, RZ, SWAP, X
from qiskit import qasm2
from qiskit.quantum_info import Operator

from pyquil_components import (
    angles,
    ansatz,
    ansatz_recipe,
    expectation,
    grover,
    hamiltonian,
    phase_oracle,
    qaoa,
    qft,
    sample,
    simulate,
)
from quil_qasm import to_qasm2, to_quil_program


def matrix(program):
    """Construct columns by evolving all q0-left basis vectors with pyQuil."""
    from pyquil.simulation import NumpyWavefunctionSimulator

    n = program.num_qubits
    columns = []
    for i in range(2**n):
        sim = NumpyWavefunctionSimulator(n)
        sim.wf[:] = 0
        sim.wf[np.unravel_index(i, [2] * n)] = 1
        columns.append(sim.do_program(program).wf.reshape(-1))
    return np.array(columns).T


def up_to_phase(actual, expected):
    idx = np.unravel_index(np.abs(expected).argmax(), expected.shape)
    factor = actual[idx] / expected[idx]
    np.testing.assert_allclose(actual, factor * expected, atol=1e-10)
    assert abs(factor) == pytest.approx(1)


@pytest.mark.parametrize("target", ["0", "10", "010", "110", "0111", "0110", "10001"])
def test_oracle_changes_only_marked_phase(target):
    expected = np.eye(2 ** len(target), dtype=complex)
    expected[int(target, 2), int(target, 2)] = -1
    up_to_phase(matrix(phase_oracle(target)), expected)


@pytest.mark.parametrize("target,k", [("10", 1), ("110", 2), ("0111", 2), ("0110", 0)])
def test_grover_probability(target, k):
    p = grover(phase_oracle(target), k)
    state = simulate(p).wf.reshape(-1)
    assert abs(state[int(target, 2)]) ** 2 == pytest.approx(
        math.sin((2 * k + 1) * math.asin(1 / math.sqrt(2 ** len(target)))) ** 2
    )


@pytest.mark.parametrize("n", [1, 2, 3, 4])
@pytest.mark.parametrize("inverse", [False, True])
def test_qft_matches_fourier_matrix(n, inverse):
    dim = 2**n
    expected = np.exp(
        ((-1 if inverse else 1) * 2j * np.pi / dim)
        * np.outer(np.arange(dim), np.arange(dim))
    ) / np.sqrt(dim)
    np.testing.assert_allclose(matrix(qft(n, inverse)), expected, atol=1e-10)


@pytest.mark.parametrize(
    "gate", [RX(0.4, 0), RY(-0.7, 1), RZ(0.3, 0), CPHASE(0.8, 0, 1), SWAP(0, 1)]
)
def test_qasm_standard_gates_agree_with_pyquil(gate):
    p = Program(gate)
    p.num_qubits = 2
    qc = qasm2.loads(to_qasm2(p))
    # Reverse Qiskit's qubit tensor axes to the documented q0-left convention.
    np.testing.assert_allclose(matrix(p), Operator(qc.reverse_bits()).data, atol=1e-10)
    back = to_quil_program("qasm2", to_qasm2(p))
    up_to_phase(matrix(back), matrix(p))


def test_modified_gate_is_rejected_instead_of_exported_as_plain_gate():
    p = Program(X(0).controlled(1))
    p.num_qubits = 2
    with pytest.raises(ValueError, match="modifiers"):
        to_qasm2(p)


def test_asymmetric_y_expectation_and_sampling_order():
    p = Program(RX(-np.pi / 2, 0), X(2))
    p.num_qubits = 3
    assert expectation(
        p, [("Y", [0], 0.5), ("Z", [2], 2), ("", [], 3)]
    ) == pytest.approx(1.5)
    counts = sample(p, 128, 12)
    assert set(counts) <= {"001", "101"}
    assert sum(counts.values()) == 128
    assert counts == sample(p, 128, 12)


def test_ansatz_rotation_order():
    recipe = ansatz_recipe(2, 0, "ry_rz", "none")
    p = ansatz(recipe, [np.pi, 0, 0, np.pi])
    assert sample(p, 16, 0) == {"10": 16}


def test_qaoa_cost_and_mixer_against_direct_exponentials():
    from scipy.linalg import expm

    terms = [("Z", [0], 0.3), ("ZZ", [0, 1], -0.7), ("", [], 1.2)]
    z = np.diag([1, -1])
    x = np.array([[0, 1], [1, 0]])
    eye = np.eye(2)
    cost = 0.3 * np.kron(z, eye) - 0.7 * np.kron(z, z) + 1.2 * np.eye(4)
    mix = np.kron(x, eye) + np.kron(eye, x)
    expected = expm(-0.4j * mix) @ expm(-0.6j * cost) @ np.ones(4) / 2
    actual = simulate(qaoa(terms, 2, [0.4], [0.6])).wf.reshape(-1)
    up_to_phase(actual, expected)


@pytest.mark.parametrize(
    "data",
    [
        {"num_qubits": 2, "terms": [["Z", 1, 0]]},
        {"num_qubits": 2, "terms": [["AZ", 1, 0]]},
        {"num_qubits": 2, "terms": [["IZ", 1, 0.1]]},
        {"num_qubits": 2, "terms": [["IZ", float("nan"), 0]]},
        {"num_qubits": 17, "terms": [["I" * 17, 1, 0]]},
        {"num_qubits": 2, "terms": []},
    ],
)
def test_invalid_hamiltonian(data):
    with pytest.raises(ValueError):
        hamiltonian(json.dumps(data))


@pytest.mark.parametrize("target", ["", "abc", "1" * 9])
def test_invalid_oracle(target):
    with pytest.raises(ValueError):
        phase_oracle(target)


def test_bad_angles():
    for spec in ["[1,2]", "[null]", "NaN", "Infinity"]:
        with pytest.raises((ValueError, TypeError)):
            angles(spec, 1)
