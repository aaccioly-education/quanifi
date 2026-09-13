"""ReadoutBaselineCircuit -- the readout-error baseline for the Grover N-M
hardware matrix.

It prepares a target bitstring with X gates only (no superposition, no
entanglement, no two-qubit gates) and measures it. On ideal hardware every
shot returns the target, so any departure is pure state-preparation-and-
measurement (SPAM) error for those specific physical qubits, separable from
the gate error the Grover builders measure.

Bit order is the trap this suite exists to catch: this repo's canonical order
is q0-left (the leftmost character of Marked State is qubit 0), but Qiskit's
own bitstring keys are little-endian. A palindrome like '11' or '0110' cannot
detect a reversal -- both readings are the same string -- so the bit-order
proof below uses the asymmetric case '10'.
"""

import pytest

from conftest import MockContext, MockFlowFile

from ReadoutBaselineCircuit import ReadoutBaselineCircuit

qiskit = pytest.importorskip("qiskit")
from qiskit import qasm2, transpile  # noqa: E402
from qiskit_aer import AerSimulator  # noqa: E402


def _run(target="10", fmt="qasm2"):
    ctx = MockContext(**{"Marked State": target, "Output Format": fmt})
    return ReadoutBaselineCircuit().transform(ctx, MockFlowFile())


class TestBasicBuild:

    def test_success_relationship(self):
        r = _run("0111")
        assert r.relationship == "success"

    def test_num_qubits_from_target_length(self):
        r = _run("0111")
        assert r.attributes["circuit.num_qubits"] == "4"

    def test_marked_state_attribute(self):
        r = _run("0111")
        assert r.attributes["circuit.marked_state"] == "0111"

    def test_format_attribute(self):
        r = _run("0111")
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.contents.decode().startswith("OPENQASM 2")


class TestCircuitContract:
    """Acceptance criterion 1: '0111' parses and contains exactly three X
    gates, four measurements, and zero two-qubit gates."""

    def _parsed(self, target="0111"):
        r = _run(target)
        assert r.relationship == "success", r.attributes
        return qasm2.loads(r.contents.decode(),
                           custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)

    def test_exactly_three_x_gates(self):
        circuit = self._parsed("0111")
        assert circuit.count_ops().get("x", 0) == 3

    def test_exactly_four_measurements(self):
        circuit = self._parsed("0111")
        assert circuit.count_ops().get("measure", 0) == 4

    def test_zero_two_qubit_gates(self):
        circuit = self._parsed("0111")
        assert circuit.num_nonlocal_gates() == 0

    def test_x_gates_land_on_the_1_bits_in_q0_left_order(self):
        """target[0] is qubit 0 -- '0111' has '0' at position 0 (q0 untouched)
        and '1' at positions 1, 2, 3 (q1, q2, q3 each get exactly one X)."""
        circuit = self._parsed("0111")
        x_qubits = sorted(
            circuit.find_bit(instr.qubits[0]).index
            for instr in circuit.data if instr.operation.name == "x"
        )
        assert x_qubits == [1, 2, 3]


class TestReportedMetricsMatchTheCircuit:

    def test_gate_count_excludes_barrier_and_measure(self):
        r = _run("0111")
        assert r.attributes["circuit.gate_count"] == "3"

    def test_nonlocal_gates_is_zero(self):
        r = _run("0111")
        assert r.attributes["circuit.nonlocal_gates"] == "0"

    def test_depth_is_present(self):
        r = _run("0111")
        assert int(r.attributes["circuit.depth"]) > 0


