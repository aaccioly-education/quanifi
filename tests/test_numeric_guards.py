"""
Regression tests for the systemic EL numeric-conversion hardening.

Properties declared with ExpressionLanguageScope.FLOWFILE_ATTRIBUTES can be
fed a malformed value at runtime by a FlowFile attribute, bypassing the static
validators. Every such int()/float() conversion must route to the ``failure``
relationship with an error attribute instead of raising out of ``transform()``.

These tests supply a non-numeric value where a number is expected and assert
the failure-routing contract. They deliberately avoid any real quantum work —
the conversion fails before the backend is invoked.
"""

import pytest

from conftest import MockContext, MockFlowFile


def _assert_failure(result, error_key):
    assert result.relationship == "failure", (
        f"expected failure, got {result.relationship} "
        f"(attrs={result.attributes})"
    )
    assert error_key in result.attributes, (
        f"expected '{error_key}' in attributes, got {result.attributes}"
    )


# A minimal valid qasm2 circuit for processors that need circuit content.
_QASM2 = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
    "qreg q[2];\ncreg c[2];\nh q[0];\nmeasure q -> c;\n"
)


class TestSimulatorNumericGuards:
    def test_cirq_simulator_bad_shots(self):
        from CirqSimulator import CirqSimulator
        proc = CirqSimulator()
        ctx = MockContext(Shots="abc")
        ff = MockFlowFile(content=_QASM2, attributes={"circuit.format": "qasm2"})
        _assert_failure(proc.transform(ctx, ff), "sim.error")

    def test_qrisp_simulator_bad_shots(self):
        from QrispSimulator import QrispSimulator
        proc = QrispSimulator()
        ctx = MockContext(Shots="notanumber")
        ff = MockFlowFile(content=_QASM2, attributes={"circuit.format": "qasm2"})
        _assert_failure(proc.transform(ctx, ff), "sim.error")

    def test_cirq_statevector_bad_threshold(self):
        from CirqStatevectorSimulator import CirqStatevectorSimulator
        proc = CirqStatevectorSimulator()
        ctx = MockContext(**{"Probability Threshold": "x.y"})
        ff = MockFlowFile(content=_QASM2, attributes={"circuit.format": "qasm2"})
        _assert_failure(proc.transform(ctx, ff), "sim.error")


class TestSolverNumericGuards:
    def test_cirq_qaoa_bad_layers(self):
        from CirqQAOA import CirqQAOA
        proc = CirqQAOA()
        ctx = MockContext(Layers="two")
        ff = MockFlowFile(content=b"Z0 + Z1", attributes={
            "hamiltonian.format": "sparse_pauli_op_json",
            "hamiltonian.num_qubits": "2",
        })
        _assert_failure(proc.transform(ctx, ff), "qaoa.error")

    def test_qrisp_qaoa_bad_maxiter(self):
        from QrispQAOA import QrispQAOA
        proc = QrispQAOA()
        ctx = MockContext(**{"Max Iterations": "many"})
        ff = MockFlowFile(content=b"Z0 + Z1", attributes={
            "hamiltonian.format": "sparse_pauli_op_json",
            "hamiltonian.num_qubits": "2",
        })
        _assert_failure(proc.transform(ctx, ff), "qaoa.error")

    def test_pennylane_qaoa_bad_shots(self):
        from PennylaneQAOA import PennylaneQAOA
        proc = PennylaneQAOA()
        ctx = MockContext(Shots="lots")
        ff = MockFlowFile(content=b"Z0 + Z1", attributes={
            "hamiltonian.format": "sparse_pauli_op_json",
            "hamiltonian.num_qubits": "2",
        })
        _assert_failure(proc.transform(ctx, ff), "qaoa.error")

    def test_qrisp_vqe_bad_shots(self):
        from QrispVQE import QrispVQE
        proc = QrispVQE()
        ctx = MockContext(Shots="NaNish")
        ff = MockFlowFile(content=b"Z0", attributes={
            "hamiltonian.format": "sparse_pauli_op_json",
            "hamiltonian.num_qubits": "1",
            "ansatz.format": "qrisp_spec",
            "ansatz.type": "efficient_su2",
            "ansatz.reps": "1",
        })
        _assert_failure(proc.transform(ctx, ff), "vqe.error")


class TestBuilderNumericGuards:
    def test_qrisp_qft_bad_qubit_count(self):
        from QrispQFTCircuit import QrispQFTCircuit
        proc = QrispQFTCircuit()
        ctx = MockContext(**{"Qubit Count": "three"})
        _assert_failure(proc.transform(ctx, MockFlowFile()), "circuit.error")

    def test_qiskit_grover_circuit_bad_iterations(self):
        from QiskitGroverCircuit import QiskitGroverCircuit
        proc = QiskitGroverCircuit()
        ctx = MockContext(**{"Marked State": "11", "Num Iterations": "several"})
        _assert_failure(proc.transform(ctx, MockFlowFile()), "grover.error")

    def test_molecule_hamiltonian_bad_charge(self):
        from MoleculeHamiltonian import MoleculeHamiltonian
        proc = MoleculeHamiltonian()
        ctx = MockContext(
            **{"Molecule Geometry": "H 0 0 0; H 0 0 0.74", "Charge": "neutral"}
        )
        _assert_failure(proc.transform(ctx, MockFlowFile()), "hamiltonian.error")


class TestAmplitudeEstimationGuards:
    def test_cirq_amplitude_estimation_bad_eval_qubits(self):
        from CirqAmplitudeEstimation import CirqAmplitudeEstimation
        proc = CirqAmplitudeEstimation()
        ctx = MockContext(Probability="0.25", **{"Evaluation Qubits": "a few"})
        _assert_failure(proc.transform(ctx, MockFlowFile()), "ae.error")

    def test_qrisp_amplitude_estimation_bad_epsilon(self):
        from QrispAmplitudeEstimation import QrispAmplitudeEstimation
        proc = QrispAmplitudeEstimation()
        ctx = MockContext(Probability="0.25", **{"Epsilon Target": "tiny"})
        _assert_failure(proc.transform(ctx, MockFlowFile()), "ae.error")

    def test_pennylane_amplitude_estimation_bad_shots(self):
        from PennylaneAmplitudeEstimation import PennylaneAmplitudeEstimation
        proc = PennylaneAmplitudeEstimation()
        ctx = MockContext(Probability="0.25", Shots="many")
        _assert_failure(proc.transform(ctx, MockFlowFile()), "ae.error")
