"""
Tests for BraketSimulator — the Amazon Braket local-simulator counts engine.

Covers: the simulator contract (sim.* attributes, counts JSON), the native
qasm3 path (stdgates.inc inlining), the qasm2 translation path (flagged via
sim.translation), Braket's MSB-first bit order, and cross-framework interop
(Qiskit- and Cirq-built circuits running on Braket).
"""

import json

from BraketSimulator import BraketSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _qiskit_qasm3_ff(build):
    """FlowFile carrying a Qiskit-built circuit serialised as qasm3."""
    from qiskit import qasm3
    qc = build()
    return MockFlowFile(content=qasm3.dumps(qc).encode("utf-8"),
                        attributes={"circuit.format": "qasm3"})


class TestBraketSimulatorQasm3:

    def _ghz_ff(self):
        def build():
            from qiskit import QuantumCircuit
            qc = QuantumCircuit(2)
            qc.h(0)
            qc.cx(0, 1)
            return qc
        return _qiskit_qasm3_ff(build)

    def test_counts_contract(self):
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "256"}), self._ghz_ff())
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "braket"
        assert r.attributes["report.type"] == "simulation"
        assert r.attributes["sim.shots"] == "256"
        # Both formats are re-based onto Braket-native gates via Qiskit and
        # flagged, since inlined stdgates definitions sample wrongly (see
        # braket_qasm.py).
        assert r.attributes["sim.translation"] == (
            "qasm3 -> braket-native basis via qiskit")
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 256
        assert all(isinstance(v, int) for v in counts.values())
        # GHZ: only the two correlated states appear
        assert set(counts.keys()) <= {"00", "11"}
        vals = list(counts.values())
        assert vals == sorted(vals, reverse=True)
        assert r.attributes["sim.top_result"] == next(iter(counts))

    def test_bit_order_is_msb_first(self):
        # X on q1 of 3 qubits -> "010" iff qubit 0 is the leftmost bit
        # (Braket follows the Cirq convention, not Qiskit's).
        def build():
            from qiskit import QuantumCircuit
            qc = QuantumCircuit(3)
            qc.x(1)
            return qc
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "32"}), _qiskit_qasm3_ff(build))
        assert r.relationship == "success"
        assert json.loads(r.contents) == {"010": 32}
        assert r.attributes["sim.top_probability"] == "1.0000"

    def test_explicit_measurements_accepted(self):
        # Qiskit's measure_all() emits barrier + per-bit measure statements;
        # they must survive the Braket-native re-basing.
        def build():
            from qiskit import QuantumCircuit
            qc = QuantumCircuit(2)
            qc.h(0)
            qc.cx(0, 1)
            qc.measure_all()
            return qc
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "64"}), _qiskit_qasm3_ff(build))
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 64


class TestBraketSimulatorQasm2:

    def test_qiskit_built_qasm2_translated(self):
        from QiskitHadamardTransform import QiskitHadamardTransform
        h = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "512"}), result_to_flowfile(h))
        assert r.relationship == "success"
        assert r.attributes["sim.translation"] == (
            "qasm2 -> braket-native basis via qiskit")
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 512
        assert set(counts.keys()) <= {"00", "01", "10", "11"}

    def test_grover_phase_kickback_peak(self):
        # Regression: the old stdgates-inlining translation gave Braket wrong
        # relative phases in multi-controlled decompositions — the 4-qubit
        # Grover peak split ~50/50 between "0110" and "0111". With the
        # Braket-native basis the marked state must dominate outright.
        from QiskitGroverCircuit import QiskitGroverCircuit
        g = QiskitGroverCircuit().transform(
            MockContext(**{"Marked State": "0110", "Num Iterations": "2",
                           "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "512"}), result_to_flowfile(g))
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert r.attributes["sim.top_result"] == "0110"
        assert counts["0110"] / 512 > 0.8

    def test_cirq_built_qasm2_runs(self):
        from CirqQFTCircuit import CirqQFTCircuit
        qft = CirqQFTCircuit().transform(
            MockContext(**{"Qubit Count": "3", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        assert qft.relationship == "success"
        r = BraketSimulator().transform(
            MockContext(**{"Shots": "128"}), result_to_flowfile(qft))
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "braket"
        assert sum(json.loads(r.contents).values()) == 128


class TestBraketSimulatorErrors:

    def test_unsupported_format_fails(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "qpy"})
        r = BraketSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_missing_format_fails(self):
        ff = MockFlowFile(content=b"...")
        r = BraketSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_malformed_qasm3_fails(self):
        # Qiskit's qasm3 parser raises QASM3Error (not ValueError) — it must
        # still route to failure rather than escape transform().
        ff = MockFlowFile(content=b"OPENQASM 3.0;\nthis is not qasm;",
                          attributes={"circuit.format": "qasm3"})
        r = BraketSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes
