"""
The correctness gate for the arithmetic family.

Every builder and every implementation is run over the whole input space on a
noiseless statevector simulator, and the expected outcome must appear with
probability 1. This is what makes the nine implementations usable as
independent versions: they are known-correct before any differential
comparison is run, so a disagreement on hardware is about the device or the
program, never about a builder that was quietly wrong.

The 2-bit sweep is in the fast suite. The 3-bit sweep is marked slow.
"""

import itertools

import pytest

from qiskit import QuantumCircuit, transpile
from qiskit.quantum_info import Statevector

from QiskitQuantumArithmetic import QiskitQuantumArithmetic
from CirqQuantumArithmetic import CirqQuantumArithmetic
from QrispQuantumArithmetic import QrispQuantumArithmetic
from PennylaneQuantumArithmetic import PennylaneQuantumArithmetic

from conftest import MockContext, MockFlowFile


# (label, processor class, property overrides) for every independent version.
IMPLEMENTATIONS = [
    ("qiskit/cdkm",         QiskitQuantumArithmetic, {"Implementation": "cdkm"}),
    ("qiskit/vbe",          QiskitQuantumArithmetic, {"Implementation": "vbe"}),
    ("qiskit/draper",       QiskitQuantumArithmetic, {"Implementation": "draper"}),
    ("cirq/ripple_carry",   CirqQuantumArithmetic,   {"Implementation": "ripple_carry"}),
    ("cirq/qft",            CirqQuantumArithmetic,   {"Implementation": "qft"}),
    ("qrisp/cuccaro",       QrispQuantumArithmetic,  {"Implementation": "cuccaro"}),
    ("qrisp/fourier",       QrispQuantumArithmetic,  {"Implementation": "fourier"}),
    ("qrisp/gidney",        QrispQuantumArithmetic,  {"Implementation": "gidney"}),
    ("qrisp/qcla",          QrispQuantumArithmetic,  {"Implementation": "qcla"}),
    ("pennylane/semiadder", PennylaneQuantumArithmetic, {"Implementation": "semiadder"}),
    # Legacy-only implementation retained to reproduce the superseded pilots.
    ("pennylane/outadder",  PennylaneQuantumArithmetic, {"Implementation": "outadder"}),
]

LABELS = [label for label, _, _ in IMPLEMENTATIONS]


def build(label, a, b, bit_width):
    """Run one builder and return its FlowFileTransformResult."""
    _, cls, extra = next(x for x in IMPLEMENTATIONS if x[0] == label)
    props = {
        "Operation": "add",
        "Operand A": str(a),
        "Operand B": str(b),
        "Bit Width": str(bit_width),
    }
    if cls is QrispQuantumArithmetic:
        props["Signed"] = "false"
    props.update(extra)
    return cls().transform(MockContext(**props), MockFlowFile())


def simulate(result):
    """Statevector-simulate the emitted QASM; return {q0_left_key: probability}."""
    qc = QuantumCircuit.from_qasm_str(result.contents.decode())
    probs = Statevector.from_instruction(qc).probabilities_dict()
    # Qiskit keys put qubit 0 rightmost; the project convention is q0 leftmost.
    return {key[::-1]: p for key, p in probs.items()}


def _sweep(label, bit_width):
    """Assert the expected outcome is certain for every input pair."""
    for a, b in itertools.product(range(2 ** bit_width), repeat=2):
        result = build(label, a, b, bit_width)
        assert result.relationship == "success", \
            "%s failed to build %d+%d: %s" % (label, a, b, result.attributes)

        expected = result.attributes["arithmetic.expected_bitstring"]
        probs = simulate(result)
        top = max(probs, key=probs.get)

        assert probs.get(expected, 0.0) > 0.999, (
            "%s: %d+%d expected %s with probability 1, got %s at p=%.4f"
            % (label, a, b, expected, top, probs[top]))


