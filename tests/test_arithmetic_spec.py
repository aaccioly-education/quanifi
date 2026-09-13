"""
Contract tests for arithmetic_spec: the classical answer, the q0-left bitstring
encoding, carry-out at the width boundary, and operand-range rejection.

These pin the conventions every arithmetic builder must follow, so an
endianness or width regression fails here first, before any circuit is built.
"""

import pytest

from arithmetic_spec import (
    ArithmeticSpecError,
    RegisterLayout,
    classical_result,
    decode_value,
    describe,
    encode_value,
    expected_bitstring,
    expected_result_bits,
    marginalize,
    result_width,
    validate_operands,
)


# A 2-bit half adder laid out as: a on q0..q1, b on q2..q3, result on q2..q4
# (in-place over b, with the carry-out at q4).  This mirrors the ripple-carry
# family, where the answer overwrites one operand.
LAYOUT_2BIT = RegisterLayout(
    num_qubits=5,
    a_qubits=(0, 1),
    b_qubits=(2, 3),
    result_qubits=(2, 3, 4),
)


class TestClassicalResult:

    @pytest.mark.parametrize("a,b,expected", [
        (0, 0, 0), (1, 0, 1), (2, 3, 5), (3, 3, 6),
    ])
    def test_add(self, a, b, expected):
        assert classical_result("add", a, b, 2) == expected

    def test_subtract_is_modular(self):
        assert classical_result("subtract", 3, 1, 2) == 2
        # 1 - 3 wraps in two's complement over 2 bits
        assert classical_result("subtract", 1, 3, 2) == 2

    def test_multiply(self):
        assert classical_result("multiply", 3, 3, 2) == 9

    def test_unknown_operation(self):
        with pytest.raises(ArithmeticSpecError, match="unknown operation"):
            classical_result("divide", 1, 1, 2)


class TestResultWidth:

    def test_add_carries_one_bit(self):
        assert result_width("add", 2) == 3

    def test_multiply_doubles(self):
        assert result_width("multiply", 3) == 6

    def test_subtract_stays_in_width(self):
        assert result_width("subtract", 4) == 4


class TestEncoding:
    """q0 is leftmost, and bit i of the value sits on qubit i."""

    def test_lsb_is_leftmost(self):
        assert encode_value(1, 3) == "100"

    def test_msb_is_rightmost(self):
        assert encode_value(4, 3) == "001"

    def test_zero_and_full(self):
        assert encode_value(0, 3) == "000"
        assert encode_value(7, 3) == "111"

    @pytest.mark.parametrize("value", range(8))
    def test_round_trip(self, value):
        assert decode_value(encode_value(value, 3)) == value

    def test_overflow_rejected(self):
        with pytest.raises(ArithmeticSpecError, match="does not fit"):
            encode_value(8, 3)


class TestExpectedBitstring:

    def test_full_register_covers_every_qubit(self):
        bits = expected_bitstring(LAYOUT_2BIT, "add", 2, 3, 2)
        assert len(bits) == LAYOUT_2BIT.num_qubits

    def test_operand_a_is_preserved(self):
        # a = 2 -> bits "01" on q0..q1
        bits = expected_bitstring(LAYOUT_2BIT, "add", 2, 3, 2)
        assert bits[0:2] == "01"

    def test_result_overwrites_b_register(self):
        # 2 + 3 = 5 -> "101" across q2,q3,q4
        assert expected_result_bits(LAYOUT_2BIT, "add", 2, 3, 2) == "101"

    def test_carry_out_at_the_boundary(self):
        # 3 + 3 = 6 needs the carry qubit; low bit 0, then 1, then 1
        assert expected_result_bits(LAYOUT_2BIT, "add", 3, 3, 2) == "011"

    def test_no_carry_leaves_top_qubit_clear(self):
        # 1 + 2 = 3 fits in two bits, so q4 stays 0
        assert expected_result_bits(LAYOUT_2BIT, "add", 1, 2, 2) == "110"

    def test_result_too_wide_for_layout(self):
        narrow = RegisterLayout(num_qubits=4, a_qubits=(0, 1), b_qubits=(2, 3),
                                result_qubits=(2, 3))
        with pytest.raises(ArithmeticSpecError, match="result register"):
            expected_bitstring(narrow, "add", 3, 3, 2)


class TestValidateOperands:

    def test_accepts_in_range(self):
        validate_operands("add", 0, 3, 2)

    def test_rejects_out_of_range_naming_operand_and_width(self):
        with pytest.raises(ArithmeticSpecError) as exc:
            validate_operands("add", 4, 1, 2)
        message = str(exc.value)
        assert "operand A" in message
        assert "4" in message and "2 bits" in message

    def test_rejects_negative(self):
        with pytest.raises(ArithmeticSpecError, match="negative"):
            validate_operands("add", -1, 1, 2)

    def test_rejects_bad_width(self):
        with pytest.raises(ArithmeticSpecError, match="bit width"):
            validate_operands("add", 0, 0, 99)


class TestMarginalize:

    def test_collapses_onto_result_register(self):
        counts = {"01101": 8, "11101": 2}   # differ only outside the result
        assert marginalize(counts, LAYOUT_2BIT.result_qubits) == {"101": 10}

    def test_short_key_is_rejected(self):
        with pytest.raises(ArithmeticSpecError, match="shorter"):
            marginalize({"01": 1}, LAYOUT_2BIT.result_qubits)


class TestDescribe:

    def test_emits_the_full_attribute_contract(self):
        attrs = describe("add", 2, 3, 2, LAYOUT_2BIT, "cdkm", "qiskit")
        assert attrs["arithmetic.operation"] == "add"
        assert attrs["arithmetic.operand_a"] == "2"
        assert attrs["arithmetic.operand_b"] == "3"
        assert attrs["arithmetic.bit_width"] == "2"
        assert attrs["arithmetic.expected_result"] == "5"
        assert attrs["arithmetic.expected_result_bits"] == "101"
        assert attrs["arithmetic.result_qubits"] == "2,3,4"
        assert attrs["arithmetic.implementation"] == "cdkm"
        assert attrs["arithmetic.framework"] == "qiskit"
        assert attrs["sim.bit_order"] == "q0_left"

    def test_every_value_is_a_string(self):
        attrs = describe("add", 1, 1, 2, LAYOUT_2BIT, "cdkm", "qiskit")
        assert all(isinstance(v, str) for v in attrs.values())


class TestRegisterLayout:

    def test_rejects_index_outside_register(self):
        with pytest.raises(ArithmeticSpecError, match="outside register"):
            RegisterLayout(num_qubits=2, a_qubits=(0, 5))

    def test_rejects_overlapping_operands(self):
        with pytest.raises(ArithmeticSpecError, match="overlap"):
            RegisterLayout(num_qubits=3, a_qubits=(0, 1), b_qubits=(1, 2))

    def test_result_may_overlap_an_operand(self):
        # in-place adders write over b; that must stay legal
        RegisterLayout(num_qubits=3, a_qubits=(0,), b_qubits=(1,),
                       result_qubits=(1, 2))
