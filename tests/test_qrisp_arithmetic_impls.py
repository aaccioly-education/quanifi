"""
Tests for the Implementation property added to QrispQuantumArithmetic.

The four named adders are in-place and share the register layout of the Qiskit
and Cirq arithmetic processors. The 'default' implementation keeps Qrisp's
high-level operator and must stay byte-identical to its pre-change behaviour.
"""

import hashlib

import pytest

from QrispQuantumArithmetic import QrispQuantumArithmetic

from conftest import MockContext, MockFlowFile

NAMED = ["cuccaro", "fourier", "gidney", "qcla"]

# sha256 of the QASM emitted by the pre-change default configuration
# (multiply, 3, 2, width 3, signed) - pins backwards compatibility.
BASELINE_DEFAULT_SHA = "d8f55c47ef3b5fbeba7fd5efa8c250bb31efc96640899c2a2f36a8a2e2812a80"


def _run(implementation="default", operation="add", a="1", b="2",
         bit_width="2", signed="false"):
    ctx = MockContext(**{
        "Operation": operation,
        "Operand A": a,
        "Operand B": b,
        "Bit Width": bit_width,
        "Signed": signed,
        "Implementation": implementation,
    })
    return QrispQuantumArithmetic().transform(ctx, MockFlowFile())


class TestBackwardsCompatibility:

    def test_default_config_is_byte_identical(self):
        r = _run(implementation="default", operation="multiply",
                 a="3", b="2", bit_width="3", signed="true")
        assert r.relationship == "success"
        assert hashlib.sha256(r.contents).hexdigest() == BASELINE_DEFAULT_SHA

    def test_default_keeps_its_original_attributes(self):
        r = _run(implementation="default", operation="multiply",
                 a="3", b="2", bit_width="3", signed="true")
        assert r.attributes["arithmetic.expected_result"] == "6"
        assert r.attributes["arithmetic.framework"] == "qrisp"
        assert r.attributes["arithmetic.result_size"]

    def test_default_does_not_emit_the_shared_layout(self):
        # out-of-place and possibly signed, so the shared contract does not apply
        r = _run(implementation="default", operation="multiply", signed="true",
                 a="3", b="2", bit_width="3")
        assert "arithmetic.result_qubits" not in r.attributes

    def test_default_still_supports_every_operation(self):
        for op, expected in (("add", "3"), ("subtract", "-1"), ("multiply", "2")):
            r = _run(implementation="default", operation=op, a="1", b="2",
                     bit_width="3", signed="true")
            assert r.relationship == "success"
            assert r.attributes["arithmetic.expected_result"] == expected


class TestNamedAdders:

    @pytest.mark.parametrize("impl", NAMED)
    def test_builds_and_reports_the_answer(self, impl):
        r = _run(implementation=impl, a="1", b="2")
        assert r.relationship == "success"
        assert r.attributes["arithmetic.expected_result"] == "3"
        assert r.attributes["arithmetic.implementation"] == impl

    @pytest.mark.parametrize("impl", NAMED)
    def test_shares_the_common_register_layout(self, impl):
        # a on 0..n-1, result in place over the widened B register at n..2n
        r = _run(implementation=impl, bit_width="2")
        assert r.attributes["arithmetic.result_qubits"] == "2,3,4"

    @pytest.mark.parametrize("impl", NAMED)
    def test_expected_bitstring_spans_the_register(self, impl):
        r = _run(implementation=impl)
        assert len(r.attributes["arithmetic.expected_bitstring"]) == \
            int(r.attributes["circuit.num_qubits"])

    @pytest.mark.parametrize("impl", NAMED)
    def test_carry_out_is_set_when_it_should_be(self, impl):
        r = _run(implementation=impl, a="3", b="3")
        assert r.attributes["arithmetic.expected_result"] == "6"
        assert r.attributes["arithmetic.expected_result_bits"] == "011"

    def test_implementations_differ_structurally(self):
        widths = {impl: int(_run(implementation=impl).attributes["circuit.num_qubits"])
                  for impl in NAMED}
        # fourier needs no ancilla; the carry-chain adders do
        assert len(set(widths.values())) > 1, widths
        assert widths["fourier"] < widths["cuccaro"]

    def test_all_named_adders_agree_on_the_answer(self):
        answers = {impl: _run(implementation=impl, a="3", b="2")
                   .attributes["arithmetic.expected_result"] for impl in NAMED}
        assert set(answers.values()) == {"5"}


class TestFailureRoutes:

    def test_unknown_implementation(self):
        r = _run(implementation="nonesuch")
        assert r.relationship == "failure"
        assert "unknown implementation" in r.attributes["arithmetic.error"]

    @pytest.mark.parametrize("op", ["subtract", "multiply"])
    def test_named_adder_rejects_non_add(self, op):
        r = _run(implementation="cuccaro", operation=op)
        assert r.relationship == "failure"
        assert "in-place adder" in r.attributes["arithmetic.error"]

    def test_named_adder_rejects_signed(self):
        r = _run(implementation="fourier", signed="true")
        assert r.relationship == "failure"
        assert "unsigned" in r.attributes["arithmetic.error"]
