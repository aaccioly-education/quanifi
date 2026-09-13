"""
Tests for QiskitQuantumArithmetic: the FlowFile contract, the register layout
read back from Qiskit's own adders, structural diversity between the three
implementations, and the failure routes.

Correctness over the full input space lives in test_arithmetic_equivalence.py;
here we pin the processor's behaviour.
"""

import pytest

from QiskitQuantumArithmetic import QiskitQuantumArithmetic

from conftest import MockContext, MockFlowFile


def _run(implementation="cdkm", a="2", b="3", bit_width="2", operation="add"):
    ctx = MockContext(**{
        "Implementation": implementation,
        "Operation": operation,
        "Operand A": a,
        "Operand B": b,
        "Bit Width": bit_width,
    })
    return QiskitQuantumArithmetic().transform(ctx, MockFlowFile())


class TestCircuitContract:

    def test_emits_qasm2_without_measurement(self):
        r = _run()
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.framework"] == "qiskit"
        qasm = r.contents.decode()
        assert "OPENQASM 2" in qasm
        assert "measure" not in qasm

    def test_circuit_metrics_present(self):
        r = _run()
        for key in ("circuit.num_qubits", "circuit.depth", "circuit.gate_count",
                    "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) > 1

    def test_bit_order_is_advertised(self):
        assert _run().attributes["sim.bit_order"] == "q0_left"


class TestArithmeticAttributes:

    def test_expected_result_is_classical_sum(self):
        r = _run(a="2", b="3")
        assert r.attributes["arithmetic.expected_result"] == "5"

    def test_full_attribute_contract(self):
        r = _run(implementation="vbe", a="1", b="2")
        attrs = r.attributes
        assert attrs["arithmetic.operation"] == "add"
        assert attrs["arithmetic.operand_a"] == "1"
        assert attrs["arithmetic.operand_b"] == "2"
        assert attrs["arithmetic.bit_width"] == "2"
        assert attrs["arithmetic.implementation"] == "vbe"
        assert attrs["arithmetic.framework"] == "qiskit"
        assert attrs["arithmetic.expected_bitstring"]
        assert attrs["arithmetic.result_qubits"]

    def test_expected_bitstring_spans_the_register(self):
        r = _run()
        assert len(r.attributes["arithmetic.expected_bitstring"]) == \
            int(r.attributes["circuit.num_qubits"])

    def test_result_register_is_b_plus_carry(self):
        # kind="half" lays out a on 0..n-1, b on n..2n-1, cout at 2n
        r = _run(bit_width="2")
        assert r.attributes["arithmetic.result_qubits"] == "2,3,4"

    def test_carry_out_is_set_when_it_should_be(self):
        # 3 + 3 = 6 -> result bits 011 in q0-left (LSB first)
        r = _run(a="3", b="3")
        assert r.attributes["arithmetic.expected_result"] == "6"
        assert r.attributes["arithmetic.expected_result_bits"] == "011"

    def test_no_carry_leaves_top_bit_clear(self):
        r = _run(a="1", b="2")
        assert r.attributes["arithmetic.expected_result_bits"] == "110"


class TestStructuralDiversity:
    """The three implementations must be genuinely different programs."""

    def test_two_qubit_counts_differ(self):
        counts = {
            impl: int(_run(implementation=impl).attributes["circuit.nonlocal_gates"])
            for impl in ("cdkm", "vbe", "draper")
        }
        assert len(set(counts.values())) > 1, counts

    def test_draper_is_the_cheapest(self):
        draper = int(_run(implementation="draper").attributes["circuit.nonlocal_gates"])
        cdkm = int(_run(implementation="cdkm").attributes["circuit.nonlocal_gates"])
        assert draper < cdkm

    def test_all_implementations_agree_on_the_answer(self):
        results = {
            impl: _run(implementation=impl, a="3", b="2").attributes["arithmetic.expected_result"]
            for impl in ("cdkm", "vbe", "draper")
        }
        assert set(results.values()) == {"5"}

    @pytest.mark.parametrize("impl", ["cdkm", "vbe", "draper"])
    def test_every_implementation_builds(self, impl):
        assert _run(implementation=impl).relationship == "success"


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
        assert "not supported" in r.attributes["arithmetic.error"]

    def test_non_integer_operand(self):
        r = _run(a="not-a-number")
        assert r.relationship == "failure"
        assert "non-integer" in r.attributes["arithmetic.error"]
