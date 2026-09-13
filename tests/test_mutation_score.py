"""
Tests for the survival-rate aggregator (MutationScoreReport) and its pure
scoring core. The aggregator closes the Layer-B mutation loop: it consumes the
per-case verdicts (assert.verdict) + mut.* bookkeeping and reports a survival
rate per operator, with controls excluded and their dissents surfaced.
"""

import json

from MutationScoreReport import MutationScoreReport, _classify, _score

from conftest import MockContext, MockFlowFile


# ---------------------------------------------------------------------------
# Pure scoring core
# ---------------------------------------------------------------------------

class TestClassify:

    def test_pass_survives(self):
        assert _classify("PASS") == "survived"
        assert _classify(" pass ") == "survived"

    def test_anything_else_killed(self):
        assert _classify("FAIL") == "killed"
        assert _classify("DISAGREE") == "killed"
        assert _classify("") == "killed"


class TestScore:

    def _rec(self, applied, verdict, op="", case_id="c"):
        return {"applied": applied, "operator": op, "verdict": verdict, "case_id": case_id}

    def test_survival_rate_excludes_controls(self):
        records = [
            self._rec(False, "DISAGREE", case_id="ctrl"),   # control dissent — NOT a kill
            self._rec(True, "PASS", "iterations.zero", "m0"),       # survived
            self._rec(True, "DISAGREE", "iterations.zero", "m1"),   # killed
            self._rec(True, "FAIL", "marked_state.bitflip", "m2"),  # killed
        ]
        s = _score(records)
        # 3 mutants, 1 survived -> survival rate 1/3
        assert s["overall"]["mutants"] == 3
        assert s["overall"]["survived"] == 1
        assert s["overall"]["killed"] == 2
        assert abs(s["overall"]["survival_rate"] - 1 / 3) < 1e-9
        assert abs(s["overall"]["mutation_score"] - 2 / 3) < 1e-9

    def test_control_dissent_reported_not_scored(self):
        records = [
            self._rec(False, "DISAGREE", case_id="ctrl-bad"),
            self._rec(False, "PASS", case_id="ctrl-ok"),
            self._rec(True, "PASS", "shots.shrink", "m0"),
        ]
        s = _score(records)
        assert s["controls"]["total"] == 2
        assert s["controls"]["dissenting"] == 1
        assert s["controls"]["dissent_case_ids"] == ["ctrl-bad"]
        # The dissenting control does not move the survival rate (1 mutant, survived).
        assert s["overall"]["survival_rate"] == 1.0

    def test_per_operator_breakdown(self):
        records = [
            self._rec(True, "PASS", "format.swap", "a"),
            self._rec(True, "PASS", "format.swap", "b"),
            self._rec(True, "DISAGREE", "format.swap", "c"),
            self._rec(True, "DISAGREE", "iterations.zero", "d"),
        ]
        s = _score(records)
        fs = s["by_operator"]["format.swap"]
        assert (fs["total"], fs["survived"], fs["killed"]) == (3, 2, 1)
        assert abs(fs["survival_rate"] - 2 / 3) < 1e-9
        assert s["by_operator"]["iterations.zero"]["survival_rate"] == 0.0

    def test_empty(self):
        s = _score([])
        assert s["overall"]["survival_rate"] == 0.0
        assert s["overall"]["mutants"] == 0
        assert s["controls"]["total"] == 0


# ---------------------------------------------------------------------------
# Processor: accumulation, attributes, relationships
# ---------------------------------------------------------------------------

class TestMutationScoreReport:

    def _feed(self, proc, ctx, attrs):
        return proc.transform(ctx, MockFlowFile(attributes=attrs))

    def test_accumulates_across_flowfiles(self, tmp_path):
        proc = MutationScoreReport()
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "t"})
        base = {"test.run_id": "R1"}
        # control + two mutants of one operator, one survives one killed
        self._feed(proc, ctx, {**base, "test.case_id": "c0", "mut.applied": "false",
                               "assert.verdict": "PASS"})
        self._feed(proc, ctx, {**base, "test.case_id": "c1", "mut.applied": "true",
                               "mut.operator": "iterations.offbyone", "assert.verdict": "PASS"})
        res = self._feed(proc, ctx, {**base, "test.case_id": "c2", "mut.applied": "true",
                                     "mut.operator": "iterations.offbyone", "assert.verdict": "DISAGREE"})
        assert res.attributes["mutation.mutants"] == "2"
        assert res.attributes["mutation.survived"] == "1"
        assert res.attributes["mutation.killed"] == "1"
        assert res.attributes["mutation.survival_rate"] == "0.5000"
        assert res.attributes["mutation.controls"] == "1"
        assert res.attributes["mutation.controls_dissenting"] == "0"
        # state + HTML written
        assert (tmp_path / "t-mutation-state.json").exists()
        assert (tmp_path / "t-mutation.html").exists()

    def test_dedup_by_case_id(self, tmp_path):
        proc = MutationScoreReport()
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "t"})
        a = {"test.run_id": "R", "test.case_id": "m", "mut.applied": "true",
             "mut.operator": "shots.shrink", "assert.verdict": "PASS"}
        self._feed(proc, ctx, a)
        res = self._feed(proc, ctx, a)   # same case id again
        assert res.attributes["mutation.mutants"] == "1"   # not double-counted

    def test_control_dissent_routes_to_its_relationship(self, tmp_path):
        proc = MutationScoreReport()
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "t"})
        res = self._feed(proc, ctx, {"test.run_id": "R", "test.case_id": "ctrl",
                                     "mut.applied": "false", "assert.verdict": "DISAGREE"})
        assert res.relationship == "control_dissent"
        assert res.attributes["mutation.controls_dissenting"] == "1"

    def test_mutant_routes_to_success(self, tmp_path):
        proc = MutationScoreReport()
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "t"})
        res = self._feed(proc, ctx, {"test.run_id": "R", "test.case_id": "m",
                                     "mut.applied": "true", "mut.operator": "format.swap",
                                     "assert.verdict": "DISAGREE"})
        assert res.relationship == "success"   # a killed mutant is the happy path

    def test_runs_are_isolated_by_run_id(self, tmp_path):
        proc = MutationScoreReport()
        ctx = MockContext(**{"Reports Directory": str(tmp_path), "Flow Name": "t"})
        self._feed(proc, ctx, {"test.run_id": "R1", "test.case_id": "m", "mut.applied": "true",
                               "mut.operator": "format.swap", "assert.verdict": "PASS"})
        res = self._feed(proc, ctx, {"test.run_id": "R2", "test.case_id": "m", "mut.applied": "true",
                                     "mut.operator": "format.swap", "assert.verdict": "DISAGREE"})
        # R2's report reflects only R2's single (killed) mutant.
        assert res.attributes["mutation.run_id"] == "R2"
        assert res.attributes["mutation.survival_rate"] == "0.0000"
        state = json.loads((tmp_path / "t-mutation-state.json").read_text())
        assert set(state) == {"R1", "R2"}
