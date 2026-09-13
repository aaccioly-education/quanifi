"""
Tests for the data-driven test-case generator (QuantumTestCaseSource) and for
the externalized (attribute-driven) Grover parameters that make a single test
table able to drive every framework branch identically.

These tests double as the regression guard for the conftest Expression Language
evaluator: the externalization is only meaningful if `${attr}` actually resolves
against the FlowFile attributes the test-case source emits.
"""

import json

import random

from QuantumTestCaseSource import (
    QuantumTestCaseSource,
    _expand_matrix,
    _MUTATION_OPERATORS,
    _derive_seed,
)

from conftest import MockContext, MockFlowFile, evaluate_el


# ---------------------------------------------------------------------------
# The EL evaluator the whole approach rests on
# ---------------------------------------------------------------------------

class TestExpressionLanguage:

    def test_plain_value_passthrough(self):
        assert evaluate_el("qasm3", {"x": "1"}) == "qasm3"

    def test_simple_attribute(self):
        assert evaluate_el("${grover.num_iterations}", {"grover.num_iterations": "3"}) == "3"

    def test_absent_attribute_is_empty(self):
        assert evaluate_el("${missing}", {}) == ""

    def test_replace_empty_fallback(self):
        assert evaluate_el("${n:replaceEmpty('1')}", {}) == "1"
        assert evaluate_el("${n:replaceEmpty('1')}", {"n": "5"}) == "5"

    def test_non_string_untouched(self):
        assert evaluate_el(None, {}) is None


# ---------------------------------------------------------------------------
# _expand_matrix
# ---------------------------------------------------------------------------

class TestExpandMatrix:

    def test_axes_cartesian_product(self):
        rows, mode = _expand_matrix({
            "grover.marked_state": ["00", "11", "000"],
            "grover.num_iterations": ["1", "2"],
        })
        assert mode == "axes"
        assert len(rows) == 6
        # Order preserved: first axis is the outer loop.
        assert rows[0] == {"grover.marked_state": "00", "grover.num_iterations": "1"}
        assert rows[1] == {"grover.marked_state": "00", "grover.num_iterations": "2"}

    def test_explicit_table_verbatim(self):
        table = [
            {"grover.marked_state": "11", "test.expected": "11"},
            {"grover.marked_state": "000", "test.expected": "000"},
        ]
        rows, mode = _expand_matrix(table)
        assert mode == "table"
        assert rows == table

    def test_empty_axes(self):
        rows, mode = _expand_matrix({})
        assert rows == [] and mode == "axes"

    def test_bad_axis_raises(self):
        try:
            _expand_matrix({"x": "not-a-list"})
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_scalar_spec_raises(self):
        try:
            _expand_matrix("nope")
            assert False, "expected ValueError"
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# QuantumTestCaseSource processor
# ---------------------------------------------------------------------------

class TestQuantumTestCaseSource:

    def _run(self, matrix, **props):
        ctx = MockContext(**{"Test Matrix": json.dumps(matrix), **props})
        return QuantumTestCaseSource().transform(ctx, MockFlowFile())

    def test_axes_emits_rows_with_bookkeeping(self):
        res = self._run({"grover.marked_state": ["00", "11"], "grover.num_iterations": ["1"]})
        assert res.relationship == "success"
        assert res.attributes["testsource.count"] == "2"
        assert res.attributes["testsource.mode"] == "axes"
        cases = json.loads(res.contents.decode("utf-8"))
        assert [c["grover.marked_state"] for c in cases] == ["00", "11"]
        # Every case gets a stable id and a shared run id.
        assert cases[0]["test.case_id"] == "case-000"
        assert cases[1]["test.case_id"] == "case-001"
        run_ids = {c["test.run_id"] for c in cases}
        assert len(run_ids) == 1
        assert run_ids == {res.attributes["testsource.run_id"]}

    def test_partition_and_prefix_and_run_id(self):
        res = self._run(
            {"grover.marked_state": ["0000"]},
            **{"Case ID Prefix": "boundary", "Partition Label": "all-zeros", "Run ID": "R1"},
        )
        case = json.loads(res.contents.decode("utf-8"))[0]
        assert case["test.case_id"] == "boundary-000"
        assert case["test.partition"] == "all-zeros"
        assert case["test.run_id"] == "R1"
        assert res.attributes["testsource.run_id"] == "R1"

    def test_explicit_row_keeps_its_own_bookkeeping(self):
        res = self._run(
            [{"grover.marked_state": "11", "test.case_id": "hand-picked",
              "test.partition": "mixed", "test.expected": "11"}],
            **{"Partition Label": "ignored"},
        )
        case = json.loads(res.contents.decode("utf-8"))[0]
        assert case["test.case_id"] == "hand-picked"   # not overwritten
        assert case["test.partition"] == "mixed"        # row wins over property
        assert case["test.expected"] == "11"

    def test_bad_json_fails(self):
        ctx = MockContext(**{"Test Matrix": "{not json"})
        res = QuantumTestCaseSource().transform(ctx, MockFlowFile())
        assert res.relationship == "failure"
        assert "testsource.error" in res.attributes

    def test_bad_axis_fails_gracefully(self):
        res = self._run({"grover.num_iterations": "1"})   # value not a list
        assert res.relationship == "failure"
        assert "testsource.error" in res.attributes


