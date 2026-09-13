"""
The whole hardware path, end to end, without submitting anything.

This is the test that answers "can we actually run this on hardware". It takes
the three real builders, pads the mixed-width batch the way the submitter does,
transpiles them onto one layout, executes on a fake backend carrying a real
device's calibrated noise model, extracts counts exactly as the poller does,
expands them, and scores them against the classical answer.

Every step is the production code path. The only substitution is the backend,
and the only thing that never happens is a job submission.

If this file passes, the remaining risk on a real run is the provider API, not
the pipeline.
"""

import json

import pytest

from conftest import MockContext, MockFlowFile

import batch_prep
import arithmetic_spec as aspec

from QiskitQuantumArithmetic import QiskitQuantumArithmetic
from CirqQuantumArithmetic import CirqQuantumArithmetic
from QrispQuantumArithmetic import QrispQuantumArithmetic
from QuantumBatchResultExpander import QuantumBatchResultExpander
from QuantumSuccessProbabilityOracle import QuantumSuccessProbabilityOracle

pytest.importorskip("qiskit_aer")

SHOTS = 1024

# One adder per algorithm family, as the hardware group defaults. Widths differ
# on purpose: 6, 5 and 8 qubits is exactly the case that used to fail.
BRANCHES = [
    ("qiskit", QiskitQuantumArithmetic, "cdkm"),
    ("cirq", CirqQuantumArithmetic, "qft"),
    ("qrisp", QrispQuantumArithmetic, "qcla"),
]


def _backend():
    from qiskit_ibm_runtime.fake_provider import FakeGuadalupeV2
    return FakeGuadalupeV2()


def _build(processor, implementation, a, b, width=2):
    result = processor().transform(
        MockContext(**{"Operation": "add", "Operand A": str(a), "Operand B": str(b),
                       "Bit Width": str(width), "Implementation": implementation,
                       "Signed": "false"}),
        MockFlowFile())
    assert result.relationship == "success", result.attributes
    return result


@pytest.fixture(scope="module")
def polled():
    """Everything a real run produces, minus the submission."""
    from qiskit import transpile
    from qiskit_aer import AerSimulator

    built = [(name, _build(processor, implementation, 3, 3))
             for name, processor, implementation in BRANCHES]

    # --- what the submitter does -------------------------------------------
    circuits, widths, target = batch_prep.pad_batch(
        batch_prep.circuits_from_qasm([r.contents.decode() for _, r in built]))
    backend = _backend()
    two_q, layout = batch_prep.layout_for(circuits[0], backend, 2)
    transpiled = transpile(circuits, backend=backend, optimization_level=3,
                           initial_layout=layout, seed_transpiler=11)

    entries = []
    for (name, result), width in zip(built, widths):
        entries.append({
            "label": name, "kind": "control", "num_qubits": width,
            "attributes": batch_prep.captured_attributes(result.attributes,
                                                         "arithmetic."),
        })

    # --- what the device and the poller do ---------------------------------
    noisy = AerSimulator.from_backend(backend)
    outcome = noisy.run(transpiled, shots=SHOTS, seed_simulator=11).result()
    for index, entry in enumerate(entries):
        entry["counts"] = outcome.get_counts(index)

    return {"job_id": "fake-job", "device": "fake_guadalupe",
            "bit_order": "q0_right", "padded_width": target,
            "layout": layout, "two_qubit_gates": two_q, "widths": widths,
            "entries": entries}


def _expand(batch):
    result = QuantumBatchResultExpander().transform(
        MockContext(**{"Source Bit Order": "q0_right"}),
        MockFlowFile(content=json.dumps(batch).encode()))
    assert result.relationship == "success", result.attributes
    return result, json.loads(result.contents.decode())


def _score(row):
    return QuantumSuccessProbabilityOracle().transform(
        MockContext(**{"Expected Outcome": "", "Mode": "single",
                       "Confidence Level": "0.95", "Alpha": "0.05",
                       "Minimum Difference": "0.05", "Comparison Label": "hw"}),
        MockFlowFile(content=json.dumps(row["counts"]).encode(),
                     attributes=row["attributes"]))


