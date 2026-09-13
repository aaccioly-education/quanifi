"""
Unit tests for the decomposed Qiskit VQE pipeline:

    QiskitHamiltonian -> QiskitAnsatz -> QiskitVQE

Each stage is tested in isolation, then chained end-to-end with
result_to_flowfile (the same way the processors are wired on the NiFi canvas).
No running NiFi / JVM — conftest.py stubs nifiapi.*.
"""

import base64
import io
import json

import numpy as np

from QiskitHamiltonian import QiskitHamiltonian, _parse_pauli_sum
from QiskitAnsatz import QiskitAnsatz
from QiskitVQE import QiskitVQE

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# QiskitHamiltonian
# ---------------------------------------------------------------------------

class TestQiskitHamiltonian:

    def test_default_expression(self):
        proc = QiskitHamiltonian()
        res = proc.transform(MockContext(), MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert res.attributes["hamiltonian.num_qubits"] == "2"
        # Z 0 + Z 1 + 0.5 X 0 X 1 -> three terms
        assert res.attributes["hamiltonian.num_terms"] == "3"

        payload = json.loads(res.contents.decode("utf-8"))
        assert payload["num_qubits"] == 2
        assert len(payload["terms"]) == 3

    def test_content_roundtrips_to_operator(self):
        from qiskit.quantum_info import SparsePauliOp
        proc = QiskitHamiltonian()
        res = proc.transform(MockContext(**{"Hamiltonian": "Z 0", "Num Qubits": "0"}), MockFlowFile())
        payload = json.loads(res.contents.decode("utf-8"))
        op = SparsePauliOp.from_list(
            [(t[0], complex(t[1], t[2])) for t in payload["terms"]],
            num_qubits=payload["num_qubits"],
        )
        assert op.num_qubits == 1
        assert op.equiv(SparsePauliOp.from_list([("Z", 1.0)]))

    def test_num_qubits_hint_pads_register(self):
        proc = QiskitHamiltonian()
        res = proc.transform(MockContext(**{"Hamiltonian": "Z 0", "Num Qubits": "3"}), MockFlowFile())
        assert res.attributes["hamiltonian.num_qubits"] == "3"

    def test_negative_and_identity_terms(self):
        op, n = _parse_pauli_sum("-1.5 Z 0 + 0.25", 0)
        assert n == 1
        labels = dict((lbl, c) for lbl, c in op.to_list())
        assert labels["Z"].real == -1.5
        assert labels["I"].real == 0.25

    def test_bad_pauli_routes_to_failure(self):
        proc = QiskitHamiltonian()
        res = proc.transform(MockContext(**{"Hamiltonian": "Q 0", "Num Qubits": "0"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes


# ---------------------------------------------------------------------------
# QiskitAnsatz
# ---------------------------------------------------------------------------

class TestQiskitAnsatz:

    def test_standalone_emits_circuit_and_carrier(self):
        proc = QiskitAnsatz()
        ctx = MockContext(**{"Ansatz Type": "efficient_su2", "Reps": "2",
                             "Entanglement": "full", "Num Qubits": "3"})
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        # circuit.* contract (standalone behaves as a circuit builder)
        assert res.attributes["circuit.format"] == "qasm3"
        assert res.attributes["circuit.num_qubits"] == "3"
        # ansatz carrier is always present and loadable
        assert res.attributes["ansatz.format"] == "qpy_b64"
        assert int(res.attributes["ansatz.num_parameters"]) > 0
        from qiskit import qpy
        loaded = qpy.load(io.BytesIO(base64.b64decode(res.attributes["ansatz.qpy_b64"])))[0]
        assert loaded.num_qubits == 3
        assert loaded.num_parameters == int(res.attributes["ansatz.num_parameters"])

    def test_chain_mode_passes_hamiltonian_through(self):
        # Build a hamiltonian FlowFile first.
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "Z 0 + Z 1 + Z 2", "Num Qubits": "0"}), MockFlowFile())
        ff = result_to_flowfile(ham)

        proc = QiskitAnsatz()
        # Num Qubits property is intentionally wrong; chain mode must override it
        # from hamiltonian.num_qubits (= 3).
        res = proc.transform(MockContext(**{"Num Qubits": "7"}), ff)
        assert res.relationship == "success"
        assert res.attributes["ansatz.num_qubits"] == "3"
        # Hamiltonian content + attributes survive the pass-through.
        assert res.contents == ham.contents
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert res.attributes["hamiltonian.num_qubits"] == "3"
        # In chain mode it does NOT claim the circuit.* payload (content is the operator).
        assert "circuit.format" not in res.attributes


# ---------------------------------------------------------------------------
# QiskitVQE
# ---------------------------------------------------------------------------

class TestQiskitVQE:

    @staticmethod
    def _prep(expr, reps="1"):
        """Run the upstream Hamiltonian + Ansatz stages, returning the FlowFile
        that QiskitVQE (strict solver) expects."""
        ham = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": expr, "Num Qubits": "0"}), MockFlowFile())
        ff = result_to_flowfile(ham)
        ans = QiskitAnsatz().transform(MockContext(**{"Reps": reps}), ff)
        return result_to_flowfile(ans)

    def test_single_qubit_z_converges_to_minus_one(self):
        # Ground state energy of H = Z is -1.
        ff = self._prep("Z 0", reps="1")
        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "200",
                             "Shots": "1024", "Initial Parameters": "random"})
        res = QiskitVQE().transform(ctx, ff)
        assert res.relationship == "success"
        assert float(res.attributes["vqe.optimal_value"]) < -0.9
        # contract attributes
        assert res.attributes["vqe.framework"] == "qiskit"
        assert res.attributes["report.type"] == "simulation"
        assert res.attributes["sim.framework"] == "qiskit"
        assert res.attributes["circuit.format"] == "qasm3"
        assert "circuit.qasm3" in res.attributes
        # content is sortable counts JSON, and the top result should be |1>
        counts = json.loads(res.contents.decode("utf-8"))
        assert sum(counts.values()) == 1024
        assert res.attributes["sim.top_result"] in ("1", "0")
        params = json.loads(res.attributes["vqe.optimal_parameters"])
        assert len(params) > 0 and all(isinstance(p, (int, float)) for p in params)

    def test_full_chain_hamiltonian_ansatz_vqe(self):
        ham = QiskitHamiltonian().transform(MockContext(), MockFlowFile())
        ff1 = result_to_flowfile(ham)
        ans = QiskitAnsatz().transform(MockContext(**{"Reps": "2"}), ff1)
        ff2 = result_to_flowfile(ans)

        ctx = MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "150",
                             "Shots": "1024", "Initial Parameters": "zeros"})
        res = QiskitVQE().transform(ctx, ff2)
        assert res.relationship == "success"
        assert res.attributes["vqe.framework"] == "qiskit"
        assert res.attributes["vqe.num_qubits"] == "2"
        # The ansatz used must be the one from the chain (efficient_su2 from QiskitAnsatz).
        assert res.attributes["vqe.ansatz_type"] == "efficient_su2"
        # default H = Z0 + Z1 + 0.5 X0X1 has ground energy around -2.06.
        assert float(res.attributes["vqe.optimal_value"]) < -1.5

    def test_bad_initial_parameters_length_fails(self):
        ff = self._prep("Z 0", reps="1")
        ctx = MockContext(**{"Initial Parameters": "0.1,0.2"})  # wrong length for the ansatz
        res = QiskitVQE().transform(ctx, ff)
        assert res.relationship == "failure"
        assert "vqe.error" in res.attributes

    def test_missing_inputs_route_to_failure(self):
        # Strict solver: a bare FlowFile (no Hamiltonian, no ansatz) must fail clearly.
        res = QiskitVQE().transform(MockContext(), MockFlowFile())
        assert res.relationship == "failure"
        assert "vqe.error" in res.attributes

    def test_missing_ansatz_only_routes_to_failure(self):
        # Hamiltonian present but no upstream QiskitAnsatz -> failure.
        ham = QiskitHamiltonian().transform(MockContext(**{"Hamiltonian": "Z 0"}), MockFlowFile())
        res = QiskitVQE().transform(MockContext(), result_to_flowfile(ham))
        assert res.relationship == "failure"
        assert "ansatz" in res.attributes["vqe.error"].lower()
