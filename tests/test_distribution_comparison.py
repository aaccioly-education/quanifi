"""
Tests for QuantumDistributionComparison's statistical layer: the pure-Python
chi-squared survival function, the two-sample homogeneity test (incl. cell
pooling), and the two-gate verdict (significance x practical effect size),
plus an end-to-end cross-simulator run (Aer vs Braket on the same circuit).
"""

import json

from QuantumDistributionComparison import (
    QuantumDistributionComparison,
    _chi2_sf,
    _chi2_two_sample,
)

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# Statistics primitives
# ---------------------------------------------------------------------------

class TestChi2Math:

    def test_sf_matches_critical_values(self):
        # Classic chi-squared table entries.
        assert abs(_chi2_sf(3.841, 1) - 0.05) < 1e-3
        assert abs(_chi2_sf(6.635, 1) - 0.01) < 1e-3
        assert abs(_chi2_sf(7.815, 3) - 0.05) < 1e-3

    def test_sf_bounds(self):
        assert _chi2_sf(0.0, 5) == 1.0
        assert _chi2_sf(1000.0, 1) < 1e-12

    def test_identical_counts_give_p_one(self):
        c = {"00": 500, "11": 500}
        chi2, dof, p, pooled = _chi2_two_sample(c, dict(c))
        assert chi2 == 0.0
        assert dof == 1
        assert p == 1.0

    def test_disjoint_counts_give_tiny_p(self):
        chi2, dof, p, pooled = _chi2_two_sample({"00": 1000}, {"11": 1000})
        assert p < 1e-10

    def test_small_cells_are_pooled(self):
        # The two singleton tail states have expected counts < 5 and must be
        # pooled into one bucket: 3 raw cells -> 2 tested cells -> dof 1.
        a = {"00": 990, "01": 6, "10": 4}
        b = {"00": 992, "01": 3, "10": 5}
        chi2, dof, p, pooled = _chi2_two_sample(a, b)
        assert pooled == 2
        assert dof == 1
        assert 0.0 <= p <= 1.0

    def test_not_applicable_returns_none(self):
        assert _chi2_two_sample({}, {"0": 10}) is None
        # Everything pools into a single bucket -> no test possible.
        assert _chi2_two_sample({"0": 2, "1": 1}, {"0": 1, "1": 2}) is None


# ---------------------------------------------------------------------------
# Verdict logic through the processor
# ---------------------------------------------------------------------------

