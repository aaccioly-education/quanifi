"""Tests for QuantumCalibratedOracle.

The processor derives its agreement threshold from control replicates carried in
the same batch, rather than taking a configured constant. The regression that
matters is TestFixedThresholdWouldFail: at measured hardware noise levels a
fixed 0.1 flags every correct version as defective, while the calibrated
threshold does not.

Offline -- the oracle consumes counts, so no simulator or device is needed.
"""
import json
import random

from conftest import MockContext, MockFlowFile, result_to_flowfile

from QuantumCalibratedOracle import QuantumCalibratedOracle, score_batch


def noisy_ghz(n, shots, fidelity, rng):
    """GHZ-n counts under a depolarising + readout model, as a device produces."""
    counts = {}
    for _ in range(shots):
        if rng.random() < fidelity:
            state = "0" * n if rng.random() < 0.5 else "1" * n
        else:
            state = "".join(rng.choice("01") for _ in range(n))
        counts[state] = counts.get(state, 0) + 1
    return counts


def batch(n=8, shots=400, fidelity=0.6, replicates=6, seed=3, mutant_counts=None):
    rng = random.Random(seed)
    entries = [{"label": "ref-%d" % i, "kind": "null",
                "counts": noisy_ghz(n, shots, fidelity, rng)}
               for i in range(replicates)]
    entries += [{"label": fw, "kind": "control",
                 "counts": noisy_ghz(n, shots, fidelity, rng)}
                for fw in ("qiskit", "cirq", "qrisp")]
    if mutant_counts is not None:
        entries.append({"label": "cirq[parity_flip]", "kind": "parity_flip",
                        "counts": mutant_counts})
    return entries


def ctx(tmp_path, **overrides):
    props = {"Reports Directory": str(tmp_path / "reports"), "Flow Name": "test",
             "Raw Results Directory": str(tmp_path / "raw"),
             "Significance Level": "0.05", "Threshold Quantile": "0.95",
             "Minimum Replicates": "4", "Expected Support": ""}
    props.update(overrides)
    return MockContext(**props)


def run(entries, tmp_path, attrs=None, **overrides):
    flow = MockFlowFile(content=json.dumps({"entries": entries}).encode(),
                        attributes=attrs or {})
    return QuantumCalibratedOracle().transform(ctx(tmp_path, **overrides), flow)


# ---------------------------------------------------------------------------

class TestFixedThresholdWouldFail:
    """The reason this processor exists."""

    def test_calibrated_passes_where_fixed_0_1_would_not(self, tmp_path):
        """At hardware noise the controls sit far above 0.1 yet must agree.

        Measured null floors were 0.3309 (ibm_kingston) and 0.4115 (iqm_emerald)
        for correct code. A configured 0.1 would flag all three frameworks as
        defective; the calibrated threshold must not.
        """
        entries = batch(fidelity=0.55)
        result = run(entries, tmp_path)
        attrs = result_to_flowfile(result).getAttributes()
        assert result.relationship == "pass"
        assert attrs["oracle.false_alarms"] == "0"
        # the whole point: the floor is well above the manuscript's constant
        assert float(attrs["oracle.threshold"]) > 0.1
        assert float(attrs["oracle.null_median"]) > 0.1

    def test_threshold_tracks_the_noise_level(self, tmp_path):
        """A noisier batch must yield a higher threshold, not a fixed one."""
        clean = run(batch(fidelity=0.95, seed=1), tmp_path / "a")
        noisy = run(batch(fidelity=0.40, seed=1), tmp_path / "b")
        t_clean = float(result_to_flowfile(clean).getAttributes()["oracle.threshold"])
        t_noisy = float(result_to_flowfile(noisy).getAttributes()["oracle.threshold"])
        assert t_noisy > t_clean * 2


class TestTwoGateRule:
    def test_noise_floor_mutant_is_not_killed(self, tmp_path):
        """A 'mutant' that is really just another correct run must survive.

        Hellinger alone killed exactly this case on ibm_kingston (p=0.196 and
        p=0.710 cleared the threshold by 0.003), which is why the chi-squared
        gate is required.
        """
        rng = random.Random(99)
        entries = batch(mutant_counts=noisy_ghz(8, 400, 0.6, rng))
        attrs = result_to_flowfile(run(entries, tmp_path)).getAttributes()
        assert attrs["oracle.killed"] == "0"
        assert attrs["oracle.survived"] != "0"

    def test_disjoint_mutant_is_killed(self, tmp_path):
        """A parity-flipped GHZ shares no support with the controls."""
        entries = batch(mutant_counts={"10000000": 200, "01111111": 200})
        attrs = result_to_flowfile(run(entries, tmp_path)).getAttributes()
        assert int(attrs["oracle.killed"]) > 0
        assert attrs["oracle.survived"] == "0"
        assert attrs["oracle.mutation_score"] == "1.0000"

    def test_mutant_not_compared_against_its_own_framework(self, tmp_path):
        """cirq[parity_flip] is judged against qiskit and qrisp, not cirq.

        Comparing a mutant to its own control is a single-framework oracle;
        the N-version one compares across implementations.
        """
        entries = batch(mutant_counts={"10000000": 200, "01111111": 200})
        res = score_batch(entries)
        pairs = [r["comparison"] for r in res["rows"] if r["kind"] == "parity_flip"]
        assert all(not p.startswith("cirq vs") for p in pairs), pairs
        assert len(pairs) == 2


