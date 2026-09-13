"""
Turning a polled hardware batch back into scorable per-circuit results.

The two things that go wrong here are both silent. Ground truth dropped
somewhere in the round trip means the lane falls back to a distributional
oracle and looks like it worked; a bit order left in the provider's convention
means every success probability reads 0.0 and looks like a dead device. Both are
pinned below.
"""

import json

import pytest

from conftest import MockContext, MockFlowFile

from QuantumBatchResultExpander import QuantumBatchResultExpander, expand, reverse_keys
from QuantumSuccessProbabilityOracle import QuantumSuccessProbabilityOracle


# 3+3=6. Over the result qubits (2,3,4) the correct answer reads "011" in
# q0_left. Keys below are written MSB-first, as Qiskit returns them.
CORRECT = "011011"        # q0_left "110110" -> result bits "011"   PASS
WRONG_RESULT = "011111"   # q0_left "111110" -> result bits "111"   FAIL
GARBAGE_ONLY = "111011"   # differs only OUTSIDE the result register  PASS


def batch(bit_order="q0_right", with_attributes=True, entries=2,
          counts=None):
    rows = []
    for index in range(entries):
        entry = {"label": "impl-%d" % index, "kind": "control",
                 "num_qubits": 6,
                 # 3+3=6 -> result bits 011 in q0_left over 3 qubits;
                 # written here MSB-first as Qiskit would return it
                 "counts": dict(counts) if counts
                           else {CORRECT: 90, WRONG_RESULT: 10}}
        if with_attributes:
            entry["attributes"] = {
                "arithmetic.expected_result_bits": "011",
                "arithmetic.result_qubits": "2,3,4",
                "arithmetic.operand_a": "3", "arithmetic.operand_b": "3",
            }
        rows.append(entry)
    return {"job_id": "job-1", "device": "ibm_test", "bit_order": bit_order,
            "entries": rows}


def run(payload, **props):
    settings = {"Source Bit Order": "q0_right"}
    settings.update(props)
    return QuantumBatchResultExpander().transform(
        MockContext(**settings),
        MockFlowFile(content=json.dumps(payload).encode()))


class TestRowShape:

    def test_one_row_per_entry(self):
        result = run(batch(entries=3))
        assert result.relationship == "success"
        rows = json.loads(result.contents.decode())
        assert len(rows) == 3
        assert result.attributes["batch.expanded"] == "3"

    def test_marginalisation_ignores_non_result_qubits(self):
        """An implementation is not penalised for what it leaves in ancillas."""
        rows = json.loads(
            run(batch(counts={CORRECT: 50, GARBAGE_ONLY: 50})).contents.decode())
        flowfile = MockFlowFile(content=json.dumps(rows[0]["counts"]).encode(),
                                attributes=rows[0]["attributes"])
        scored = QuantumSuccessProbabilityOracle().transform(
            MockContext(**{"Expected Outcome": "", "Mode": "single",
                           "Confidence Level": "0.95", "Alpha": "0.05",
                           "Minimum Difference": "0.05",
                           "Comparison Label": "hw"}),
            flowfile)
        assert float(scored.attributes["oracle.success_probability"]) == 1.0

    def test_each_row_carries_counts_and_attributes(self):
        rows = json.loads(run(batch()).contents.decode())
        assert set(rows[0]) == {"counts", "attributes"}
        assert sum(rows[0]["counts"].values()) == 100

    def test_ground_truth_survives(self):
        rows = json.loads(run(batch()).contents.decode())
        assert rows[0]["attributes"]["arithmetic.expected_result_bits"] == "011"
        assert rows[0]["attributes"]["arithmetic.result_qubits"] == "2,3,4"

    def test_bookkeeping_is_added(self):
        rows = json.loads(run(batch()).contents.decode())
        attrs = rows[0]["attributes"]
        assert attrs["batch.entry_index"] == "0"
        assert attrs["batch.label"] == "impl-0"
        assert attrs["batch.job_id"] == "job-1"
        assert attrs["sim.shots"] == "100"


class TestBitOrder:
    """Getting this wrong does not raise. It reports 0% and looks like noise."""

    def test_reverse_keys_is_an_involution(self):
        counts = {"01100": 5, "11010": 7}
        assert reverse_keys(reverse_keys(counts)) == counts

    def test_q0_right_input_is_flipped(self):
        rows = expand(batch(bit_order="q0_right"))
        assert "110110" in rows[0]["counts"]

    def test_q0_left_input_is_left_alone(self):
        rows = expand(batch(bit_order="q0_left"))
        assert CORRECT in rows[0]["counts"]

    def test_output_always_advertises_q0_left(self):
        for order in ("q0_left", "q0_right"):
            rows = expand(batch(bit_order=order))
            assert rows[0]["attributes"]["sim.bit_order"] == "q0_left"

    def test_property_is_used_when_the_batch_is_silent(self):
        payload = batch()
        del payload["bit_order"]
        rows = json.loads(run(payload, **{"Source Bit Order": "q0_left"}).contents.decode())
        assert CORRECT in rows[0]["counts"]

    def test_batch_overrides_the_property(self):
        rows = json.loads(
            run(batch(bit_order="q0_left"), **{"Source Bit Order": "q0_right"})
            .contents.decode())
        assert CORRECT in rows[0]["counts"]