# ---------------------------------------------------------------------------
# Layer B mutation operators (pure functions)
# ---------------------------------------------------------------------------

class TestMutationOperators:

    def _fn(self, name):
        return _MUTATION_OPERATORS[name][1]

    def test_bitflip_changes_exactly_one_char(self):
        fn = self._fn("marked_state.bitflip")
        out = fn("0000", random.Random(1))
        assert len(out) == 4
        assert sum(a != b for a, b in zip("0000", out)) == 1
        assert set(out) <= {"0", "1"}

    def test_bitflip_empty_inapplicable(self):
        assert self._fn("marked_state.bitflip")("", random.Random(1)) is None

    def test_lenshift_changes_length_by_one(self):
        fn = self._fn("marked_state.lenshift")
        assert abs(len(fn("0101", random.Random(3))) - 4) == 1

    def test_iterations_offbyone(self):
        assert self._fn("iterations.offbyone")("3", random.Random(0)) == "4"

    def test_iterations_offbyone_non_int_inapplicable(self):
        assert self._fn("iterations.offbyone")("abc", random.Random(0)) is None

    def test_iterations_zero(self):
        assert self._fn("iterations.zero")("5", random.Random(0)) == "0"

    def test_shots_shrink(self):
        assert self._fn("shots.shrink")("1024", random.Random(0)) == "8"

    def test_format_swap_roundtrips(self):
        fn = self._fn("format.swap")
        assert fn("qasm2", random.Random(0)) == "qasm3"
        assert fn("qasm3", random.Random(0)) == "qasm2"

    def test_barriers_toggle(self):
        fn = self._fn("barriers.toggle")
        assert fn("true", random.Random(0)) == "false"
        assert fn("false", random.Random(0)) == "true"

    def test_noise_inject(self):
        fn = self._fn("noise.inject")
        assert fn("none", random.Random(0)) == "depolarizing"
        assert fn("depolarizing", random.Random(0)) == "none"

    def test_derive_seed_is_deterministic_and_distinct(self):
        assert _derive_seed(42, 0) == _derive_seed(42, 0)
        assert _derive_seed(42, 0) != _derive_seed(42, 1)


# ---------------------------------------------------------------------------
# Mutation pass on the processor
# ---------------------------------------------------------------------------

