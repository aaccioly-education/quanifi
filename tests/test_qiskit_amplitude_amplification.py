"""
Tests for QiskitAmplitudeAmplification.

The processor wraps qiskit-algorithms AmplificationProblem + Grover.construct_circuit()
to build Quantum Amplitude Amplification (QAA) circuits without measuring them.
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "nifi_extensions"))

from conftest import MockContext, MockFlowFile, result_to_flowfile
from QiskitAmplitudeAmplification import QiskitAmplitudeAmplification


def _run(props=None):
    """Run the processor with default properties overridden by props."""
    defaults = {
        "Marked State": "11",
        "Num Iterations": "1",
        "Insert Barriers": "false",
        "Output Format": "qasm3",
    }
    if props:
        defaults.update(props)
    proc = QiskitAmplitudeAmplification()
    ctx = MockContext(**defaults)
    ff = MockFlowFile()  # no circuit.format → standalone mode
    return proc.transform(ctx, ff)


# ---------------------------------------------------------------------------
# Standalone mode — basic structure
# ---------------------------------------------------------------------------

class TestStandalone:

    def test_success_relationship(self):
        assert _run().relationship == "success"

    def test_algorithm_attribute(self):
        assert _run().attributes["circuit.algorithm"] == "amplitude_amplification"

    def test_mode_attribute(self):
        assert _run().attributes["circuit.mode"] == "standalone"

    def test_marked_state_attribute(self):
        assert _run().attributes["circuit.marked_state"] == "11"

    def test_framework_attribute(self):
        assert _run().attributes["circuit.framework"] == "qiskit"

    def test_num_qubits_from_marked_state(self):
        assert _run({"Marked State": "101"}).attributes["circuit.num_qubits"] == "3"

    def test_num_iterations_attribute(self):
        r = _run({"Num Iterations": "2"})
        assert r.attributes["circuit.num_iterations"] == "2"

    def test_qasm3_output_starts_with_openqasm(self):
        body = _run({"Output Format": "qasm3"}).contents.decode()
        assert body.startswith("OPENQASM")

    def test_qasm3_attribute_set_when_format_qasm3(self):
        r = _run()
        assert "circuit.qasm3" in r.attributes
        assert r.attributes["circuit.qasm3"].startswith("OPENQASM")

    def test_qasm3_attribute_absent_for_qpy(self):
        r = _run({"Output Format": "qpy"})
        assert "circuit.qasm3" not in r.attributes

    def test_qpy_output_is_bytes(self):
        r = _run({"Output Format": "qpy"})
        # QPY binary starts with 'QISKIT'
        assert r.contents[:6] == b"QISKIT"

    def test_format_attribute_reflects_chosen_format(self):
        assert _run({"Output Format": "qpy"}).attributes["circuit.format"] == "qpy"

    def test_metrics_emitted(self):
        r = _run()
        for attr in ["circuit.depth", "circuit.gate_count",
                     "circuit.nonlocal_gates", "circuit.t_count"]:
            assert attr in r.attributes, f"Missing {attr}"

    def test_depth_is_positive_integer(self):
        depth = int(_run().attributes["circuit.depth"])
        assert depth > 0

    def test_gate_count_positive(self):
        assert int(_run().attributes["circuit.gate_count"]) > 0

    def test_nonlocal_gates_positive_for_nontrivial(self):
        # 2 qubits, 1 iteration → should have at least one 2-qubit gate
        assert int(_run().attributes["circuit.nonlocal_gates"]) > 0

    def test_zero_iterations_still_succeeds(self):
        r = _run({"Num Iterations": "0"})
        assert r.relationship == "success"
        assert r.attributes["circuit.num_iterations"] == "0"

    def test_zero_iterations_smaller_depth(self):
        depth_0 = int(_run({"Num Iterations": "0"}).attributes["circuit.depth"])
        depth_1 = int(_run({"Num Iterations": "1"}).attributes["circuit.depth"])
        assert depth_0 < depth_1

    def test_more_iterations_deeper_circuit(self):
        depth_1 = int(_run({"Num Iterations": "1"}).attributes["circuit.depth"])
        depth_2 = int(_run({"Num Iterations": "2"}).attributes["circuit.depth"])
        assert depth_2 > depth_1

    def test_larger_marked_state_more_qubits(self):
        r3 = _run({"Marked State": "101"})
        r2 = _run({"Marked State": "11"})
        assert int(r3.attributes["circuit.num_qubits"]) > int(r2.attributes["circuit.num_qubits"])

    def test_marked_state_all_zeros(self):
        r = _run({"Marked State": "000"})
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_single_qubit(self):
        r = _run({"Marked State": "1", "Num Iterations": "1"})
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "1"

    def test_insert_barriers_succeeds(self):
        r = _run({"Insert Barriers": "true"})
        assert r.relationship == "success"

    def test_property_descriptors(self):
        names = {d.name for d in QiskitAmplitudeAmplification().getPropertyDescriptors()}
        assert names == {"Marked State", "Num Iterations", "Insert Barriers", "Output Format"}


# ---------------------------------------------------------------------------
# Compose mode — incoming FlowFile is the oracle
# ---------------------------------------------------------------------------

class TestComposeMode:

    def _make_oracle_flowfile(self, n=2):
        """Build a simple phase oracle for |1...1⟩ and serialise to qasm3."""
        from qiskit import QuantumCircuit, qasm3
        oracle = QuantumCircuit(n, name="test_oracle")
        # H·MCX·H = multi-controlled Z (phase flip of |1...1⟩)
        oracle.h(n - 1)
        if n > 1:
            oracle.mcx(list(range(n - 1)), n - 1)
        else:
            oracle.x(0)
        oracle.h(n - 1)
        qasm3_str = qasm3.dumps(oracle)
        return MockFlowFile(
            content=qasm3_str.encode(),
            attributes={"circuit.format": "qasm3"},
        )

    def test_compose_mode_succeeds(self):
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, self._make_oracle_flowfile(2))
        assert r.relationship == "success"

    def test_compose_mode_attribute(self):
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, self._make_oracle_flowfile(2))
        assert r.attributes["circuit.mode"] == "compose"

    def test_compose_mode_no_marked_state_attribute(self):
        """In compose mode the marked_state attribute must not be set."""
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, self._make_oracle_flowfile(2))
        assert "circuit.marked_state" not in r.attributes

    def test_compose_mode_num_qubits_from_oracle(self):
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, self._make_oracle_flowfile(3))
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_compose_mode_output_is_valid_qasm3(self):
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, self._make_oracle_flowfile(2))
        assert r.contents.decode().startswith("OPENQASM")

    def test_compose_mode_qasm2_oracle(self):
        """Compose mode also accepts qasm2-format oracle circuits."""
        from qiskit import QuantumCircuit, qasm2
        oracle = QuantumCircuit(2, name="oracle_q2")
        oracle.h(1); oracle.cx(0, 1); oracle.h(1)
        qasm2_str = qasm2.dumps(oracle)
        ff = MockFlowFile(
            content=qasm2_str.encode(),
            attributes={"circuit.format": "qasm2"},
        )
        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.mode"] == "compose"


# ---------------------------------------------------------------------------
# Pipeline: QAA → QiskitAerSimulator
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestPipeline:

    def test_2q_1iter_dominates_marked_state(self):
        """
        2 qubits, 1 marked state ('11'), 1 Grover iteration.
        For n=2: θ = arcsin(1/2) = π/6, P after 1 iter = sin²(3π/6) = 1.0.
        Nearly all shots should land on '11'.
        """
        from QiskitAerSimulator import QiskitAerSimulator

        r_aa = _run({"Marked State": "11", "Num Iterations": "1", "Output Format": "qasm3"})
        r_sim = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(r_aa),
        )
        assert r_sim.relationship == "success"
        counts = json.loads(r_sim.contents)
        total = sum(counts.values())
        assert counts.get("11", 0) / total > 0.95

    def test_3q_2iter_dominates_marked_state(self):
        """
        3 qubits, marked '101', 2 Grover iterations.
        P after 2 iters ≈ sin²(5·arcsin(1/√8)) ≈ 0.97.
        """
        from QiskitAerSimulator import QiskitAerSimulator

        r_aa = _run({
            "Marked State": "101", "Num Iterations": "2", "Output Format": "qasm3"
        })
        r_sim = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "2048"}),
            result_to_flowfile(r_aa),
        )
        assert r_sim.relationship == "success"
        counts = json.loads(r_sim.contents)
        total = sum(counts.values())
        assert counts.get("101", 0) / total > 0.85

    def test_zero_iterations_gives_uniform_distribution(self):
        """
        0 iterations = uniform superposition only.
        2 qubits → 4 states each with ~25% probability.
        """
        from QiskitAerSimulator import QiskitAerSimulator

        r_aa = _run({"Marked State": "11", "Num Iterations": "0", "Output Format": "qasm3"})
        r_sim = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "4096"}),
            result_to_flowfile(r_aa),
        )
        assert r_sim.relationship == "success"
        counts = json.loads(r_sim.contents)
        assert len(counts) == 4, "Should see all 4 basis states"
        for bitstr, count in counts.items():
            assert 0.15 < count / 4096 < 0.35, f"State {bitstr!r} not uniformly distributed"

    def test_compose_pipeline_amplifies_marked_state(self):
        """
        Compose mode: pass a phase oracle for |11⟩ from QiskitAmplitudeAmplification,
        then simulate.  1 iteration should strongly amplify |11⟩.
        """
        from QiskitAerSimulator import QiskitAerSimulator
        from qiskit import QuantumCircuit, qasm3

        # Build a clean phase oracle for |11⟩
        oracle = QuantumCircuit(2, name="oracle_11")
        oracle.h(1)
        oracle.cx(0, 1)
        oracle.h(1)
        ff_oracle = MockFlowFile(
            content=qasm3.dumps(oracle).encode(),
            attributes={"circuit.format": "qasm3"},
        )

        proc = QiskitAmplitudeAmplification()
        ctx = MockContext(**{
            "Marked State": "11", "Num Iterations": "1",
            "Insert Barriers": "false", "Output Format": "qasm3",
        })
        r_aa = proc.transform(ctx, ff_oracle)
        assert r_aa.relationship == "success"

        r_sim = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(r_aa),
        )
        assert r_sim.relationship == "success"
        counts = json.loads(r_sim.contents)
        total = sum(counts.values())
        assert counts.get("11", 0) / total > 0.80