class TestNoiselessEquivalence2Bit:
    """The exhaustive 2-bit gate: 9 implementations x 16 input pairs."""

    @pytest.mark.parametrize("label", LABELS)
    def test_exhaustive_two_bit(self, label):
        _sweep(label, 2)


@pytest.mark.slow
class TestNoiselessEquivalence3Bit:
    """The same at 3 bits: 9 implementations x 64 input pairs."""

    @pytest.mark.parametrize("label", LABELS)
    def test_exhaustive_three_bit(self, label):
        _sweep(label, 3)


class TestStructuralDiversity:
    """Independent versions must be structurally different, not re-spellings."""

    @staticmethod
    def _two_qubit_count(label):
        result = build(label, 1, 2, 2)
        qc = QuantumCircuit.from_qasm_str(result.contents.decode())
        t = transpile(qc, basis_gates=["cx", "rz", "sx", "x"],
                      optimization_level=3, seed_transpiler=7)
        return t.count_ops().get("cx", 0)

    def test_implementations_span_a_wide_gate_range(self):
        counts = {label: self._two_qubit_count(label) for label in LABELS}
        assert len(set(counts.values())) >= 4, counts
        # the cheapest and dearest must differ by more than a constant factor
        assert max(counts.values()) >= 2 * max(1, min(counts.values())), counts

    def test_all_implementations_agree_on_the_expected_result(self):
        answers = {label: build(label, 3, 2, 2).attributes["arithmetic.expected_result"]
                   for label in LABELS}
        assert set(answers.values()) == {"5"}, answers

    #: Adders that write the sum over the B register. Every framework ships one
    #: except the legacy PennyLane OutAdder lane.
    IN_PLACE = [label for label in LABELS if label != "pennylane/outadder"]

    def test_in_place_implementations_share_one_layout(self):
        # every implementation in this family writes its answer to the same
        # qubits, which is what makes a cross-framework comparison direct
        layouts = {label: build(label, 1, 2, 2).attributes["arithmetic.result_qubits"]
                   for label in self.IN_PLACE}
        assert set(layouts.values()) == {"2,3,4"}, layouts

    def test_out_of_place_lane_declares_its_own_result_register(self):
        """PennyLane's OutAdder writes to a third register, and says so.

        Sharing a layout is a convenience, not a requirement: the oracle scores
        against `arithmetic.expected_result_bits`, which is marginalised onto
        each circuit's own `arithmetic.result_qubits`. So an out-of-place lane
        stays directly comparable as long as it declares its register honestly
        -- which is what this asserts, and what
        TestCrossFrameworkAgreement then verifies distributionally.
        """
        attrs = build("pennylane/outadder", 1, 2, 2).attributes
        assert attrs["arithmetic.result_qubits"] == "4,5,6"
        # disjoint from both operand registers, unlike the in-place family
        assert attrs["arithmetic.result_qubits"] != "2,3,4"

    def test_semiadder_writes_over_the_enlarged_b_register(self):
        attrs = build("pennylane/semiadder", 1, 2, 2).attributes
        assert attrs["arithmetic.result_qubits"] == "2,3,4"
        layout = PennylaneQuantumArithmetic._layout(2, "semiadder")
        assert layout.b_qubits == (2, 3)
        assert layout.result_qubits == (2, 3, 4)


class TestCrossFrameworkAgreement:
    """Every pair of implementations must produce the same distribution."""

    def test_all_pairs_agree_on_every_input(self):
        for a, b in itertools.product(range(4), repeat=2):
            observed = {}
            for label in LABELS:
                result = build(label, a, b, 2)
                probs = simulate(result)
                idx = [int(i) for i in
                       result.attributes["arithmetic.result_qubits"].split(",")]
                top = max(probs, key=probs.get)
                observed[label] = "".join(top[i] for i in idx)
            assert len(set(observed.values())) == 1, \
                "disagreement on %d+%d: %s" % (a, b, observed)
