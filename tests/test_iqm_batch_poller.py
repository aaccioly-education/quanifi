"""The IQM batch poller must emit exactly what the expander consumes.

The gap this closes cost a real job: on 2026-08-24 an IQM arithmetic batch ran
on `garnet` and could not be scored, because the group paired the batch
submitter with the single-circuit `IQMJobPoller` and the expander rejected its
shape. These tests pin the contract in both directions.
"""
import json

import pytest

from QuantumIQMBatchPoller import QuantumIQMBatchPoller, merge_counts
from QuantumBatchResultExpander import QuantumBatchResultExpander
from conftest import MockContext, MockFlowFile


def manifest(n=3):
    return {"job_id": "j-1", "device": "garnet", "layout": [0, 4, 1],
            "padded_width": 9, "estimated_usage_seconds": 0.0,
            "entries": [
                {"label": "cdkm@%d+%d" % (i, i), "kind": "control",
                 "num_qubits": 6,
                 "attributes": {"arithmetic.operand_a": str(i),
                                "arithmetic.operand_b": str(i),
                                "arithmetic.expected_result_bits": "10",
                                "arithmetic.result_qubits": "0,1"}}
                for i in range(n)]}


def artifact(n=3):
    return [{"measurement_keys": ["m"], "counts": {"100000": 400 + i, "000000": 112}}
            for i in range(n)]


class TestMergeCounts:

    def test_ground_truth_survives_the_round_trip(self):
        merged = merge_counts(artifact(), manifest()["entries"])
        assert len(merged) == 3
        for i, row in enumerate(merged):
            assert row["label"] == "cdkm@%d+%d" % (i, i)
            assert row["kind"] == "control"
            assert row["num_qubits"] == 6
            # Without these the counts cannot be scored against the classical
            # answer and the lane degrades to a distributional oracle.
            assert row["attributes"]["arithmetic.expected_result_bits"] == "10"

    def test_a_length_mismatch_is_refused_not_zipped_short(self):
        # zip() would silently attach the wrong ground truth to every row after
        # the gap, which is worse than failing.
        with pytest.raises(ValueError, match="Refusing to align"):
            merge_counts(artifact(2), manifest(3)["entries"])

    def test_a_bare_object_is_accepted_as_a_one_circuit_batch(self):
        merged = merge_counts(artifact(1)[0], manifest(1)["entries"])
        assert len(merged) == 1

    @pytest.mark.parametrize("payload", [[], {}, None, "counts"])
    def test_an_empty_or_wrong_shaped_artifact_is_refused(self, payload):
        with pytest.raises(ValueError):
            merge_counts(payload, manifest(1)["entries"])

    def test_an_entry_without_counts_is_refused(self):
        with pytest.raises(ValueError, match="no counts"):
            merge_counts([{"measurement_keys": ["m"]}], manifest(1)["entries"])


class TestExpanderContract:
    """The whole point: the poller's output must feed the expander."""

    def test_the_expander_accepts_what_the_poller_emits(self):
        merged = merge_counts(artifact(), manifest()["entries"])
        out = {"entries": merged, "job_id": "j-1", "device": "garnet",
               "layout": [0, 4, 1], "padded_width": 9, "bit_order": "q0_left"}
        result = QuantumBatchResultExpander().transform(
            MockContext(**{"Source Bit Order": "q0_left"}),
            MockFlowFile(content=json.dumps(out).encode()))
        assert result.relationship == "success", result.attributes
        rows = json.loads(result.contents.decode())
        assert len(rows) == 3
        assert rows[0]["attributes"]["arithmetic.operand_a"] == "0"

    def test_the_single_circuit_poller_shape_would_not_have_worked(self):
        """Regression for the actual 2026-08-24 failure.

        IQMJobPoller emits merged counts with no 'entries' list. Pinning this
        keeps anyone from repointing the group back at it.
        """
        result = QuantumBatchResultExpander().transform(
            MockContext(**{"Source Bit Order": "q0_left"}),
            MockFlowFile(content=json.dumps({"counts": {"100": 512}}).encode()))
        assert result.relationship == "failure"


def test_relationships_are_declared():
    rels = {r.name for r in QuantumIQMBatchPoller().getRelationships()}
    assert rels == {"success", "pending", "failure"}
