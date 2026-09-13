"""
Tests for QuantumSuccessProbabilityOracle.

Covers the statistics against published reference values, every verdict branch,
the absent-outcome case, marginalisation onto the result register, and the
missing-ground-truth failure.
"""

import json

import pytest

from QuantumSuccessProbabilityOracle import (
    QuantumSuccessProbabilityOracle,
    _norm_ppf,
    detectable_difference,
    required_shots,
    two_proportion_test,
    wilson_interval,
)

from conftest import MockContext, MockFlowFile


def _ff(counts, attributes=None):
    return MockFlowFile(content=json.dumps(counts).encode(),
                        attributes=attributes or {})


def _run(counts, attributes=None, **props):
    settings = {
        "Expected Outcome": "",
        "Mode": "single",
        "Confidence Level": "0.95",
        "Alpha": "0.05",
        "Minimum Difference": "0.05",
        "Comparison Label": "test",
        "State Directory": "/tmp/quanifi_success_oracle_test",
    }
    settings.update(props)
    return QuantumSuccessProbabilityOracle().transform(
        MockContext(**settings), _ff(counts, attributes))


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestNormalQuantile:

    @pytest.mark.parametrize("p,expected", [
        (0.975, 1.959964), (0.95, 1.644854), (0.8, 0.841621), (0.5, 0.0),
    ])
    def test_matches_published_values(self, p, expected):
        assert _norm_ppf(p) == pytest.approx(expected, abs=1e-5)

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            _norm_ppf(0.0)


class TestWilsonInterval:

    def test_known_reference(self):
        # 10 successes in 20 trials at 95%: the standard worked example
        lo, hi = wilson_interval(10, 20, 0.95)
        assert lo == pytest.approx(0.299, abs=0.002)
        assert hi == pytest.approx(0.701, abs=0.002)

    def test_stays_inside_the_unit_interval_at_the_boundary(self):
        lo, hi = wilson_interval(20, 20, 0.95)
        assert lo >= 0.0 and hi <= 1.0
        assert hi == pytest.approx(1.0, abs=1e-9)

    def test_zero_successes_has_a_positive_upper_bound(self):
        lo, hi = wilson_interval(0, 50, 0.95)
        assert lo == 0.0
        assert 0.0 < hi < 0.15

    def test_narrows_as_shots_grow(self):
        wide = wilson_interval(50, 100)
        narrow = wilson_interval(500, 1000)
        assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])

    def test_empty_sample_is_uninformative(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)


class TestTwoProportionTest:

    def test_identical_proportions_do_not_reject(self):
        z, p = two_proportion_test(80, 100, 80, 100)
        assert z == pytest.approx(0.0, abs=1e-9)
        assert p == pytest.approx(1.0, abs=1e-9)

    def test_large_difference_rejects(self):
        z, p = two_proportion_test(90, 100, 50, 100)
        assert p < 0.001

    def test_small_difference_at_small_n_does_not_reject(self):
        z, p = two_proportion_test(52, 100, 48, 100)
        assert p > 0.05

    def test_empty_arm_yields_no_test(self):
        assert two_proportion_test(0, 0, 5, 10) == (None, None)


class TestSampleSizing:
    """Reference values from design D5."""

    @pytest.mark.parametrize("p_ref,min_diff,expected", [
        (0.90, 0.10, 201), (0.80, 0.15, 140), (0.60, 0.15, 174),
    ])
    def test_required_shots_matches_design(self, p_ref, min_diff, expected):
        assert required_shots(p_ref, min_diff, 0.05, 0.8) == expected

    def test_more_power_needs_more_shots(self):
        assert required_shots(0.9, 0.1, 0.05, 0.95) > required_shots(0.9, 0.1, 0.05, 0.8)

    def test_smaller_effect_needs_more_shots(self):
        assert required_shots(0.9, 0.02, 0.05, 0.8) > required_shots(0.9, 0.10, 0.05, 0.8)

    def test_detectable_difference_inverts_required_shots(self):
        n = required_shots(0.9, 0.1, 0.05, 0.8)
        assert detectable_difference(n, 0.9, 0.05, 0.8) == pytest.approx(0.1, abs=0.01)

    def test_rejects_impossible_difference(self):
        with pytest.raises(ValueError):
            required_shots(0.9, 0.0)


# ---------------------------------------------------------------------------
# Single-run scoring
# ---------------------------------------------------------------------------

