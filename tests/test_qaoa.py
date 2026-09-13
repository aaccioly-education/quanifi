"""
Unit tests for the QAOA solver family:

    <any>Hamiltonian -> <Framework>QAOA -> QuanifiReport

QAOA has no separate ansatz stage (the ansatz is derived from the cost
Hamiltonian), so each solver consumes the framework-neutral
sparse_pauli_op_json wire content directly. The canonical test problem is
MaxCut on a triangle, H = 0.5 (Z0Z1 + Z1Z2 + Z0Z2): exact minimum -0.5,
attained by every 2-vs-1 partition (6 of the 8 basis states).
No running NiFi / JVM — conftest.py stubs nifiapi.*.
"""

import json

import numpy as np
import pytest

from QiskitHamiltonian import QiskitHamiltonian
from QiskitQAOA import QiskitQAOA

from conftest import MockContext, MockFlowFile, result_to_flowfile

TRIANGLE = "0.5 Z0 Z1 + 0.5 Z1 Z2 + 0.5 Z0 Z2"
TRIANGLE_MIN = -0.5
TRIANGLE_OPTIMA = {"001", "010", "100", "011", "101", "110"}


def triangle_flowfile():
    res = QiskitHamiltonian().transform(
        MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile())
    assert res.relationship == "success"
    return result_to_flowfile(res)


def assert_qaoa_contract(res, framework):
    """The shared qaoa.* / sim.* / report contract every solver must emit."""
    assert res.relationship == "success"
    a = res.attributes
    assert a["qaoa.framework"] == framework
    assert a["sim.framework"] == framework
    assert a["report.type"] == "simulation"
    assert a["qaoa.num_qubits"] == "3"
    assert a["qaoa.layers"] == "2"
    assert abs(float(a["qaoa.exact_minimum"]) - TRIANGLE_MIN) < 1e-9
    # The best sampled state must be (near-)optimal: 6 of 8 states are optima.
    assert float(a["qaoa.approximation_ratio"]) >= 0.99
    assert a["qaoa.best_measurement"] in TRIANGLE_OPTIMA
    assert len(a["sim.top_result"]) == 3
    # Content is the final measurement distribution.
    dist = json.loads(res.contents.decode("utf-8"))
    assert len(dist) >= 1
    assert all(len(k) == 3 for k in dist)


# ---------------------------------------------------------------------------
# pauli_dsl diagonal helpers
# ---------------------------------------------------------------------------

class TestDiagonalHelpers:

    def test_diagonal_values_zz(self):
        from pauli_dsl import diagonal_values
        # Z0 Z1: +1 on aligned (00, 11), -1 on anti-aligned (01, 10).
        v = diagonal_values([("ZZ", [0, 1], 1.0)], 2)
        assert v.tolist() == [1.0, -1.0, -1.0, 1.0]

    def test_is_diagonal(self):
        from pauli_dsl import is_diagonal
        assert is_diagonal([("ZZ", [0, 1], 1.0), ("", [], 0.5)])
        assert not is_diagonal([("XX", [0, 1], 1.0)])

    def test_diagonal_values_rejects_x_terms(self):
        from pauli_dsl import diagonal_values, PauliDSLError
        with pytest.raises(PauliDSLError):
            diagonal_values([("X", [0], 1.0)], 1)


# ---------------------------------------------------------------------------
# QiskitQAOA
# ---------------------------------------------------------------------------

class TestQiskitQAOA:

    def test_triangle_maxcut(self):
        res = QiskitQAOA().transform(
            MockContext(**{"Layers": "2", "Max Iterations": "150"}), triangle_flowfile())
        assert_qaoa_contract(res, "qiskit")
        # 2p = 4 optimal parameters, trained circuit serialised.
        assert len(json.loads(res.attributes["qaoa.optimal_parameters"])) == 4
        assert res.attributes["circuit.format"] == "qasm3"
        assert res.attributes["circuit.algorithm"] == "qaoa"

    def test_non_diagonal_routes_to_failure(self):
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile())
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]

    def test_missing_hamiltonian_routes_to_failure(self):
        res = QiskitQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_bad_initial_parameters_length(self):
        res = QiskitQAOA().transform(
            MockContext(**{"Layers": "2", "Initial Parameters": "0.1, 0.2"}),
            triangle_flowfile())
        assert res.relationship == "failure"
        assert "2p" in res.attributes["qaoa.error"]

    def test_molecule_hamiltonian_is_rejected_as_non_diagonal(self):
        # Interchange boundary check: the molecular (X/Y-bearing) Hamiltonian
        # rides the same wire format but is not a QAOA problem.
        from MoleculeHamiltonian import MoleculeHamiltonian
        ham = MoleculeHamiltonian().transform(
            MockContext(**{"Compute Reference Energies": "false"}), MockFlowFile())
        res = QiskitQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]


# ---------------------------------------------------------------------------
# CirqQAOA
# ---------------------------------------------------------------------------