class TestMutationPass:

    def _run(self, matrix, **props):
        ctx = MockContext(**{"Test Matrix": json.dumps(matrix), **props})
        res = QuantumTestCaseSource().transform(ctx, MockFlowFile())
        return res

    def _cases(self, res):
        return json.loads(res.contents.decode("utf-8"))

    def test_no_operators_is_legacy_passthrough(self):
        # No mut.* fields, no testsource.mutated=true effect on the rows.
        res = self._run({"grover.marked_state": ["00", "11"]})
        cases = self._cases(res)
        assert res.attributes["testsource.mutated"] == "false"
        assert all("mut.applied" not in c for c in cases)
        assert [c["test.case_id"] for c in cases] == ["case-000", "case-001"]

    def test_one_control_plus_one_mutant_per_operator(self):
        res = self._run(
            {"grover.marked_state": ["00"], "grover.num_iterations": ["1"]},
            **{"Mutation Operators": "marked_state.bitflip, iterations.offbyone"},
        )
        cases = self._cases(res)
        # 1 base row -> 1 control + 2 mutants
        assert res.attributes["testsource.controls"] == "1"
        assert res.attributes["testsource.mutants"] == "2"
        control = [c for c in cases if c["mut.applied"] == "false"][0]
        mutants = [c for c in cases if c["mut.applied"] == "true"]
        assert control["grover.marked_state"] == "00"          # untouched
        ops = {m["mut.operator"] for m in mutants}
        assert ops == {"marked_state.bitflip", "iterations.offbyone"}
        # bookkeeping is complete and links back to the control
        for m in mutants:
            assert m["mut.base_case_id"] == control["test.case_id"]
            assert m["mut.target_attr"] and m["mut.original_value"] and m["mut.seed"]

    def test_mutant_actually_differs_from_original(self):
        res = self._run(
            {"grover.num_iterations": ["3"]},
            **{"Mutation Operators": "iterations.offbyone"},
        )
        m = [c for c in self._cases(res) if c["mut.applied"] == "true"][0]
        assert m["grover.num_iterations"] == "4"
        assert m["mut.original_value"] == "3"

    def test_seed_makes_mutation_reproducible(self):
        kw = {"Mutation Operators": "marked_state.bitflip", "Mutation Seed": "777",
              "Mutants Per Row": "1"}
        a = self._run({"grover.marked_state": ["00000"]}, **kw)
        b = self._run({"grover.marked_state": ["00000"]}, **kw)
        ma = [c for c in self._cases(a) if c["mut.applied"] == "true"][0]
        mb = [c for c in self._cases(b) if c["mut.applied"] == "true"][0]
        assert ma["grover.marked_state"] == mb["grover.marked_state"]
        assert ma["mut.seed"] == mb["mut.seed"]

    def test_mutants_per_row_cycles_operators(self):
        res = self._run(
            {"grover.marked_state": ["000"]},
            **{"Mutation Operators": "marked_state.bitflip", "Mutants Per Row": "3"},
        )
        mutants = [c for c in self._cases(res) if c["mut.applied"] == "true"]
        assert len(mutants) == 3
        # distinct seeds -> can hit different positions
        assert len({m["mut.seed"] for m in mutants}) == 3

    def test_zero_mutants_emits_controls_only(self):
        res = self._run(
            {"grover.marked_state": ["00", "11"]},
            **{"Mutation Operators": "marked_state.bitflip", "Mutants Per Row": "0"},
        )
        cases = self._cases(res)
        assert res.attributes["testsource.mutants"] == "0"
        assert res.attributes["testsource.controls"] == "2"
        assert all(c["mut.applied"] == "false" for c in cases)

    def test_inapplicable_operator_is_skipped(self):
        # Row has no grover.num_iterations, so iterations.offbyone produces nothing.
        res = self._run(
            {"grover.marked_state": ["00"]},
            **{"Mutation Operators": "iterations.offbyone"},
        )
        assert res.relationship == "success"
        assert res.attributes["testsource.mutants"] == "0"
        assert res.attributes["testsource.controls"] == "1"

    def test_unknown_operator_fails(self):
        res = self._run({"grover.marked_state": ["00"]},
                        **{"Mutation Operators": "no.such.op"})
        assert res.relationship == "failure"
        assert "unknown mutation operator" in res.attributes["testsource.error"]

    def test_bad_mutants_per_row_fails(self):
        res = self._run({"grover.marked_state": ["00"]},
                        **{"Mutation Operators": "marked_state.bitflip",
                           "Mutants Per Row": "-1"})
        assert res.relationship == "failure"
        assert "testsource.error" in res.attributes


# ---------------------------------------------------------------------------
# End-to-end: a generated row drives an externalized Grover param via EL
# ---------------------------------------------------------------------------

class TestAttributeDrivenGrover:
    """
    The point of externalization: one table row, dropped onto the FlowFile as
    attributes, drives a framework processor whose property is set to `${...}`.
    Uses QiskitGroverCircuit (no quantum execution beyond building the circuit).
    """

    def test_num_iterations_comes_from_attribute(self):
        from QiskitGroverCircuit import QiskitGroverCircuit

        # Simulate the canvas wiring: property values are EL referencing the
        # attributes a SplitJson/EvaluateJsonPath step hoisted from a row.
        ctx = MockContext(**{
            "Marked State":   "${grover.marked_state}",
            "Num Iterations": "${grover.num_iterations}",
            "Output Format":  "${circuit.output_format:replaceEmpty('qasm3')}",
        })
        ff = MockFlowFile(attributes={
            "grover.marked_state":   "101",
            "grover.num_iterations": "2",
            # circuit.output_format intentionally absent -> replaceEmpty fallback
        })
        res = QiskitGroverCircuit().transform(ctx, ff)
        assert res.relationship == "success"
        assert res.attributes["circuit.marked_state"] == "101"
        assert res.attributes["circuit.num_iterations"] == "2"   # from the attribute
        assert res.attributes["circuit.num_qubits"] == "3"
        assert res.attributes["circuit.format"] == "qasm3"        # fallback applied


