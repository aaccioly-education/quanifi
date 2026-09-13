"""
Cross-runner interchangeability: a circuit built by any arithmetic processor
must execute correctly on any simulator processor.

This is the property the shared OpenQASM 2.0 wire format exists to provide, and
it is what lets the experiment separate "which framework built the circuit"
from "which framework ran it". A builder/runner mismatch that silently
reordered qubits would show up here as a wrong answer rather than as a
mysterious cross-framework disagreement later.
"""

import json

import pytest

from QiskitAerSimulator import QiskitAerSimulator
from CirqSimulator import CirqSimulator
from QrispSimulator import QrispSimulator

from arithmetic_spec import marginalize

from test_arithmetic_equivalence import build, LABELS

from conftest import MockContext, MockFlowFile, result_to_flowfile

RUNNERS = ["QiskitAerSimulator", "CirqSimulator", "QrispSimulator"]

# One builder per family, so the matrix stays fast while still covering a
# Toffoli-based circuit, a rotation-based one, and an out-of-framework pairing.
BUILDERS = ["qiskit/cdkm", "cirq/qft", "qrisp/gidney", "pennylane/semiadder"]

SHOTS = 256


def _run_on(runner_name, result):
    proc = {"QiskitAerSimulator": QiskitAerSimulator,
            "CirqSimulator": CirqSimulator,
            "QrispSimulator": QrispSimulator}[runner_name]()
    return proc.transform(MockContext(**{"Shots": str(SHOTS)}),
                          result_to_flowfile(result))


class TestCrossRunnerExecution:

    @pytest.mark.parametrize("builder", BUILDERS)
    @pytest.mark.parametrize("runner", RUNNERS)
    def test_builder_runs_on_every_runner(self, builder, runner):
        built = build(builder, 1, 2, 2)
        out = _run_on(runner, built)
        assert out.relationship == "success", \
            "%s could not run a circuit from %s" % (runner, builder)

    @pytest.mark.parametrize("builder", BUILDERS)
    @pytest.mark.parametrize("runner", RUNNERS)
    def test_answer_survives_the_wire_format(self, builder, runner):
        built = build(builder, 1, 2, 2)
        out = _run_on(runner, built)

        counts = json.loads(out.contents)
        idx = [int(i) for i in built.attributes["arithmetic.result_qubits"].split(",")]
        collapsed = marginalize(counts, idx)

        expected = built.attributes["arithmetic.expected_result_bits"]
        top = max(collapsed, key=collapsed.get)
        assert top == expected, (
            "%s built by %s: expected result bits %s, got %s (counts %s)"
            % (runner, builder, expected, top, collapsed))
        # noiseless simulation, so the correct answer should take every shot
        assert collapsed[expected] == SHOTS

    @pytest.mark.parametrize("builder", BUILDERS)
    @pytest.mark.parametrize("runner", RUNNERS)
    def test_runner_agrees_on_bit_order(self, builder, runner):
        out = _run_on(runner, build(builder, 1, 2, 2))
        assert out.attributes["sim.bit_order"] == "q0_left"


class TestRunnerIndependence:
    """The result must not depend on which runner executed it."""

    @pytest.mark.parametrize("builder", BUILDERS)
    def test_all_runners_produce_the_same_answer(self, builder):
        built = build(builder, 3, 2, 2)
        idx = [int(i) for i in built.attributes["arithmetic.result_qubits"].split(",")]
        answers = {}
        for runner in RUNNERS:
            counts = json.loads(_run_on(runner, built).contents)
            collapsed = marginalize(counts, idx)
            answers[runner] = max(collapsed, key=collapsed.get)
        assert len(set(answers.values())) == 1, answers

    def test_every_builder_agrees_under_one_runner(self):
        answers = {}
        for builder in BUILDERS:
            built = build(builder, 3, 2, 2)
            idx = [int(i) for i in built.attributes["arithmetic.result_qubits"].split(",")]
            counts = json.loads(_run_on("QiskitAerSimulator", built).contents)
            answers[builder] = max(marginalize(counts, idx).items(),
                                   key=lambda kv: kv[1])[0]
        assert len(set(answers.values())) == 1, answers