class TestCirqQAOA:

    def test_triangle_maxcut(self):
        from CirqQAOA import CirqQAOA
        res = CirqQAOA().transform(
            MockContext(**{"Layers": "2", "Max Iterations": "150"}), triangle_flowfile())
        assert_qaoa_contract(res, "cirq")
        assert len(json.loads(res.attributes["qaoa.optimal_parameters"])) == 4
        assert res.attributes["circuit.format"] == "cirq_json"
        assert res.attributes["circuit.algorithm"] == "qaoa"
        # The hand-rolled expectation is exact, so the converged energy must
        # respect the variational bound on the diagonal cost.
        assert float(res.attributes["qaoa.optimal_value"]) >= TRIANGLE_MIN - 1e-6

    def test_non_diagonal_routes_to_failure(self):
        from CirqQAOA import CirqQAOA
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile())
        res = CirqQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]

    def test_missing_hamiltonian_routes_to_failure(self):
        from CirqQAOA import CirqQAOA
        res = CirqQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes

    def test_bitstring_energy_leftmost_char_is_qubit0(self):
        from CirqQAOA import CirqQAOA
        # Z0 with bitstring '10': qubit 0 is |1> -> energy -1.
        assert CirqQAOA._bitstring_energy([("Z", [0], 1.0)], "10") == -1.0
        assert CirqQAOA._bitstring_energy([("Z", [1], 1.0)], "10") == 1.0


# ---------------------------------------------------------------------------
# QrispQAOA — actual-optimization tests marked slow (real Qrisp simulation),
# matching the convention in test_qrisp_vqe.py.
# ---------------------------------------------------------------------------

class TestQrispQAOA:

    @pytest.mark.slow
    def test_triangle_maxcut(self):
        from QrispQAOA import QrispQAOA
        res = QrispQAOA().transform(
            MockContext(**{"Layers": "2", "Max Iterations": "100"}), triangle_flowfile())
        assert_qaoa_contract(res, "qrisp")
        # Qrisp emits probabilities, so the distribution sums to ~1.
        dist = json.loads(res.contents.decode("utf-8"))
        assert abs(sum(dist.values()) - 1.0) < 0.05

    def test_non_diagonal_routes_to_failure(self):
        """Strict-contract failures fail fast (no quantum execution) -> not slow."""
        from QrispQAOA import QrispQAOA
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile())
        res = QrispQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]

    def test_missing_hamiltonian_routes_to_failure(self):
        from QrispQAOA import QrispQAOA
        res = QrispQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes


# ---------------------------------------------------------------------------
# PennylaneQAOA
# ---------------------------------------------------------------------------

class TestPennylaneQAOA:

    def test_triangle_maxcut(self):
        from PennylaneQAOA import PennylaneQAOA
        res = PennylaneQAOA().transform(
            MockContext(**{"Layers": "2", "Max Iterations": "150"}), triangle_flowfile())
        assert_qaoa_contract(res, "pennylane")
        assert len(json.loads(res.attributes["qaoa.optimal_parameters"])) == 4
        assert res.attributes["circuit.algorithm"] == "qaoa"
        assert float(res.attributes["qaoa.optimal_value"]) >= TRIANGLE_MIN - 1e-6

    def test_non_diagonal_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "X0 X1", "Num Qubits": "0"}), MockFlowFile())
        res = PennylaneQAOA().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "diagonal" in res.attributes["qaoa.error"]

    def test_missing_hamiltonian_routes_to_failure(self):
        from PennylaneQAOA import PennylaneQAOA
        res = PennylaneQAOA().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "qaoa.error" in res.attributes


# ---------------------------------------------------------------------------
# Cross-framework interchange: any *Hamiltonian feeds any QAOA solver,
# because the wire format (and the property names) are shared.
# ---------------------------------------------------------------------------

class TestQAOAInterchange:

    @pytest.mark.parametrize("ham_cls_name", [
        "QiskitHamiltonian", "CirqHamiltonian", "QrispHamiltonian"])
    def test_any_hamiltonian_feeds_qiskit_qaoa(self, ham_cls_name):
        import importlib
        ham_cls = getattr(importlib.import_module(ham_cls_name), ham_cls_name)
        ham = ham_cls().transform(
            MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile())
        assert ham.relationship == "success"
        res = QiskitQAOA().transform(
            MockContext(**{"Layers": "1", "Max Iterations": "80"}), result_to_flowfile(ham))
        assert res.relationship == "success"
        assert abs(float(res.attributes["qaoa.exact_minimum"]) - TRIANGLE_MIN) < 1e-9
        assert float(res.attributes["qaoa.approximation_ratio"]) >= 0.99

    def test_qrisp_hamiltonian_feeds_cirq_qaoa(self):
        from QrispHamiltonian import QrispHamiltonian
        from CirqQAOA import CirqQAOA
        ham = QrispHamiltonian().transform(
            MockContext(**{"Hamiltonian": TRIANGLE, "Num Qubits": "0"}), MockFlowFile())
        res = CirqQAOA().transform(
            MockContext(**{"Layers": "1", "Max Iterations": "80"}), result_to_flowfile(ham))
        assert res.relationship == "success"
        assert float(res.attributes["qaoa.approximation_ratio"]) >= 0.99
        assert res.attributes["qaoa.framework"] == "cirq"