class TestFailures:

    def test_not_json(self):
        result = QuantumBatchResultExpander().transform(
            MockContext(**{"Source Bit Order": "q0_right"}),
            MockFlowFile(content=b"not json"))
        assert result.relationship == "failure"
        assert "not JSON" in result.attributes["batch.error"]

    def test_no_entries(self):
        result = run({"job_id": "x"})
        assert result.relationship == "failure"
        assert "entries" in result.attributes["batch.error"]

    def test_entry_without_counts_names_the_entry(self):
        payload = batch()
        del payload["entries"][1]["counts"]
        result = run(payload)
        assert result.relationship == "failure"
        assert "impl-1" in result.attributes["batch.error"]

    def test_a_list_is_not_a_batch(self):
        with pytest.raises(ValueError):
            expand([1, 2, 3])


class TestGroundTruthWarning:
    """A GHZ batch legitimately has none; say so rather than degrade silently."""

    def test_warns_when_nothing_is_scorable(self):
        result = run(batch(with_attributes=False))
        assert result.relationship == "success"
        assert result.attributes["batch.scorable"] == "0"
        assert "cannot score" in result.attributes["batch.warning"]

    def test_no_warning_when_scorable(self):
        result = run(batch())
        assert result.attributes["batch.scorable"] == "2"
        assert "batch.warning" not in result.attributes

    def test_grover_expected_counts_as_scorable(self):
        """A Grover batch's ground truth is grover.expected, not arithmetic.*.

        Without this, every Grover batch logs the "no entry carries ground
        truth" warning even though the marked state IS the ground truth --
        a false alarm on every run, which is what buries a true one.
        """
        payload = batch(with_attributes=False)
        for entry in payload["entries"]:
            entry["attributes"] = {"grover.expected": "10",
                                   "grover.builder": "qiskit"}
        result = run(payload)
        assert result.relationship == "success"
        assert result.attributes["batch.scorable"] == "2"
        assert "batch.warning" not in result.attributes


class TestOracleScoresAnExpandedRow:
    """The point of the whole processor: the oracle needs NO modification."""

    def _score(self, row):
        flowfile = MockFlowFile(content=json.dumps(row["counts"]).encode(),
                                attributes=row["attributes"])
        return QuantumSuccessProbabilityOracle().transform(
            MockContext(**{"Expected Outcome": "", "Mode": "single",
                           "Confidence Level": "0.95", "Alpha": "0.05",
                           "Minimum Difference": "0.05",
                           "Comparison Label": "hw"}),
            flowfile)

    def test_row_scores_against_the_classical_answer(self):
        rows = json.loads(run(batch()).contents.decode())
        scored = self._score(rows[0])
        assert scored.relationship == "success", scored.attributes.get("oracle.error")
        assert scored.attributes["oracle.expected_outcome"] == "011"
        assert float(scored.attributes["oracle.success_probability"]) == pytest.approx(0.90)

    def test_confidence_interval_brackets_the_estimate(self):
        rows = json.loads(run(batch()).contents.decode())
        scored = self._score(rows[0])
        lo = float(scored.attributes["oracle.ci_low"])
        hi = float(scored.attributes["oracle.ci_high"])
        assert lo < 0.90 < hi

    def test_unflipped_counts_would_have_scored_zero(self):
        """Why the bit-order normalisation is not cosmetic."""
        rows = json.loads(
            run(batch(bit_order="q0_left"), **{"Source Bit Order": "q0_left"})
            .contents.decode())
        # q0_left input left alone means the result register reads differently
        scored = self._score(rows[0])
        assert float(scored.attributes["oracle.success_probability"]) != pytest.approx(0.90)


class TestChunkProvenanceIsOptional:
    """IBM/IQM manifests (and an unchunked QI manifest) carry no chunk_index/
    chunk_count/batch_index -- the chunking added for Quantum Inspire's
    partitioned batches must not leak anything onto those rows."""

    def test_no_chunk_keys_means_no_chunk_attributes(self):
        rows = expand(batch())
        for row in rows:
            assert "batch.chunk_index" not in row["attributes"]
            assert "batch.chunk_count" not in row["attributes"]

    def test_batch_index_equals_entry_index_when_unchunked(self):
        rows = expand(batch(entries=3))
        for row in rows:
            assert row["attributes"]["batch.batch_index"] == row["attributes"]["batch.entry_index"]