class TestSingleMode:

    def test_scores_against_the_property(self):
        r = _run({"101": 90, "100": 10}, **{"Expected Outcome": "101"})
        assert r.relationship == "success"
        assert r.attributes["oracle.successes"] == "90"
        assert r.attributes["oracle.shots"] == "100"
        assert float(r.attributes["oracle.success_probability"]) == pytest.approx(0.9)

    def test_reads_ground_truth_from_the_arithmetic_contract(self):
        r = _run({"101": 80, "011": 20},
                 {"arithmetic.expected_result_bits": "101"})
        assert r.attributes["oracle.expected_outcome"] == "101"
        assert float(r.attributes["oracle.success_probability"]) == pytest.approx(0.8)

    def test_marginalises_onto_the_result_register(self):
        # full-register keys differing only outside the result qubits
        counts = {"01101": 60, "11101": 30, "01011": 10}
        attrs = {"arithmetic.expected_result_bits": "101",
                 "arithmetic.result_qubits": "2,3,4"}
        r = _run(counts, attrs)
        assert r.attributes["oracle.successes"] == "90"
        assert float(r.attributes["oracle.success_probability"]) == pytest.approx(0.9)

    def test_absent_outcome_scores_zero_without_failing(self):
        r = _run({"000": 100}, **{"Expected Outcome": "111"})
        assert r.relationship == "success"
        assert r.attributes["oracle.success_probability"] == "0.000000"
        assert float(r.attributes["oracle.ci_high"]) > 0.0

    def test_emits_a_confidence_interval(self):
        r = _run({"101": 90, "100": 10}, **{"Expected Outcome": "101"})
        lo = float(r.attributes["oracle.ci_low"])
        hi = float(r.attributes["oracle.ci_high"])
        assert 0.0 <= lo < 0.9 < hi <= 1.0

    def test_reports_what_the_shot_count_could_detect(self):
        r = _run({"101": 90, "100": 10}, **{"Expected Outcome": "101"})
        assert 0.0 < float(r.attributes["oracle.detectable_difference"]) < 1.0

    def test_record_body_is_json(self):
        r = _run({"101": 90, "100": 10}, **{"Expected Outcome": "101"})
        body = json.loads(r.contents)
        assert body["successes"] == 90 and body["shots"] == 100


class TestFailureRoutes:

    def test_missing_ground_truth(self):
        r = _run({"101": 10})
        assert r.relationship == "failure"
        assert "no ground truth" in r.attributes["oracle.error"]

    def test_non_json_content(self):
        ff = MockFlowFile(content=b"not json", attributes={})
        r = QuantumSuccessProbabilityOracle().transform(
            MockContext(**{"Expected Outcome": "1", "Mode": "single",
                           "Confidence Level": "0.95", "Alpha": "0.05",
                           "Minimum Difference": "0.05",
                           "Comparison Label": "x", "State Directory": "/tmp/q"}), ff)
        assert r.relationship == "failure"
        assert "counts JSON" in r.attributes["oracle.error"]

    def test_analytic_distribution_is_rejected(self):
        r = _run({"101": 0.9, "100": 0.1}, **{"Expected Outcome": "101"})
        assert r.relationship == "failure"
        assert "integers" in r.attributes["oracle.error"]

    def test_empty_counts(self):
        r = _run({}, **{"Expected Outcome": "101"})
        assert r.relationship == "failure"


# ---------------------------------------------------------------------------
# Paired verdicts
# ---------------------------------------------------------------------------

class TestPairedMode:

    def _pair(self, counts_a, counts_b, tmp_path, label="pair", **props):
        settings = {"Expected Outcome": "1", "Mode": "paired",
                    "Confidence Level": "0.95", "Alpha": "0.05",
                    "Minimum Difference": "0.05", "Comparison Label": label,
                    "State Directory": str(tmp_path)}
        settings.update(props)
        proc = QuantumSuccessProbabilityOracle()
        first = proc.transform(MockContext(**settings), _ff(counts_a))
        second = proc.transform(MockContext(**settings), _ff(counts_b))
        return first, second

    def test_first_run_is_held(self, tmp_path):
        first, _ = self._pair({"1": 90, "0": 10}, {"1": 90, "0": 10}, tmp_path)
        assert first.attributes["oracle.verdict"] == "pending"

    def test_two_correct_runs_agree(self, tmp_path):
        _, second = self._pair({"1": 90, "0": 10}, {"1": 88, "0": 12}, tmp_path)
        assert second.relationship == "success"
        assert second.attributes["oracle.verdict"] == "agree"
        assert float(second.attributes["oracle.p_value"]) >= 0.05

    def test_degraded_run_diverges(self, tmp_path):
        _, second = self._pair({"1": 900, "0": 100}, {"1": 500, "0": 500}, tmp_path)
        assert second.attributes["oracle.verdict"] == "diverge"
        assert float(second.attributes["oracle.p_value"]) < 0.05
        assert float(second.attributes["oracle.difference"]) > 0.05

    def test_significant_but_negligible_difference(self, tmp_path):
        # huge n makes a 2-point gap significant, but it is under Minimum Difference
        _, second = self._pair({"1": 50000, "0": 50000},
                               {"1": 48000, "0": 52000}, tmp_path)
        assert float(second.attributes["oracle.p_value"]) < 0.05
        assert second.attributes["oracle.verdict"] == "negligible-difference"

    def test_verdict_is_recomputable_from_the_record(self, tmp_path):
        _, second = self._pair({"1": 900, "0": 100}, {"1": 500, "0": 500}, tmp_path)
        body = json.loads(second.contents)
        z, p = two_proportion_test(body["entry_a"]["successes"], body["entry_a"]["shots"],
                                   body["entry_b"]["successes"], body["entry_b"]["shots"])
        assert p == pytest.approx(body["p_value"], abs=1e-8)
        recomputed = ("agree" if p >= body["alpha"]
                      else "negligible-difference" if body["difference"] < body["min_difference"]
                      else "diverge")
        assert recomputed == body["verdict"]

    def test_slot_is_cleared_between_pairs(self, tmp_path):
        self._pair({"1": 90, "0": 10}, {"1": 90, "0": 10}, tmp_path, label="reuse")
        first, _ = self._pair({"1": 90, "0": 10}, {"1": 90, "0": 10},
                              tmp_path, label="reuse")
        assert first.attributes["oracle.verdict"] == "pending"
