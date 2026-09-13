"""
Unit and integration tests for QrispQuantumArithmetic.

The circuit-building tests do NOT call a simulator and run in the fast suite.
One end-to-end pipeline test (QrispQuantumArithmetic -> QiskitAerSimulator)
is marked slow.
"""

import json

import pytest

from QrispQuantumArithmetic import QrispQuantumArithmetic

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _run(operation="multiply", a="3", b="2", bit_width="3", signed="true"):
    ctx = MockContext(**{
        "Operation": operation,
        "Operand A": a,
        "Operand B": b,
        "Bit Width": bit_width,
        "Signed": signed,
    })
    return QrispQuantumArithmetic().transform(ctx, MockFlowFile())


class TestQrispQuantumArithmetic:

    # --- Structural / contract ---

    def test_multiply_qasm2_contract(self):
        r = _run("multiply", "3", "2")
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.framework"] == "qrisp"
        assert "OPENQASM 2" in r.contents.decode()

    def test_circuit_metrics_emitted(self):
        r = _run("multiply", "3", "2")
        for key in ("circuit.num_qubits", "circuit.depth", "circuit.gate_count",
                    "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) > 1
        assert int(r.attributes["circuit.num_qubits"]) > 1

    def test_arithmetic_attributes(self):
        r = _run("multiply", "3", "2")
        assert r.attributes["arithmetic.operation"] == "multiply"
        assert r.attributes["arithmetic.operand_a"] == "3"
        assert r.attributes["arithmetic.operand_b"] == "2"
        assert r.attributes["arithmetic.bit_width"] == "3"
        assert r.attributes["arithmetic.signed"] == "true"
        assert r.attributes["arithmetic.framework"] == "qrisp"

    # --- Expected-result computation per operation ---

    def test_expected_result_multiply(self):
        assert _run("multiply", "3", "2").attributes["arithmetic.expected_result"] == "6"

    def test_expected_result_add(self):
        assert _run("add", "3", "2").attributes["arithmetic.expected_result"] == "5"

    def test_expected_result_subtract(self):
        assert _run("subtract", "3", "2").attributes["arithmetic.expected_result"] == "1"

    def test_signed_subtract_negative(self):
        r = _run("subtract", "2", "5", signed="true")
        assert r.relationship == "success"
        assert r.attributes["arithmetic.expected_result"] == "-3"

    # --- Error paths ---

    def test_operand_out_of_range_fails(self):
        # bit width 2 signed -> range [-4, 3]; 9 is out of range.
        r = _run("add", "9", "1", bit_width="2", signed="true")
        assert r.relationship == "failure"
        assert "arithmetic.error" in r.attributes

    def test_negative_operand_unsigned_fails(self):
        r = _run("add", "-1", "1", signed="false")
        assert r.relationship == "failure"
        assert "arithmetic.error" in r.attributes

    def test_unknown_operation_fails(self):
        r = _run("divide", "3", "2")
        assert r.relationship == "failure"
        assert "arithmetic.error" in r.attributes

    # --- Descriptors ---

    def test_property_descriptors(self):
        names = {d.name for d in QrispQuantumArithmetic().getPropertyDescriptors()}
        assert names == {"Operation", "Operand A", "Operand B", "Bit Width",
                         "Signed", "Implementation"}


@pytest.mark.slow
class TestQrispArithmeticPipeline:

    def test_multiply_pipeline_runs(self):
        """QrispQuantumArithmetic -> QiskitAerSimulator produces a valid distribution."""
        from QiskitAerSimulator import QiskitAerSimulator
        arith_r = _run("multiply", "3", "2")
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "512"}),
            result_to_flowfile(arith_r),
        )
        assert sim_r.relationship == "success"
        counts = json.loads(sim_r.contents)
        assert sum(counts.values()) > 0
