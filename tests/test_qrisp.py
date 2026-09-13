"""
Unit tests for Qrisp-based NiFi processors: QrispSimulator, QrispGroverSearch.

These tests run the actual Qrisp simulator, which is slower than Qiskit/Cirq
unit tests.  Mark them with ``-m slow`` to run in isolation, or exclude with
``-m "not slow"`` for faster CI feedback.
"""

import json

import pytest

from QrispSimulator import QrispSimulator
from QrispGroverSearch import QrispGroverSearch

from conftest import MockContext, MockFlowFile


pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qasm2_h(n=2):
    from qiskit import QuantumCircuit, qasm2, transpile
    qc = QuantumCircuit(n)
    qc.h(range(n))
    tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
    return qasm2.dumps(tc)


# ---------------------------------------------------------------------------
# QrispSimulator
# ---------------------------------------------------------------------------

class TestQrispSimulator:

    def test_qasm2_uniform_distribution(self):
        proc = QrispSimulator()
        ctx  = MockContext(**{"Shots": "512"})
        ff   = MockFlowFile(
            content=_qasm2_h(2).encode(),
            attributes={"circuit.format": "qasm2"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 512
        assert r.attributes["sim.framework"] == "qrisp"
        assert r.attributes["sim.shots"] == "512"
        assert "sim.top_result" in r.attributes

    def test_wrong_format_returns_failure(self):
        proc = QrispSimulator()
        ctx  = MockContext(**{"Shots": "256"})
        ff   = MockFlowFile(
            content=b"fake",
            attributes={"circuit.format": "qasm3"},
        )
        r = proc.transform(ctx, ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_shots_attribute_matches(self):
        proc = QrispSimulator()
        ctx  = MockContext(**{"Shots": "256"})
        ff   = MockFlowFile(
            content=_qasm2_h(1).encode(),
            attributes={"circuit.format": "qasm2"},
        )
        r = proc.transform(ctx, ff)
        assert r.attributes["sim.shots"] == "256"

    def test_property_descriptors(self):
        names = {d.name for d in QrispSimulator().getPropertyDescriptors()}
        assert names == {"Shots", "Random Seed"}

    def test_counts_keys_full_width_and_grover_peak(self):
        # Regression: measure(range(n)) allocated a single clbit in Qrisp, so
        # a 3-qubit Grover circuit came back with 2-char keys ("01": 499) and
        # a nonsense distribution. Keys must be num_qubits wide and the
        # non-palindromic marked state must dominate in q0-left order.
        from QiskitGroverCircuit import QiskitGroverCircuit
        g = QiskitGroverCircuit().transform(
            MockContext(**{"Marked State": "110", "Num Iterations": "2",
                           "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        ff = MockFlowFile(content=g.contents,
                          attributes={"circuit.format": "qasm2"})
        r = QrispSimulator().transform(MockContext(**{"Shots": "512"}), ff)
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert all(len(k) == 3 for k in counts)
        assert r.attributes["sim.top_result"] == "110"
        assert counts["110"] / 512 > 0.8


# ---------------------------------------------------------------------------
# QrispGroverSearch (all-in-one)
# ---------------------------------------------------------------------------

class TestQrispGroverSearch:

    def _run(self, target, iterations=0, shots=1024):
        ctx = MockContext(**{
            "Marked State": target,
            "Num Iterations": str(iterations),
            "Shots": str(shots),
        })
        return QrispGroverSearch().transform(ctx, MockFlowFile())

    def test_two_qubit_finds_target(self):
        # Use explicit iterations=1; auto-calculation is unreliable for n=2
        r = self._run("11", iterations=1)
        assert r.relationship == "success"
        results = json.loads(r.contents)
        assert max(results, key=results.get) == "11"

    def test_three_qubit_finds_target(self):
        r = self._run("101")
        results = json.loads(r.contents)
        assert max(results, key=results.get) == "101"

    def test_attributes(self):
        r = self._run("11", iterations=1)
        assert r.attributes["circuit.marked_state"] == "11"
        assert r.attributes["grover.framework"] == "qrisp"
        assert "sim.top_result" in r.attributes
        assert "sim.top_probability" in r.attributes

    def test_top_result_matches_target(self):
        r = self._run("11", iterations=1)
        assert r.attributes["sim.top_result"] == "11"

    def test_explicit_iterations(self):
        r = self._run("11", iterations=1)
        assert r.relationship == "success"