class TestGuards:
    def test_refuses_too_few_replicates(self, tmp_path):
        entries = batch(replicates=2)
        result = run(entries, tmp_path, **{"Minimum Replicates": "6"})
        attrs = result_to_flowfile(result).getAttributes()
        assert result.relationship == "failure"
        assert "Minimum Replicates" in attrs["oracle.error"]

    def test_malformed_payload_routes_to_failure(self, tmp_path):
        flow = MockFlowFile(content=b"{}", attributes={})
        result = QuantumCalibratedOracle().transform(ctx(tmp_path), flow)
        assert result.relationship == "failure"
        assert "oracle.error" in result_to_flowfile(result).getAttributes()

    def test_entry_missing_counts_routes_to_failure(self, tmp_path):
        flow = MockFlowFile(content=json.dumps(
            {"entries": [{"label": "a", "kind": "null"}]}).encode())
        result = QuantumCalibratedOracle().transform(ctx(tmp_path), flow)
        assert result.relationship == "failure"

    def test_control_disagreement_fails_the_batch(self, tmp_path):
        """If correct versions diverge, the oracle must not report PASS."""
        entries = batch(fidelity=0.95, seed=5)
        # replace one control with a disjoint distribution
        for e in entries:
            if e["kind"] == "control" and e["label"] == "cirq":
                e["counts"] = {"10101010": 400}
        result = run(entries, tmp_path)
        attrs = result_to_flowfile(result).getAttributes()
        assert result.relationship == "fail"
        assert attrs["assert.verdict"] == "FAIL"
        assert int(attrs["oracle.false_alarms"]) > 0

    def test_raw_counts_write_failure_routes_to_failure(self, tmp_path):
        not_a_directory = tmp_path / "blocked"
        not_a_directory.write_text("file")
        result = run(batch(), tmp_path,
                     **{"Raw Results Directory": str(not_a_directory)})
        attrs = result_to_flowfile(result).getAttributes()
        assert result.relationship == "failure"
        assert "persist raw batch counts" in attrs["oracle.error"]


class TestContract:
    def test_persists_replayable_raw_counts_before_scoring(self, tmp_path):
        entries = batch(replicates=2)
        result = run(
            entries, tmp_path,
            attrs={"batch.device": "iqm:emerald", "batch.job_id": "job/42"},
            **{"Minimum Replicates": "4"},
        )
        assert result.relationship == "failure"
        artifacts = list((tmp_path / "raw").glob("*.json"))
        assert len(artifacts) == 1
        saved = json.loads(artifacts[0].read_text())
        assert saved["schema"] == "quanifi.hardware-batch.v1"
        assert saved["batch"]["batch.job_id"] == "job/42"
        assert saved["entries"] == entries
        assert "/" not in artifacts[0].name

    def test_emits_attributes_and_report(self, tmp_path):
        entries = batch(mutant_counts={"10000000": 200, "01111111": 200})
        result = run(entries, tmp_path, attrs={"batch.device": "iqm:garnet",
                                               "batch.job_id": "abc123"})
        attrs = result_to_flowfile(result).getAttributes()
        assert attrs["report.type"] == "consensus"
        assert attrs["oracle.mode"] == "calibrated"
        assert attrs["batch.device"] == "iqm:garnet"
        assert attrs["batch.job_id"] == "abc123"
        assert attrs["oracle.replicates"] == "6"
        assert attrs["oracle.null_pairs"] == "15"
        raw_path = tmp_path / "raw" / attrs["oracle.raw_results_path"].split("/")[-1]
        assert raw_path.exists()
        report = tmp_path / "reports" / "test-calibrated-oracle.html"
        assert report.exists()
        assert "calibrated" in report.read_text().lower()

    def test_expected_support_failure(self, tmp_path):
        entries = batch()
        result = run(entries, tmp_path,
                     **{"Expected Support": "00000000,11111111,10101010"})
        attrs = result_to_flowfile(result).getAttributes()
        assert result.relationship == "fail"
        assert "support" in attrs["assert.reason"]
