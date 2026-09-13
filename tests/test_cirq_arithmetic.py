"""
Tests for CirqQuantumArithmetic, the hand-written adder.

Cirq has no high-level adder, so this circuit is written gate by gate here and
is the most likely place in the arithmetic family for a real defect. These
tests pin the contract; exhaustive correctness over the input space lives in
test_arithmetic_equivalence.py.
"""

import pytest

from CirqQuantumArithmetic import CirqQuantumArithmetic

from conftest import MockContext, MockFlowFile


def _run(implementation="ripple_carry", a="2", b="3", bit_width="2", operation="add"):
    ctx = MockContext(**{
        "Implementation": implementation,
        "Operation": operation,
        "Operand A": a,
        "Operand B": b,
        "Bit Width": bit_width,
    })
    return CirqQuantumArithmetic().transform(ctx, MockFlowFile())


class TestCircuitContract:

    def test_emits_qasm2_without_measurement(self):
        r = _run()
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.framework"] == "cirq"
        qasm = r.contents.decode()
        assert "OPENQASM 2" in qasm
        assert "measure" not in qasm

    def test_bit_order_is_advertised(self):
        assert _run().attributes["sim.bit_order"] == "q0_left"

    def test_circuit_metrics_present(self):
        r = _run()
        for key in ("circuit.num_qubits", "circuit.depth", "circuit.gate_count",
                    "circuit.nonlocal_gates"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) > 1


class TestRegisterLayout:
    """Must match the Qiskit and Qrisp processors so branches are comparable."""

    def test_ripple_carry_uses_two_extra_qubits(self):
        # 2n operands + carry-out + one ancilla
        assert _run(implementation="ripple_carry", bit_width="2") \
            .attributes["circuit.num_qubits"] == "6"

    def test_qft_uses_one_extra_qubit(self):
        # 2n operands + carry-out, no ancilla
        assert _run(implementation="qft", bit_width="2") \
            .attributes["circuit.num_qubits"] == "5"

    @pytest.mark.parametrize("impl", ["ripple_carry", "qft"])
    def test_result_register_is_b_plus_carry(self, impl):
        assert _run(implementation=impl, bit_width="2") \
            .attributes["arithmetic.result_qubits"] == "2,3,4"

    def test_expected_bitstring_spans_the_register(self):
        r = _run()
        assert len(r.attributes["arithmetic.expected_bitstring"]) == \
            int(r.attributes["circuit.num_qubits"])


class TestArithmeticAttributes:

    def test_expected_result_is_classical_sum(self):
        assert _run(a="2", b="3").attributes["arithmetic.expected_result"] == "5"

    def test_full_attribute_contract(self):
        attrs = _run(implementation="qft", a="1", b="2").attributes
        assert attrs["arithmetic.operation"] == "add"
        assert attrs["arithmetic.operand_a"] == "1"
        assert attrs["arithmetic.operand_b"] == "2"
        assert attrs["arithmetic.bit_width"] == "2"
        assert attrs["arithmetic.implementation"] == "qft"
        assert attrs["arithmetic.framework"] == "cirq"

    def test_carry_out_is_set_when_it_should_be(self):
        r = _run(a="3", b="3")
        assert r.attributes["arithmetic.expected_result"] == "6"
        assert r.attributes["arithmetic.expected_result_bits"] == "011"


class TestStructuralDiversity:
    """The two constructions must be genuinely unlike each other."""

    def test_ripple_carry_uses_toffolis(self):
        assert int(_run(implementation="ripple_carry")
                   .attributes["circuit.toffoli_count"]) > 0

    def test_qft_uses_no_toffoli(self):
        assert _run(implementation="qft").attributes["circuit.toffoli_count"] == "0"

    def test_both_agree_on_the_answer(self):
        answers = {
            impl: _run(implementation=impl, a="3", b="2")
                  .attributes["arithmetic.expected_result"]
            for impl in ("ripple_carry", "qft")
        }
        assert set(answers.values()) == {"5"}


class TestFailureRoutes:

    def test_operand_out_of_range(self):
        r = _run(a="4", bit_width="2")
        assert r.relationship == "failure"
        err = r.attributes["arithmetic.error"]
        assert "operand A" in err and "2 bits" in err

    def test_negative_operand(self):
        r = _run(a="-1")
        assert r.relationship == "failure"
        assert "negative" in r.attributes["arithmetic.error"]

    def test_unknown_implementation(self):
        r = _run(implementation="nonesuch")
        assert r.relationship == "failure"
        assert "unknown implementation" in r.attributes["arithmetic.error"]

    def test_unsupported_operation(self):
        r = _run(operation="multiply")
        assert r.relationship == "failure"
        assert "not implemented" in r.attributes["arithmetic.error"]

    def test_non_integer_operand(self):
        r = _run(b="oops")
        assert r.relationship == "failure"
        assert "non-integer" in r.attributes["arithmetic.error"]
