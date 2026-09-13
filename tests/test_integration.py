"""
End-to-end pipeline integration tests.

Each test exercises a complete NiFi flow by chaining processor outputs
directly into the next processor's input via result_to_flowfile().
These tests verify cross-framework interoperability and full pipeline
correctness — not just individual processor behaviour.
"""

import json

import pytest

from conftest import MockContext, MockFlowFile, result_to_flowfile

from QiskitGroverCircuit import QiskitGroverCircuit
from QiskitPhaseOracle import QiskitPhaseOracle
from QiskitGroverOperator import QiskitGroverOperator
from QiskitAerSimulator import QiskitAerSimulator
from QiskitHadamardTransform import QiskitHadamardTransform
from QiskitStatePreparation import QiskitStatePreparation
from CirqGroverCircuit import CirqGroverCircuit
from CirqPhaseOracle import CirqPhaseOracle
from CirqGroverOperator import CirqGroverOperator
from CirqSimulator import CirqSimulator


# ---------------------------------------------------------------------------
# Qiskit-only pipelines
# ---------------------------------------------------------------------------

class TestQiskitPipelines:

    def test_grover_2q(self):
        """QiskitGroverCircuit (qasm3) → QiskitAerSimulator → correct target."""
        circuit_r = QiskitGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "qasm3",
            }),
            MockFlowFile(),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_grover_3q(self):
        """3-qubit Grover search with 2 iterations."""
        circuit_r = QiskitGroverCircuit().transform(
            MockContext(**{
                "Marked State": "101", "Num Iterations": "2",
                "Insert Barriers": "false", "Output Format": "qasm3",
            }),
            MockFlowFile(),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.attributes["sim.top_result"] == "101"

    def test_decomposed_grover_2q(self):
        """QiskitPhaseOracle → QiskitGroverOperator → QiskitAerSimulator (2-qubit)."""
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "qasm3"}),
            result_to_flowfile(oracle_r),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_decomposed_grover_3q(self):
        """QiskitPhaseOracle → QiskitGroverOperator → QiskitAerSimulator (3-qubit)."""
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "101", "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false", "Output Format": "qasm3"}),
            result_to_flowfile(oracle_r),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.attributes["sim.top_result"] == "101"

    def test_decomposed_attribute_propagation(self):
        """circuit.marked_state propagates through the decomposed Qiskit pipeline."""
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes["circuit.marked_state"] == "11"

        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "qasm3"}),
            result_to_flowfile(oracle_r),
        )
        assert operator_r.attributes.get("circuit.marked_state") == "11"

    def test_hadamard_uniform_distribution(self):
        """H⊗2 → AerSimulator should produce a roughly uniform distribution."""
        h_r = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "4096"}),
            result_to_flowfile(h_r),
        )
        counts = json.loads(sim_r.contents)
        # All 4 states should appear; none dominant (each ~25%)
        assert len(counts) == 4
        max_count = max(counts.values())
        assert max_count < 4096 * 0.5  # no state gets > 50% of shots


# ---------------------------------------------------------------------------
# Cirq-only pipelines
# ---------------------------------------------------------------------------