class TestTheBatchIsWhatWeThink:

    def test_the_branches_really_differ_in_width(self, polled):
        assert sorted(polled["widths"]) == [5, 6, 8]

    def test_one_layout_serves_all_three(self, polled):
        assert len(polled["layout"]) == polled["padded_width"] == 8
        assert len(set(polled["layout"])) == 8

    def test_counts_keys_keep_each_branch_own_width(self, polled):
        for entry in polled["entries"]:
            key = next(iter(entry["counts"]))
            assert len(key.replace(" ", "")) == entry["num_qubits"], (
                "padding must not widen the observable")


class TestGroundTruthSurvives:

    def test_every_entry_carries_the_answer(self, polled):
        for entry in polled["entries"]:
            assert entry["attributes"]["arithmetic.expected_result_bits"]
            assert entry["attributes"]["arithmetic.result_qubits"]

    def test_expander_marks_every_row_scorable(self, polled):
        result, _ = _expand(polled)
        assert result.attributes["batch.scorable"] == "3"
        assert "batch.warning" not in result.attributes

    def test_all_three_expect_the_same_answer(self, polled):
        _, rows = _expand(polled)
        expected = {r["attributes"]["arithmetic.expected_result_bits"] for r in rows}
        assert expected == {aspec.encode_value(6, 3)}, (
            "3+3=6 regardless of which adder computed it")


class TestScoring:

    def test_every_branch_is_scored_against_the_classical_answer(self, polled):
        _, rows = _expand(polled)
        for row in rows:
            scored = _score(row)
            assert scored.relationship == "success", scored.attributes.get("oracle.error")
            probability = float(scored.attributes["oracle.success_probability"])
            assert 0.0 < probability < 1.0, (
                "a noisy device should be neither perfect nor dead: %s" % probability)

    def test_the_interval_brackets_the_estimate(self, polled):
        _, rows = _expand(polled)
        for row in rows:
            scored = _score(row)
            p = float(scored.attributes["oracle.success_probability"])
            lo = float(scored.attributes["oracle.ci_low"])
            hi = float(scored.attributes["oracle.ci_high"])
            assert lo <= p <= hi
            assert hi - lo > 0

    def test_detectable_difference_accompanies_every_score(self, polled):
        """A null result is a claim only if it says what it could have seen."""
        _, rows = _expand(polled)
        for row in rows:
            mde = float(_score(row).attributes["oracle.detectable_difference"])
            assert 0.0 < mde < 1.0

    def test_shots_survive_the_round_trip(self, polled):
        _, rows = _expand(polled)
        for row in rows:
            assert int(_score(row).attributes["oracle.shots"]) == SHOTS


class TestBitOrderIsNotCosmetic:
    """Skipping the normalisation reports 0% and looks like a dead device."""

    def test_unnormalised_counts_score_far_worse(self, polled):
        _, correct = _expand(polled)
        raw = dict(polled)
        raw["bit_order"] = "q0_left"          # i.e. skip the flip
        _, unflipped = _expand(raw)
        good = [float(_score(r).attributes["oracle.success_probability"])
                for r in correct]
        bad = [float(_score(r).attributes["oracle.success_probability"])
               for r in unflipped]
        assert sum(good) > sum(bad), (
            "normalised counts must score better; if not, the convention moved")


class TestMeasuredReference:
    """What the run would actually look like, recorded rather than assumed."""

    def test_report_the_control_success_probabilities(self, polled):
        _, rows = _expand(polled)
        observed = {}
        for row in rows:
            label = row["attributes"].get("arithmetic.implementation", "?")
            observed[label] = float(
                _score(row).attributes["oracle.success_probability"])
        # Not an assertion about the exact values, which depend on the noise
        # model: an assertion that a CORRECT adder is far from certain on
        # hardware, which is the fact the shot budget has to be built on.
        assert all(p < 0.95 for p in observed.values()), observed
        print("\ncontrol success probability on fake_guadalupe:")
        for label, probability in sorted(observed.items()):
            print("  %-12s %.3f" % (label, probability))