class TestAttributeContract:
    """The output attribute contract the Grover builders share, plus the
    two fields specific to this processor."""

    def test_builder_component(self):
        r = _run("10")
        assert r.attributes["builder.component"] == "ReadoutBaseline"

    def test_builder_framework(self):
        r = _run("10")
        assert r.attributes["builder.framework"] == "qiskit"

    def test_grover_kind_is_readout(self):
        """The batch submitter's case-major ordering ranks kinds, and
        `readout` must sort first."""
        r = _run("10")
        assert r.attributes["grover.kind"] == "readout"

    def test_circuit_bit_order(self):
        r = _run("10")
        assert r.attributes["circuit.bit_order"] == "q0_left"

    def test_svg_blanked_for_cross_framework_chains(self):
        r = _run("10")
        assert r.attributes["circuit.svg"] == ""


class TestBitOrder:
    """Acceptance criterion 2. Proven against the asymmetric case '10':
    a palindrome like '11' cannot detect a reversal because it reads the
    same both ways, so it is not used here.

    This simulates with plain `qiskit_aer.AerSimulator`, not by routing the
    already-measured circuit back through the repo's `QiskitAerSimulator`
    NiFi processor. That processor calls `circuit.measure_all()`
    unconditionally (nifi_extensions/QiskitAerSimulator.py:265) with no guard
    for a circuit that already carries measurements, so an already-measured
    FlowFile picks up a *second* classical register and Aer reports counts
    like '01 01' -- which the processor's own reversal then turns into a
    4-character string ('1010') instead of the 2-character canonical result.
    That is a pre-existing gap in a file this task does not own (it has never
    had to accept a pre-measured circuit before), not a bug in
    ReadoutBaselineCircuit; it is reproduced and asserted below so the gap is
    pinned rather than silently worked around, and the actual bit-order claim
    is verified with a direct, single measurement.
    """

    def _canonical_counts(self, target, shots=1024, seed=11):
        r = _run(target)
        assert r.relationship == "success", r.attributes
        circuit = qasm2.loads(r.contents.decode(),
                              custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
        backend = AerSimulator()
        result = backend.run(transpile(circuit, backend),
                             shots=shots, seed_simulator=seed).result()
        counts = result.get_counts()
        normalised = {}
        for key, cnt in counts.items():
            k = key.replace(" ", "")[::-1]
            normalised[k] = normalised.get(k, 0) + cnt
        return normalised, shots

    def test_asymmetric_state_recovers_canonical_q0_left_order(self):
        counts, shots = self._canonical_counts("10")
        assert counts == {"10": shots}

    def test_a_palindrome_would_prove_nothing(self):
        """'11' reads the same in either bit order -- documented, not used as
        the bit-order proof above."""
        counts, shots = self._canonical_counts("11")
        assert counts == {"11": shots}  # true under EITHER order; uninformative

    def test_routing_through_QiskitAerSimulator_double_measures(self):
        """Pins the QiskitAerSimulator limitation described in this class's
        docstring, so nobody 'fixes' the bit-order test by routing through it
        without first fixing that processor."""
        from QiskitAerSimulator import QiskitAerSimulator

        r = _run("10")
        sim = QiskitAerSimulator()
        ff = MockFlowFile(content=r.contents, attributes=r.attributes)
        sim_ctx = MockContext(**{"Shots": "1024", "Random Seed": "11",
                                 "Noise Model": "none"})
        sim_result = sim.transform(sim_ctx, ff)
        assert sim_result.attributes["sim.top_result"] == "1010", (
            "if this now reads '10', QiskitAerSimulator started guarding "
            "against pre-measured input -- the workaround in this test class "
            "is no longer needed and TestBitOrder should route through it "
            "directly instead"
        )


class TestFailurePath:

    def test_empty_marked_state_routes_to_failure(self):
        r = _run("")
        assert r.relationship == "failure"
        assert "grover.error" in r.attributes

    def test_non_binary_marked_state_routes_to_failure(self):
        r = _run("012")
        assert r.relationship == "failure"
        assert "grover.error" in r.attributes

    def test_failure_has_no_stack_trace(self):
        r = _run("abc")
        assert r.relationship == "failure"
        assert "Traceback" not in r.attributes.get("grover.error", "")
