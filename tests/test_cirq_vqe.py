"""
Unit tests for the decomposed Cirq VQE pipeline:

    CirqHamiltonian -> CirqAnsatz -> CirqVQE

Mirrors the Qiskit VQE pipeline shape. Cirq has no high-level VQE class, so
CirqVQE is hand-rolled (simulate + scipy.optimize). Also checks cross-framework
interop: a QiskitHamiltonian can feed a CirqVQE (shared neutral wire format).
"""

import base64
import io
import json

import numpy as np

from CirqHamiltonian import CirqHamiltonian
from CirqAnsatz import CirqAnsatz
from CirqVQE import CirqVQE
from QiskitHamiltonian import QiskitHamiltonian

from conftest import MockContext, MockFlowFile, result_to_flowfile


class TestCirqHamiltonian:

    def test_default_uses_neutral_wire_format(self):
        res = CirqHamiltonian().transform(MockContext(), MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert res.attributes["hamiltonian.framework"] == "cirq"
        assert res.attributes["hamiltonian.num_qubits"] == "2"
        payload = json.loads(res.contents.decode("utf-8"))
        assert payload["num_qubits"] == 2 and len(payload["terms"]) == 3

    def test_wire_format_matches_qiskit_byte_for_byte(self):
        # Same expression through both processors -> identical content (only the
        # framework tag differs). This is what makes the Hamiltonian swappable.
        expr = "Z0 + Z1 + 0.5 X0 X1"
        cirq_res = CirqHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        qk_res = QiskitHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        assert json.loads(cirq_res.contents) == json.loads(qk_res.contents)

    def test_bad_input_fails(self):
        res = CirqHamiltonian().transform(MockContext(**{"Hamiltonian": "Q0"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes


class TestCirqAnsatz:

    def test_standalone_carrier_loadable(self):
        import cirq
        ctx = MockContext(**{"Ansatz Type": "efficient_su2", "Reps": "2",
                             "Entanglement": "full", "Num Qubits": "3"})
        res = CirqAnsatz().transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["circuit.format"] == "cirq_json"
        assert int(res.attributes["ansatz.num_parameters"]) > 0
        circ = cirq.read_json(json_text=res.attributes["ansatz.cirq_json"])
        names = json.loads(res.attributes["ansatz.param_names"])
        assert sorted(cirq.parameter_names(circ)) == sorted(names)
        assert len(names) == int(res.attributes["ansatz.num_parameters"])

    def test_chain_mode_passes_hamiltonian_through(self):
        ham = CirqHamiltonian().transform(
            MockContext(**{"Hamiltonian": "Z0 + Z1 + Z2"}), MockFlowFile())
        res = CirqAnsatz().transform(MockContext(**{"Num Qubits": "7"}), result_to_flowfile(ham))
        assert res.attributes["ansatz.num_qubits"] == "3"   # from hamiltonian, not the property
        assert res.contents == ham.contents
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert "circuit.format" not in res.attributes


class TestCirqVQE:

    @staticmethod
    def _prep(expr, reps="1", ansatz_ctx=None):
        ham = CirqHamiltonian().transform(MockContext(**{"Hamiltonian": expr}), MockFlowFile())
        ff = result_to_flowfile(ham)
        ctx = ansatz_ctx or MockContext(**{"Reps": reps})
        ans = CirqAnsatz().transform(ctx, ff)
        return result_to_flowfile(ans)

    def test_single_qubit_z_converges_to_minus_one(self):
        np.random.seed(0)  # make the 'random' initial point deterministic
        ff = self._prep("Z0", reps="1")
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "300",
                             "Shots": "1024", "Initial Parameters": "random"})
        res = CirqVQE().transform(ctx, ff)
        assert res.relationship == "success"
        assert float(res.attributes["vqe.optimal_value"]) < -0.9
        assert res.attributes["vqe.framework"] == "cirq"
        assert res.attributes["report.type"] == "simulation"
        assert res.attributes["sim.framework"] == "cirq"
        assert res.attributes["circuit.format"] == "cirq_json"
        counts = json.loads(res.contents.decode("utf-8"))
        assert sum(counts.values()) == 1024
        assert res.attributes["sim.top_result"] in ("0", "1")

    def test_full_chain_converges(self):
        np.random.seed(1)
        ff = self._prep("Z0 + Z1 + 0.5 X0 X1", reps="2")
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "250",
                             "Shots": "1024", "Initial Parameters": "random"})
        res = CirqVQE().transform(ctx, ff)
        assert res.relationship == "success"
        assert res.attributes["vqe.num_qubits"] == "2"
        assert res.attributes["vqe.ansatz_type"] == "efficient_su2"
        assert float(res.attributes["vqe.optimal_value"]) < -1.5

    def test_cross_framework_qiskit_hamiltonian_feeds_cirq_vqe(self):
        # QiskitHamiltonian -> CirqAnsatz -> CirqVQE proves the wire format is neutral.
        np.random.seed(2)
        ham = QiskitHamiltonian().transform(MockContext(**{"Hamiltonian": "Z0"}), MockFlowFile())
        ans = CirqAnsatz().transform(MockContext(**{"Reps": "1"}), result_to_flowfile(ham))
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "300",
                             "Initial Parameters": "random"})
        res = CirqVQE().transform(ctx, result_to_flowfile(ans))
        assert res.relationship == "success"
        assert float(res.attributes["vqe.optimal_value"]) < -0.9

    def test_missing_inputs_route_to_failure(self):
        res = CirqVQE().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "vqe.error" in res.attributes

    def test_missing_ansatz_only_fails(self):
        ham = CirqHamiltonian().transform(MockContext(**{"Hamiltonian": "Z0"}), MockFlowFile())
        res = CirqVQE().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "ansatz" in res.attributes["vqe.error"].lower()