class TestCirqPipelines:

    def test_grover_circuit_all_in_one(self):
        """CirqGroverCircuit → CirqSimulator."""
        circuit_r = CirqGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "cirq_json",
            }),
            MockFlowFile(),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_decomposed_grover_2q(self):
        """CirqPhaseOracle → CirqGroverOperator → CirqSimulator (2-qubit)."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_decomposed_grover_3q(self):
        """CirqPhaseOracle → CirqGroverOperator → CirqSimulator (3-qubit)."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "101", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.attributes["sim.top_result"] == "101"

    def test_attribute_propagation(self):
        """circuit.marked_state propagates through the decomposed pipeline."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes["circuit.marked_state"] == "11"

        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        assert operator_r.attributes.get("circuit.marked_state") == "11"


# ---------------------------------------------------------------------------
# Cross-framework pipelines
# ---------------------------------------------------------------------------

class TestCrossFramework:

    def test_qiskit_circuit_to_cirq_simulator(self):
        """QiskitGroverCircuit (qasm2) → CirqSimulator."""
        circuit_r = QiskitGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "qasm2",
            }),
            MockFlowFile(),
        )
        assert circuit_r.attributes["circuit.format"] == "qasm2"

        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.framework"] == "cirq"
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_cirq_circuit_to_qiskit_simulator(self):
        """CirqGroverCircuit (qasm2) → QiskitAerSimulator."""
        circuit_r = CirqGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "qasm2",
            }),
            MockFlowFile(),
        )
        assert circuit_r.attributes["circuit.format"] == "qasm2"

        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_cirq_decomposed_to_qiskit_simulator(self):
        """CirqPhaseOracle → CirqGroverOperator (qasm2) → QiskitAerSimulator."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "qasm2"}),
            result_to_flowfile(oracle_r),
        )
        assert operator_r.attributes["circuit.format"] == "qasm2"

        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.top_result"] == "11"


    def test_cirq_oracle_to_qiskit_operator(self):
        """CirqPhaseOracle (qasm2) → QiskitGroverOperator → QiskitAerSimulator.

        A Grover search assembled from components of two frameworks: the oracle
        is built by Cirq, the operator and simulation run in Qiskit.
        """
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes["circuit.format"] == "qasm2"

        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "qasm3"}),
            result_to_flowfile(oracle_r),
        )
        assert operator_r.relationship == "success"
        assert operator_r.attributes.get("circuit.marked_state") == "11"

        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.top_result"] == "11"

    def test_qiskit_oracle_to_cirq_operator(self):
        """QiskitPhaseOracle (qasm2) → CirqGroverOperator → CirqSimulator.

        The reverse assembly: oracle built by Qiskit, operator and simulation
        run in Cirq.
        """
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes["circuit.format"] == "qasm2"

        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        assert operator_r.relationship == "success"

        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(operator_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.top_result"] == "11"


    def test_no_stale_svg_cirq_oracle_to_qiskit_operator(self):
        """NiFi merges attributes downstream, so QiskitGroverOperator must blank
        the Cirq oracle's circuit.svg — otherwise QuanifiReport renders the
        stale oracle drawing instead of the full Grover circuit (Flow B bug)."""
        from conftest import result_to_flowfile_merged
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes.get("circuit.svg")  # Cirq emits a real SVG

        oracle_ff = result_to_flowfile(oracle_r)
        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "qasm3"}),
            oracle_ff,
        )
        merged = result_to_flowfile_merged(operator_r, oracle_ff)
        assert merged.getAttribute("circuit.svg") == ""   # stale SVG blanked
        assert merged.getAttribute("circuit.qasm3")       # fresh full circuit renders

    def test_no_stale_qasm_qiskit_oracle_to_cirq_operator(self):
        """The mirror image: CirqGroverOperator must blank the Qiskit oracle's
        circuit.qasm2/qasm3 so the report's QASM panel isn't the stale oracle."""
        from conftest import result_to_flowfile_merged
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes.get("circuit.qasm2")

        oracle_ff = result_to_flowfile(oracle_r)
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "1", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            oracle_ff,
        )
        merged = result_to_flowfile_merged(operator_r, oracle_ff)
        assert merged.getAttribute("circuit.qasm3") == ""
        assert merged.getAttribute("circuit.qasm2") == ""
        assert merged.getAttribute("circuit.svg")         # fresh Cirq SVG renders

    def test_no_stale_svg_cirq_oracle_to_qiskit_hadamard(self):
        """QiskitHadamardTransform is compose-capable and must blank a stale
        Cirq SVG so the report shows the new Qiskit circuit, not the oracle's."""
        from conftest import result_to_flowfile_merged
        from QiskitHadamardTransform import QiskitHadamardTransform
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes.get("circuit.svg")
        oracle_ff = result_to_flowfile(oracle_r)
        had_r = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm3"}),
            oracle_ff,
        )
        merged = result_to_flowfile_merged(had_r, oracle_ff)
        assert merged.getAttribute("circuit.svg") == ""

    def test_no_stale_qasm_qiskit_oracle_to_cirq_hadamard(self):
        """CirqHadamardTransform emits an SVG but no qasm3 — it must blank a
        stale upstream Qiskit qasm3 so the report's QASM panel isn't stale."""
        from conftest import result_to_flowfile_merged
        from CirqHadamardTransform import CirqHadamardTransform
        oracle_r = QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes.get("circuit.qasm3")
        oracle_ff = result_to_flowfile(oracle_r)
        had_r = CirqHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "cirq_json"}),
            oracle_ff,
        )
        merged = result_to_flowfile_merged(had_r, oracle_ff)
        assert merged.getAttribute("circuit.qasm3") == ""

    def test_mutant_blanks_stale_svg(self):
        """A QuantumMutator mutant is a fresh circuit: it must blank a stale
        Cirq SVG and the non-emitted QASM dialect so the report renders the
        MUTANT, not the unmutated upstream circuit."""
        from conftest import result_to_flowfile_merged
        from QuantumMutator import QuantumMutator
        # Build a Cirq circuit first so a real circuit.svg rides on the FlowFile.
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert oracle_r.attributes.get("circuit.svg")
        # Then a Qiskit circuit-builder emitting qasm2 content for the mutator.
        qasm_r = QiskitGroverCircuit().transform(
            MockContext(**{"Marked State": "11", "Num Iterations": "1",
                           "Insert Barriers": "false", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert qasm_r.attributes.get("circuit.format") == "qasm2"
        # Carry the stale Cirq SVG onto the Qiskit circuit's FlowFile.
        base_ff = result_to_flowfile(qasm_r)
        base_ff._attrs["circuit.svg"] = oracle_r.attributes["circuit.svg"]
        mut_r = QuantumMutator().transform(
            MockContext(**{"Mutation Operators": "gate.remove", "Mutation Seed": "7",
                           "Output Format": "qasm2"}),
            base_ff,
        )
        assert mut_r.relationship == "success", mut_r.attributes
        merged = result_to_flowfile_merged(mut_r, base_ff)
        assert merged.getAttribute("circuit.svg") == ""
        assert merged.getAttribute("circuit.qasm3") == ""  # qasm2 emitted, qasm3 blanked


# ---------------------------------------------------------------------------
# Qrisp cross-framework (slow — real simulator)
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestQrispCrossFramework:

    def test_qiskit_circuit_to_qrisp_simulator(self):
        """QiskitGroverCircuit (qasm2) → QrispSimulator."""
        from QrispSimulator import QrispSimulator

        circuit_r = QiskitGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "qasm2",
            }),
            MockFlowFile(),
        )
        sim_r = QrispSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.relationship == "success"
        assert sim_r.attributes["sim.framework"] == "qrisp"
        counts = json.loads(sim_r.contents)
        assert max(counts, key=counts.get) == "11"

    def test_cirq_circuit_to_qrisp_simulator(self):
        """CirqGroverCircuit (qasm2) → QrispSimulator."""
        from QrispSimulator import QrispSimulator

        circuit_r = CirqGroverCircuit().transform(
            MockContext(**{
                "Marked State": "11", "Num Iterations": "1",
                "Insert Barriers": "false", "Output Format": "qasm2",
            }),
            MockFlowFile(),
        )
        sim_r = QrispSimulator().transform(
            MockContext(**{"Shots": "512"}),
            result_to_flowfile(circuit_r),
        )
        assert sim_r.relationship == "success"
        counts = json.loads(sim_r.contents)
        assert max(counts, key=counts.get) == "11"
