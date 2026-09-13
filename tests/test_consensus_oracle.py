"""
Tests for the K-way majority oracle (QuantumConsensusOracle) and its pure
consensus core. The oracle generalises the 2-way comparison+assertion to K
framework branches and emits the assert.verdict contract MutationScoreReport
consumes.
"""

import json

from QuantumConsensusOracle import (
    QuantumConsensusOracle,
    _consensus,
    _canonical,
    _gt_match,
)

from conftest import MockContext, MockFlowFile


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestCanonical:

    def test_string_and_reverse_share_canonical(self):
        assert _canonical("001") == _canonical("100")

    def test_distinct_states_differ(self):
        assert _canonical("011") != _canonical("000")

    def test_palindrome_and_empty(self):
        assert _canonical("010") == "010"
        assert _canonical("") == ""


class TestGtMatch:

    def test_endian_agnostic(self):
        assert _gt_match("001", "100")
        assert _gt_match("11", "11")

    def test_absent_expected_is_vacuously_true(self):
        assert _gt_match("anything", "")

    def test_mismatch(self):
        assert not _gt_match("011", "000")


class TestConsensus:

    def _e(self, label, top):
        return {"label": label, "dist": {top: 1.0}}

    def test_all_agree_no_groundtruth_passes(self):
        r = _consensus([self._e("qiskit", "11"), self._e("cirq", "11"), self._e("qrisp", "11")])
        assert r["verdict"] == "PASS"
        assert r["dissenters"] == []
        assert r["majority_count"] == 3

    def test_single_dissenter_disagrees(self):
        r = _consensus([self._e("qiskit", "11"), self._e("cirq", "11"), self._e("qrisp", "00")])
        assert r["verdict"] == "DISAGREE"
        assert r["dissenters"] == ["qrisp"]
        assert r["majority_top"] == "11"

    def test_endian_difference_is_not_a_dissent(self):
        # Qiskit little-endian "100" vs Cirq MSB-first "001" of the same state.
        r = _consensus([self._e("qiskit", "100"), self._e("cirq", "001"), self._e("qrisp", "100")])
        assert r["verdict"] == "PASS"
        assert r["dissenters"] == []

    def test_agree_but_wrong_answer_fails_on_groundtruth(self):
        # Whole-case mutant: every branch makes the same mistake -> consensus can't
        # see it, ground truth does.
        r = _consensus(
            [self._e("qiskit", "10"), self._e("cirq", "10"), self._e("qrisp", "10")],
            expected="11", check_gt=True,
        )
        assert r["verdict"] == "FAIL"

    def test_agree_and_correct_passes(self):
        r = _consensus(
            [self._e("qiskit", "11"), self._e("cirq", "11"), self._e("qrisp", "11")],
            expected="11", check_gt=True,
        )
        assert r["verdict"] == "PASS"

    def test_three_way_split_disagrees(self):
        r = _consensus([self._e("a", "00"), self._e("b", "01"), self._e("c", "11")])
        assert r["verdict"] == "DISAGREE"
        assert len(r["dissenters"]) == 2   # two fall outside the chosen majority

    def test_empty(self):
        r = _consensus([])
        assert r["verdict"] == "DISAGREE"
        assert r["branches"] == 0


# ---------------------------------------------------------------------------
# Processor: buffering until K, then verdict
# ---------------------------------------------------------------------------

