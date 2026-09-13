import json
import pytest
from unittest.mock import MagicMock

from QrispTruthTableSynthesis import QrispTruthTableSynthesis
from QrispTypeEncoder import QrispTypeEncoder
from QrispPhaseEstimation import QrispPhaseEstimation


def make_context(properties):
    context = MagicMock()
    def get_prop(prop_desc):
        val = properties.get(prop_desc.name, prop_desc.default_value)
        mock_val = MagicMock()
        mock_val.getValue.return_value = val
        mock_val.evaluateAttributeExpressions.return_value = mock_val
        return mock_val
    context.getProperty.side_effect = get_prop
    return context


def make_flowfile(content=b"", attributes=None):
    flowfile = MagicMock()
    flowfile.getContentsAsBytes.return_value = content
    attrs = attributes or {}
    flowfile.getAttribute.side_effect = lambda k: attrs.get(k)
    return flowfile


class TestQrispTruthTableSynthesis:

    def test_and_gate_gray_synthesis(self):
        proc = QrispTruthTableSynthesis()
        ctx = make_context({
            "Truth Table Spec": "[\"0001\"]",
            "Synthesis Method": "gray",
            "Output Format": "qasm2",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert res.attributes["circuit.format"] == "qasm2"
        assert int(res.attributes["circuit.num_qubits"]) == 3  # 2 inputs + 1 output
        assert int(res.attributes["synth.num_inputs"]) == 2
        assert int(res.attributes["synth.num_outputs"]) == 1
        assert "OPENQASM 2.0;" in res.contents.decode("utf-8")

    def test_half_adder_pprm_synthesis(self):
        proc = QrispTruthTableSynthesis()
        # Half adder: Carry = 0001, Sum = 0110
        ctx = make_context({
            "Truth Table Spec": json.dumps(["0001", "0110"]),
            "Synthesis Method": "pprm",
            "Output Format": "qasm2",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert int(res.attributes["circuit.num_qubits"]) == 4  # 2 inputs + 2 outputs
        assert int(res.attributes["synth.num_inputs"]) == 2
        assert int(res.attributes["synth.num_outputs"]) == 2
        assert res.contents is not None

    def test_invalid_truth_table_fails_gracefully(self):
        proc = QrispTruthTableSynthesis()
        ctx = make_context({
            "Truth Table Spec": "invalid_bits",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)
        assert res.relationship == "failure"
        assert "synth.error" in res.attributes


class TestQrispTypeEncoder:

    def test_encode_quantum_float_scalar(self):
        proc = QrispTypeEncoder()
        ctx = make_context({
            "Data Type": "QuantumFloat",
            "Value": "3.5",
            "Bit Width": "4",
            "Exponent": "-1",
            "Output Format": "qasm2",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert res.attributes["circuit.format"] == "qasm2"
        assert res.attributes["qtype.type"] == "QuantumFloat"
        assert res.attributes["qtype.value"] == "3.5"
        assert int(res.attributes["circuit.num_qubits"]) == 4
        assert "OPENQASM 2.0;" in res.contents.decode("utf-8")

    def test_encode_quantum_array(self):
        proc = QrispTypeEncoder()
        ctx = make_context({
            "Data Type": "QuantumArray",
            "Value": "[1.0, 2.0, 3.0]",
            "Bit Width": "4",
            "Exponent": "0",
            "Output Format": "qasm2",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert res.attributes["qtype.type"] == "QuantumArray"
        assert int(res.attributes["qtype.num_elements"]) == 3
        assert int(res.attributes["circuit.num_qubits"]) == 12  # 3 elements * 4 qubits
        assert "OPENQASM 2.0;" in res.contents.decode("utf-8")


class TestQrispPhaseEstimationCompose:

    def test_qpe_standalone_simulate(self):
        proc = QrispPhaseEstimation()
        ctx = make_context({
            "Phase Register Size": "3",
            "Builtin Unitary": "T",
            "Shots": "512",
            "Output Mode": "simulate",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert float(res.attributes["qpe.top_phase"]) == pytest.approx(0.125, abs=1e-3)
        assert res.attributes["qpe.mode"] == "standalone(T)"

    def test_qpe_compose_mode(self):
        proc = QrispPhaseEstimation()
        # QASM2 T-gate circuit
        qasm2_t = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
t q[0];
"""
        ctx = make_context({
            "Phase Register Size": "3",
            "Shots": "512",
            "Output Mode": "simulate",
        })
        ff = make_flowfile(qasm2_t.encode("utf-8"), attributes={"circuit.format": "qasm2"})
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert float(res.attributes["qpe.top_phase"]) == pytest.approx(0.125, abs=1e-3)
        assert res.attributes["qpe.mode"] == "compose"

    def test_qpe_circuit_output_mode(self):
        proc = QrispPhaseEstimation()
        ctx = make_context({
            "Phase Register Size": "3",
            "Builtin Unitary": "T",
            "Output Mode": "circuit",
        })
        ff = make_flowfile(b"")
        res = proc.transform(ctx, ff)

        assert res.relationship == "success"
        assert res.attributes["circuit.format"] == "qasm2"
        assert int(res.attributes["circuit.qpe_phase_register_size"]) == 3
        assert "OPENQASM 2.0;" in res.contents.decode("utf-8")
