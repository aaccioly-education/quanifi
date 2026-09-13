import json
import pytest

from QrispDeutschJozsa import QrispDeutschJozsa
from QrispBernsteinVazirani import QrispBernsteinVazirani
from QrispSwapTest import QrispSwapTest
from conftest import MockContext, MockFlowFile


class TestQrispDeutschJozsa:
    def test_constant_zero(self):
        proc = QrispDeutschJozsa()
        ctx = MockContext(**{
            "Function Type": "constant_zero",
            "Num Qubits": "3",
            "Shots": "256",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["dj.function_type"] == "constant_zero"
        assert attrs["dj.verdict"] == "constant"
        assert attrs["dj.is_constant"] == "true"
        assert attrs["sim.top_result"] == "000"
        assert float(attrs["sim.top_probability"]) > 0.99
        assert attrs["sim.framework"] == "qrisp"
        assert attrs["report.type"] == "simulation"

    def test_constant_one(self):
        proc = QrispDeutschJozsa()
        ctx = MockContext(**{
            "Function Type": "constant_one",
            "Num Qubits": "2",
            "Shots": "256",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["dj.function_type"] == "constant_one"
        assert attrs["dj.verdict"] == "constant"
        assert attrs["dj.is_constant"] == "true"
        assert attrs["sim.top_result"] == "00"

    def test_balanced(self):
        proc = QrispDeutschJozsa()
        ctx = MockContext(**{
            "Function Type": "balanced",
            "Num Qubits": "3",
            "Balanced Mask": "101",
            "Shots": "256",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["dj.function_type"] == "balanced"
        assert attrs["dj.verdict"] == "balanced"
        assert attrs["dj.is_constant"] == "false"
        assert attrs["sim.top_result"] != "000"

    def test_invalid_num_qubits(self):
        proc = QrispDeutschJozsa()
        ctx = MockContext(**{
            "Function Type": "balanced",
            "Num Qubits": "0",
            "Shots": "100",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "failure"
        assert "dj.error" in res.attributes


class TestQrispBernsteinVazirani:
    def test_finds_secret_101(self):
        proc = QrispBernsteinVazirani()
        ctx = MockContext(**{
            "Secret Bitstring": "101",
            "Shots": "256",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["bv.secret_bitstring"] == "101"
        assert attrs["bv.found_bitstring"] == "101"
        assert attrs["bv.match"] == "true"
        assert attrs["sim.top_result"] == "101"
        assert float(attrs["sim.top_probability"]) > 0.99

    def test_finds_secret_1100(self):
        proc = QrispBernsteinVazirani()
        ctx = MockContext(**{
            "Secret Bitstring": "1100",
            "Shots": "256",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert attrs["bv.secret_bitstring"] == "1100"
        assert attrs["bv.found_bitstring"] == "1100"
        assert attrs["bv.match"] == "true"

    def test_invalid_secret(self):
        proc = QrispBernsteinVazirani()
        ctx = MockContext(**{
            "Secret Bitstring": "102a",
            "Shots": "100",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "failure"
        assert "bv.error" in res.attributes


class TestQrispSwapTest:
    def test_identical_states(self):
        # |0> and |0> have overlap 1.0
        proc = QrispSwapTest()
        ctx = MockContext(**{
            "State A": "zero",
            "State B": "zero",
            "Shots": "512",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert float(attrs["swap_test.overlap"]) > 0.95
        assert float(attrs["swap_test.p_zero"]) > 0.95

    def test_orthogonal_states(self):
        # |0> and |1> have overlap 0.0
        proc = QrispSwapTest()
        ctx = MockContext(**{
            "State A": "zero",
            "State B": "one",
            "Shots": "512",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert float(attrs["swap_test.overlap"]) < 0.1
        assert abs(float(attrs["swap_test.p_zero"]) - 0.5) < 0.1

    def test_superposition_states(self):
        # |+> and |+> have overlap 1.0
        proc = QrispSwapTest()
        ctx = MockContext(**{
            "State A": "plus",
            "State B": "plus",
            "Shots": "512",
        })
        res = proc.transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        attrs = res.attributes
        assert float(attrs["swap_test.overlap"]) > 0.95
