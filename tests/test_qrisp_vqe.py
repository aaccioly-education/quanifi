"""
Unit tests for the decomposed Qrisp VQE pipeline:

    QrispHamiltonian -> QrispAnsatz -> QrispVQE

Mirrors the Qiskit/Cirq VQE pipelines. Qrisp has a high-level VQEProblem, so
QrispVQE assembles and calls it. The actual-optimization tests are marked `slow`
(real Qrisp simulation); the parsing/spec tests are fast. Also checks
cross-framework interop via the shared neutral wire format.
"""

import json

import pytest

from QrispHamiltonian import QrispHamiltonian
from QrispAnsatz import QrispAnsatz, _num_params_per_layer, _entangle_pairs
from QrispVQE import QrispVQE
from QiskitHamiltonian import QiskitHamiltonian

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# Fast tests (no quantum execution)
# ---------------------------------------------------------------------------

class TestQrispHamiltonian:

    def test_default_neutral_wire_format(self):
        res = QrispHamiltonian().transform(MockContext(), MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert res.attributes["hamiltonian.framework"] == "qrisp"
        payload = json.loads(res.contents.decode("utf-8"))
        assert payload["num_qubits"] == 2 and len(payload["terms"]) == 3

    def test_wire_matches_qiskit_byte_for_byte(self):
        expr = "Z0 + Z1 + 0.5 X0 X1"
        q = QrispHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        k = QiskitHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        assert json.loads(q.contents) == json.loads(k.contents)

    def test_bad_input_fails(self):
        res = QrispHamiltonian().transform(MockContext(**{"Hamiltonian": "Q0"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes


class TestQrispAnsatz:

    def test_param_counts(self):
        assert _num_params_per_layer("efficient_su2", 3) == 6   # RY+RZ per qubit
        assert _num_params_per_layer("real_amplitudes", 3) == 3
        assert _entangle_pairs(3, "linear") == [(0, 1), (1, 2)]

    def test_standalone_spec(self):
        ctx = MockContext(**{"Ansatz Type": "efficient_su2", "Reps": "2",
                             "Entanglement": "full", "Num Qubits": "3"})
        res = QrispAnsatz().transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["ansatz.format"] == "qrisp_spec"
        assert res.attributes["ansatz.num_params_per_layer"] == "6"
        assert res.attributes["ansatz.num_parameters"] == "12"   # 6 * 2 reps
        spec = json.loads(res.contents.decode("utf-8"))
        assert spec["type"] == "efficient_su2" and spec["num_qubits"] == 3

    def test_chain_mode_passes_hamiltonian_through(self):
        ham = QrispHamiltonian().transform(MockContext(**{"Hamiltonian": "Z0 + Z1 + Z2"}), MockFlowFile())
        res = QrispAnsatz().transform(MockContext(**{"Num Qubits": "7"}), result_to_flowfile(ham))
        assert res.attributes["ansatz.num_qubits"] == "3"   # from hamiltonian, not the property
        assert res.contents == ham.contents
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert "circuit.num_qubits" not in res.attributes


class TestQrispVQEStrict:
    """Strict-contract failures fail fast (no quantum execution) -> not slow."""

    def test_missing_inputs_fails(self):
        res = QrispVQE().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "vqe.error" in res.attributes

    def test_missing_ansatz_only_fails(self):
        ham = QrispHamiltonian().transform(MockContext(**{"Hamiltonian": "Z0"}), MockFlowFile())
        res = QrispVQE().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "ansatz" in res.attributes["vqe.error"].lower()


# ---------------------------------------------------------------------------
# Slow tests (real Qrisp VQE optimization)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestQrispVQERun:

    @staticmethod
    def _prep(expr, reps="2"):
        ham = QrispHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        ans = QrispAnsatz().transform(MockContext(**{"Reps": reps}), result_to_flowfile(ham))
        return result_to_flowfile(ans)

    def test_single_qubit_z_converges(self):
        ff = self._prep("Z0", reps="2")
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "150",
                             "Shots": "512", "Initial Parameters": "random"})
        res = QrispVQE().transform(ctx, ff)
        assert res.relationship == "success"
        assert float(res.attributes["vqe.optimal_value"]) < -0.9
        assert res.attributes["vqe.framework"] == "qrisp"
        assert res.attributes["report.type"] == "simulation"
        assert res.attributes["sim.framework"] == "qrisp"
        # QASM3 export of the trained circuit must actually land (previously
        # lost to a NameError: the export referenced qv_e, local to a closure).
        assert res.attributes["circuit.format"] == "qasm3"
        assert "circuit.qasm3" in res.attributes
        assert "circuit.diagram" in res.attributes
        probs = json.loads(res.contents.decode("utf-8"))
        assert abs(sum(probs.values()) - 1.0) < 0.05   # Qrisp returns probabilities

    def test_full_chain_converges(self):
        ff = self._prep("Z0 + Z1 + 0.5 X0 X1", reps="2")
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "200",
                             "Shots": "512", "Initial Parameters": "random"})
        res = QrispVQE().transform(ctx, ff)
        assert res.relationship == "success"
        assert res.attributes["vqe.num_qubits"] == "2"
        assert res.attributes["vqe.ansatz_type"] == "efficient_su2"
        assert float(res.attributes["vqe.optimal_value"]) < -1.5

    def test_cross_framework_qiskit_hamiltonian_feeds_qrisp_vqe(self):
        ham = QiskitHamiltonian().transform(MockContext(**{"Hamiltonian": "Z0"}), MockFlowFile())
        ans = QrispAnsatz().transform(MockContext(**{"Reps": "2"}), result_to_flowfile(ham))
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "150", "Initial Parameters": "random"})
        res = QrispVQE().transform(ctx, result_to_flowfile(ans))
        assert res.relationship == "success"
        assert float(res.attributes["vqe.optimal_value"]) < -0.9