# ---------------------------------------------------------------------------
# Arithmetic mode
# ---------------------------------------------------------------------------

class TestArithmeticSuite:
    """
    The canvas twin of experiments/arithmetic_study.py's suite generation.
    Both go through arithmetic_spec.suite, so a canvas run and a headless run
    cover the same inputs; these tests pin that, and pin that turning the
    property on does not disturb the Grover path.
    """

    def _run(self, spec, **props):
        ctx = MockContext(**{
            "Test Matrix": '{"grover.marked_state": ["00"]}',
            "Arithmetic Suite": spec,
            **props,
        })
        return QuantumTestCaseSource().transform(ctx, MockFlowFile())

    def test_exhaustive_covers_the_whole_input_space(self):
        res = self._run("exhaustive:2")
        assert res.relationship == "success"
        assert res.attributes["testsource.mode"] == "arithmetic"
        assert res.attributes["testsource.count"] == "16"
        cases = json.loads(res.contents.decode("utf-8"))
        pairs = {(c["arithmetic.operand_a"], c["arithmetic.operand_b"]) for c in cases}
        assert len(pairs) == 16

    def test_rows_carry_the_builder_contract(self):
        cases = json.loads(self._run("exhaustive:2").contents.decode("utf-8"))
        row = next(c for c in cases
                   if c["arithmetic.operand_a"] == "3" and c["arithmetic.operand_b"] == "3")
        assert row["arithmetic.operation"] == "add"
        assert row["arithmetic.bit_width"] == "2"
        assert row["arithmetic.expected_result"] == "6"
        assert row["test.case_id"] == "add-3+3"

    def test_partition_names_the_boundaries(self):
        cases = json.loads(self._run("exhaustive:2").contents.decode("utf-8"))
        row = next(c for c in cases
                   if c["arithmetic.operand_a"] == "3" and c["arithmetic.operand_b"] == "3")
        assert "carry_out" in row["test.partition"]
        assert row["arithmetic.exercises_carry"] == "true"
        row = next(c for c in cases
                   if c["arithmetic.operand_a"] == "1" and c["arithmetic.operand_b"] == "2")
        assert "carry_out" not in row["test.partition"]
        assert row["arithmetic.exercises_carry"] == "false"

    def test_boundary_strategy_is_smaller_and_still_carries(self):
        res = self._run("boundary:2")
        cases = json.loads(res.contents.decode("utf-8"))
        assert 0 < len(cases) < 16
        assert int(res.attributes["testsource.carrying_cases"]) >= 1

    def test_matches_the_headless_suite(self):
        import arithmetic_spec as aspec

        cases = json.loads(self._run("exhaustive:2").contents.decode("utf-8"))
        emitted = [(int(c["arithmetic.operand_a"]), int(c["arithmetic.operand_b"]))
                   for c in cases]
        assert emitted == [(c["a"], c["b"]) for c in aspec.suite("exhaustive", 2)]

    def test_operation_is_selectable(self):
        cases = json.loads(self._run("boundary:2:subtract").contents.decode("utf-8"))
        assert all(c["arithmetic.operation"] == "subtract" for c in cases)

    def test_records_the_suite_for_reproducibility(self):
        res = self._run("exhaustive:2")
        assert res.attributes["testsource.suite"] == "exhaustive"
        assert res.attributes["testsource.bit_width"] == "2"
        assert "carry_out" in res.attributes["testsource.boundaries"]

    def test_bad_spec_routes_to_failure(self):
        for spec in ("exhaustive", "nonsense:2", "exhaustive:wide"):
            res = self._run(spec)
            assert res.relationship == "failure", spec
            assert res.attributes["testsource.error"]

    def test_blank_suite_leaves_the_matrix_path_untouched(self):
        res = self._run("")
        assert res.attributes["testsource.mode"] == "axes"
        cases = json.loads(res.contents.decode("utf-8"))
        assert cases[0]["grover.marked_state"] == "00"
        assert "testsource.suite" not in res.attributes