class TestComparisonVerdict:

    def _run_pair(self, tmp_path, content_a, content_b, **extra_props):
        props = {
            "Reports Directory": str(tmp_path / "reports"),
            "State Directory":   str(tmp_path / "state"),
            "Flow Name":         "verdict-test",
            "Comparison Label":  "verdict-test",
            "Framework Label":   "",
        }
        props.update(extra_props)
        proc = QuantumDistributionComparison()
        first = proc.transform(MockContext(**props),
                               MockFlowFile(content=content_a))
        assert first.attributes.get("compare.status") == "waiting"
        return proc.transform(MockContext(**props),
                              MockFlowFile(content=content_b))

    def test_same_distribution_is_consistent(self, tmp_path):
        # Two samplings of the same Bell distribution, ordinary shot spread.
        a = json.dumps({"00": 520, "11": 504}).encode()
        b = json.dumps({"00": 498, "11": 526}).encode()
        r = self._run_pair(tmp_path, a, b)
        assert r.relationship == "success"
        assert r.attributes["compare.verdict"] == "consistent"
        assert float(r.attributes["compare.p_value"]) >= 0.01
        assert r.attributes["compare.dof"] == "1"

    def test_disjoint_distributions_disagree(self, tmp_path):
        a = json.dumps({"00": 1024}).encode()
        b = json.dumps({"11": 1024}).encode()
        r = self._run_pair(tmp_path, a, b)
        assert r.attributes["compare.verdict"] == "disagree"
        assert float(r.attributes["compare.p_value"]) < 0.01
        assert float(r.attributes["compare.hellinger_distance"]) > 0.9

    def test_significant_but_negligible(self, tmp_path):
        # Huge shots, tiny real difference: chi-squared rejects (p < alpha)
        # but Hellinger stays under the practical threshold.
        a = json.dumps({"0": 501500, "1": 498500}).encode()
        b = json.dumps({"0": 498500, "1": 501500}).encode()
        r = self._run_pair(tmp_path, a, b)
        assert float(r.attributes["compare.p_value"]) < 0.01
        assert float(r.attributes["compare.hellinger_distance"]) < 0.1
        assert r.attributes["compare.verdict"] == "negligible-difference"

    def test_probability_input_skips_test(self, tmp_path):
        # Float probabilities (analytic lane) carry no shot information.
        a = json.dumps({"00": 0.5, "11": 0.5}).encode()
        b = json.dumps({"00": 0.48, "11": 0.52}).encode()
        r = self._run_pair(tmp_path, a, b)
        assert r.relationship == "success"
        assert r.attributes["compare.verdict"] == "no-test"
        assert "compare.p_value" not in r.attributes
        # Effect sizes still reported.
        assert "compare.hellinger_distance" in r.attributes

    def test_json_content_carries_test_block(self, tmp_path):
        a = json.dumps({"00": 512, "11": 512}).encode()
        b = json.dumps({"00": 500, "11": 524}).encode()
        r = self._run_pair(tmp_path, a, b)
        body = json.loads(r.contents)
        block = body["chi_squared"]
        assert block["shots_a"] == 1024
        assert block["shots_b"] == 1024
        assert block["alpha"] == 0.01
        assert 0.0 <= block["p_value"] <= 1.0
        assert body["verdict"] == r.attributes["compare.verdict"]

    def test_alpha_property_is_respected(self, tmp_path):
        # A mild difference: significant at alpha=0.5 gate, not at 1e-12.
        a = json.dumps({"0": 560, "1": 464}).encode()
        b = json.dumps({"0": 500, "1": 524}).encode()
        strict = self._run_pair(tmp_path, a, b,
                                **{"Significance Level": "1e-12",
                                   "Comparison Label": "strict"})
        assert strict.attributes["compare.verdict"] == "consistent"


# ---------------------------------------------------------------------------
# End-to-end: two independent engines on the same circuit
# ---------------------------------------------------------------------------

class TestCrossSimulatorEndToEnd:

    def test_aer_vs_braket_consistent(self, tmp_path):
        from QiskitHadamardTransform import QiskitHadamardTransform
        from QiskitAerSimulator import QiskitAerSimulator
        from BraketSimulator import BraketSimulator

        circuit = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        aer = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "2048"}), result_to_flowfile(circuit))
        braket = BraketSimulator().transform(
            MockContext(**{"Shots": "2048"}), result_to_flowfile(circuit))
        assert aer.relationship == braket.relationship == "success"

        props = {
            "Reports Directory": str(tmp_path / "reports"),
            "State Directory":   str(tmp_path / "state"),
            "Flow Name":         "aer-vs-braket",
            "Comparison Label":  "aer-vs-braket",
            "Framework Label":   "${sim.framework}",
            # alpha low enough that a same-distribution pair virtually never
            # trips it, keeping this stochastic test stable.
            "Significance Level": "0.0001",
        }
        proc = QuantumDistributionComparison()
        proc.transform(MockContext(**props), result_to_flowfile(aer))
        r = proc.transform(MockContext(**props), result_to_flowfile(braket))

        assert r.relationship == "success"
        assert r.attributes["compare.framework_a"] == "qiskit"
        assert r.attributes["compare.framework_b"] == "braket"
        # Same ideal circuit on two independent engines: must never reach
        # "disagree" (H between two 2048-shot uniform samples is ~0.02 << 0.1).
        assert r.attributes["compare.verdict"] in ("consistent",
                                                   "negligible-difference")
        assert "compare.p_value" in r.attributes
