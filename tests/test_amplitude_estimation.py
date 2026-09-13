"""Tests for the four Amplitude Estimation processors.

The Bernoulli problem (A = Ry(2*asin(sqrt(p)))) makes the amplitude to
estimate exactly the configured probability p, and puts every framework's
canonical estimate on the same grid sin^2(pi*y/2^m). Oracle values are
harvested from the qiskit-algorithms test suite (test_amplitude_estimators.py
runtime trace): p=0.2, m=2 canonical -> estimation=0.5 (grid), mle~0.2;
iterative (eps=0.01) -> ~0.2. For m=3 the grid point nearest p=0.2 is
sin^2(pi/8) = 0.14644661.
"""
import json
import math

import pytest

from conftest import MockContext, MockFlowFile

from QiskitAmplitudeEstimation import QiskitAmplitudeEstimation
from CirqAmplitudeEstimation import CirqAmplitudeEstimation
from PennylaneAmplitudeEstimation import PennylaneAmplitudeEstimation
from QrispAmplitudeEstimation import QrispAmplitudeEstimation

GRID_M3 = math.sin(math.pi / 8) ** 2  # 0.14644661: nearest m=3 grid point to 0.2


def assert_ae_contract(res, framework, method):
    """The shared ae.* / sim.* / report contract every AE processor must emit."""
    assert res.relationship == "success"
    a = res.attributes
    assert a["ae.framework"] == framework
    assert a["ae.method"] == method
    assert a["sim.framework"] == framework
    assert a["report.type"] == "simulation"
    assert abs(float(a["ae.probability"]) - 0.2) < 1e-9
    est = float(a["ae.estimate"])
    assert 0.0 <= est <= 1.0
    dist = json.loads(res.contents.decode("utf-8"))
    assert len(dist) >= 1
    return est


class TestQiskitAmplitudeEstimation:

    def test_canonical_matches_harvested_oracle(self):
        # harvested: p=0.2, m=2 -> grid estimate exactly 0.5, MLE ~ 0.2
        res = QiskitAmplitudeEstimation().transform(
            MockContext(**{"Probability": "0.2", "Evaluation Qubits": "2",
                           "Method": "canonical", "Shots": "4096"}),
            MockFlowFile())
        est = assert_ae_contract(res, "qiskit", "canonical")
        assert abs(est - 0.5) < 1e-9
        assert abs(float(res.attributes["ae.mle"]) - 0.2) < 0.05
        assert res.attributes["ae.eval_qubits"] == "2"

    def test_iterative_matches_harvested_oracle(self):
        res = QiskitAmplitudeEstimation().transform(
            MockContext(**{"Probability": "0.2", "Method": "iterative",
                           "Shots": "4096", "Epsilon Target": "0.01",
                           "Alpha": "0.05"}),
            MockFlowFile())
        est = assert_ae_contract(res, "qiskit", "iterative")
        assert abs(est - 0.2) < 0.05

    def test_invalid_probability_routes_to_failure(self):
        res = QiskitAmplitudeEstimation().transform(
            MockContext(**{"Probability": "1.5"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "ae.error" in res.attributes


class TestCirqAmplitudeEstimation:

    def test_estimate_on_m3_grid(self):
        res = CirqAmplitudeEstimation().transform(
            MockContext(**{"Probability": "0.2", "Evaluation Qubits": "3",
                           "Shots": "2048"}),
            MockFlowFile())
        est = assert_ae_contract(res, "cirq", "canonical")
        assert abs(est - GRID_M3) < 1e-6
        a = res.attributes
        assert a["ae.num_qubits"] == "4"
        assert len(a["sim.top_result"]) == 3
        assert a["sim.bit_order"] == "q0_left"

    def test_invalid_probability_routes_to_failure(self):
        res = CirqAmplitudeEstimation().transform(
            MockContext(**{"Probability": "-0.1"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "ae.error" in res.attributes


class TestPennylaneAmplitudeEstimation:

    def test_estimate_on_m3_grid_analytic(self):
        res = PennylaneAmplitudeEstimation().transform(
            MockContext(**{"Probability": "0.2", "Evaluation Qubits": "3"}),
            MockFlowFile())
        est = assert_ae_contract(res, "pennylane", "canonical")
        assert abs(est - GRID_M3) < 1e-9  # analytic, deterministic
        assert res.attributes["sim.top_result"] == "001"

    def test_invalid_probability_routes_to_failure(self):
        res = PennylaneAmplitudeEstimation().transform(
            MockContext(**{"Probability": "nope"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "ae.error" in res.attributes


class TestQrispAmplitudeEstimation:

    @pytest.mark.slow
    def test_iqae_estimate_converges(self):
        res = QrispAmplitudeEstimation().transform(
            MockContext(**{"Probability": "0.2", "Epsilon Target": "0.01",
                           "Alpha": "0.05", "Shots": "4096"}),
            MockFlowFile())
        est = assert_ae_contract(res, "qrisp", "iterative")
        assert abs(est - 0.2) < 0.05

    def test_invalid_probability_routes_to_failure(self):
        res = QrispAmplitudeEstimation().transform(
            MockContext(**{"Probability": "2"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "ae.error" in res.attributes


class TestCrossFrameworkAgreement:
    """The three canonical implementations share the sin^2(pi*y/2^m) grid, so
    on the same (p, m) they must produce the same estimate: the N-version
    pseudo-oracle for this component."""

    def test_canonical_estimates_agree_across_frameworks(self):
        ctx = {"Probability": "0.2", "Evaluation Qubits": "3", "Shots": "4096"}
        r_qiskit = QiskitAmplitudeEstimation().transform(
            MockContext(**dict(ctx, Method="canonical")), MockFlowFile())
        r_cirq = CirqAmplitudeEstimation().transform(
            MockContext(**ctx), MockFlowFile())
        r_pl = PennylaneAmplitudeEstimation().transform(
            MockContext(**ctx), MockFlowFile())
        ests = {float(r.attributes["ae.estimate"])
                for r in (r_qiskit, r_cirq, r_pl)}
        assert all(abs(e - GRID_M3) < 1e-6 for e in ests), ests