class TestQuantumConsensusOracle:

    def _ctx(self, tmp_path, **over):
        props = {
            "Reports Directory": str(tmp_path),
            "State Directory": str(tmp_path / "state"),
            "Flow Name": "t",
            "Expected Branches": "3",
            "Consensus Label": "${test.run_id}-${test.case_id}",
            "Branch Label": "${sim.framework}",
        }
        props.update(over)
        return MockContext(**props)

    def _ff(self, dist, **attrs):
        return MockFlowFile(content=json.dumps(dist).encode(), attributes=attrs)

    def _feed(self, proc, ctx, dist, **attrs):
        return proc.transform(ctx, self._ff(dist, **attrs))

    def test_waits_then_votes(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "c0"}
        r1 = self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "qiskit"})
        r2 = self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "cirq"})
        assert r1.relationship == "waiting" and r2.relationship == "waiting"
        assert r2.attributes["consensus.have"] == "2"
        r3 = self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "qrisp"})
        assert r3.relationship == "pass"
        assert r3.attributes["assert.verdict"] == "PASS"
        assert r3.attributes["consensus.branches"] == "3"
        assert r3.attributes["consensus.dissenter_count"] == "0"

    def test_dissenter_routes_to_fail_and_names_branch(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "c0"}
        self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "qiskit"})
        self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "cirq"})
        r = self._feed(proc, ctx, {"00": 100}, **base, **{"sim.framework": "qrisp"})
        assert r.relationship == "fail"
        assert r.attributes["assert.verdict"] == "DISAGREE"
        assert r.attributes["consensus.dissenters"] == "qrisp"

    def test_passes_mutation_bookkeeping_through(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "m0",
                "mut.applied": "true", "mut.operator": "iterations.offbyone"}
        self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "qiskit"})
        self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": "cirq"})
        r = self._feed(proc, ctx, {"00": 100}, **base, **{"sim.framework": "qrisp"})
        # mut.* survives so MutationScoreReport can score the mutant.
        assert r.attributes["mut.applied"] == "true"
        assert r.attributes["mut.operator"] == "iterations.offbyone"

    def test_groundtruth_fail_on_agreed_wrong_answer(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "c0", "test.expected": "11"}
        self._feed(proc, ctx, {"10": 100}, **base, **{"sim.framework": "qiskit"})
        self._feed(proc, ctx, {"10": 100}, **base, **{"sim.framework": "cirq"})
        r = self._feed(proc, ctx, {"10": 100}, **base, **{"sim.framework": "qrisp"})
        assert r.attributes["assert.verdict"] == "FAIL"
        assert r.relationship == "fail"

    def test_slot_cleared_after_vote(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "c0"}
        for fw in ("qiskit", "cirq", "qrisp"):
            last = self._feed(proc, ctx, {"11": 100}, **base, **{"sim.framework": fw})
        assert last.relationship == "pass"
        # Slot file removed; a fresh trio starts a new vote, not a 4th-branch error.
        import os
        state_files = os.listdir(tmp_path / "state")
        assert state_files == []

    def test_builder_and_sim_component_branch_naming(self, tmp_path):
        proc = QuantumConsensusOracle()
        ctx = self._ctx(tmp_path)
        base = {"test.run_id": "R", "test.case_id": "c0"}
        r1 = self._feed(proc, ctx, {"11": 100}, **base,
                        **{"builder.component": "QiskitGrover", "sim.component": "CirqSimulator", "sim.framework": "cirq"})
        r2 = self._feed(proc, ctx, {"11": 100}, **base,
                        **{"builder.component": "CirqGrover", "sim.component": "QrispSimulator", "sim.framework": "qrisp"})
        r3 = self._feed(proc, ctx, {"00": 100}, **base,
                        **{"builder.component": "PennylaneGrover", "sim.component": "BraketSimulator", "sim.framework": "braket"})
        assert r3.relationship == "fail"
        assert r3.attributes["assert.verdict"] == "DISAGREE"
        assert r3.attributes["consensus.dissenters"] == "PennylaneGrover and BraketSimulator"
        assert "PennylaneGrover and BraketSimulator" in r3.attributes["assert.reason"]
        assert r3.attributes["consensus.majority_count"] == "2"
        assert r3.attributes["consensus.branches"] == "3"

        # Check exported branches_json
        details = json.loads(r3.attributes["consensus.branches_json"])
        assert len(details) == 3
        assert details[0]["label"] == "QiskitGrover and CirqSimulator"
        assert details[0]["dissent"] is False
        assert details[2]["label"] == "PennylaneGrover and BraketSimulator"
        assert details[2]["dissent"] is True

    def test_duplicate_labels_majority_count_not_collapsed(self):
        # 6 branches: 4 vote '11', 2 vote '00'. Labels have duplicates.
        entries = [
            {"label": "branch_a", "dist": {"11": 1.0}},
            {"label": "branch_a", "dist": {"11": 1.0}},
            {"label": "branch_b", "dist": {"11": 1.0}},
            {"label": "branch_b", "dist": {"11": 1.0}},
            {"label": "branch_c", "dist": {"00": 1.0}},
            {"label": "branch_c", "dist": {"00": 1.0}},
        ]
        from QuantumConsensusOracle import _consensus
        r = _consensus(entries)
        assert r["verdict"] == "DISAGREE"
        assert r["majority_count"] == 4  # Must be 4 branches, NOT 2 unique labels!
        assert r["branches"] == 6

    def test_cross_combination_branch_naming_with_custom_or_legacy_label(self, tmp_path):
        """Cross combinations like QiskitGrover + CirqSimulator must resolve to
        compound component names even when Branch Label is legacy '${sim.framework}'
        or '${builder.framework}×${sim.framework}' or raw string."""
        for label_expr in ("${sim.framework}", "${builder.framework}×${sim.framework}", "custom"):
            proc = QuantumConsensusOracle()
            ctx = self._ctx(tmp_path / f"ctx_{abs(hash(label_expr))}", **{"Branch Label": label_expr})
            base = {"test.run_id": "R", "test.case_id": f"c_{abs(hash(label_expr))}"}
            # Feed 3 cross branches where builder and simulator frameworks differ
            r1 = self._feed(proc, ctx, {"11": 100}, **base,
                            **{"builder.component": "QiskitGrover", "builder.framework": "qiskit",
                               "sim.component": "CirqSimulator", "sim.framework": "cirq"})
            r2 = self._feed(proc, ctx, {"11": 100}, **base,
                            **{"builder.component": "CirqGrover", "builder.framework": "cirq",
                               "sim.component": "PennylaneSimulator", "sim.framework": "pennylane"})
            r3 = self._feed(proc, ctx, {"11": 100}, **base,
                            **{"builder.component": "PennylaneGrover", "builder.framework": "pennylane",
                               "sim.component": "QrispSimulator", "sim.framework": "qrisp"})
            assert r3.relationship == "pass"
            details = json.loads(r3.attributes["consensus.branches_json"])
            assert details[0]["label"] == "QiskitGrover and CirqSimulator"
            assert details[1]["label"] == "CirqGrover and PennylaneSimulator"
            assert details[2]["label"] == "PennylaneGrover and QrispSimulator"


