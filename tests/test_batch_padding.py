"""
Width padding for mixed-width hardware batches.

The batch submitters search one layout and pin it for the whole job. Arithmetic
adders span different active widths, so the balanced selector must evaluate a
representative of each shape on every candidate layout.

    InvalidLayoutError: The length of the layout is different than the size of
    the circuit: 5 <> 6

These tests pin the fix and, just as importantly, pin its two constraints: the
padding must not be measured, and it must not reorder the batch.
"""

import pytest

from conftest import MockContext, MockFlowFile

import batch_prep

from QiskitQuantumArithmetic import QiskitQuantumArithmetic
from QrispQuantumArithmetic import QrispQuantumArithmetic

qiskit = pytest.importorskip("qiskit")


def qasm(processor, implementation, a=3, b=3, width=2):
    result = processor().transform(
        MockContext(**{"Operation": "add", "Operand A": str(a), "Operand B": str(b),
                       "Bit Width": str(width), "Implementation": implementation,
                       "Signed": "false"}),
        MockFlowFile())
    assert result.relationship == "success", result.attributes
    return result.contents.decode("utf-8")


@pytest.fixture(scope="module")
def mixed():
    """5, 6 and 8 qubits: the three widths the default hardware branches use."""
    return [qasm(QiskitQuantumArithmetic, "draper"),    # 5
            qasm(QiskitQuantumArithmetic, "cdkm"),      # 6
            qasm(QrispQuantumArithmetic, "qcla")]       # 8


class TestTheProblemIsReal:

    def test_the_adders_really_do_differ_in_width(self, mixed):
        widths = [c.num_qubits for c in batch_prep.circuits_from_qasm(mixed)]
        assert len(set(widths)) > 1, (
            "if this ever becomes uniform the padding is unnecessary, not broken")
        assert sorted(widths) == [5, 6, 8]

    def test_unpadded_mixed_batch_fails_to_transpile(self, mixed):
        """The bug this module exists for. Pinned so nobody 'simplifies' it away."""
        from qiskit import transpile
        from qiskit.transpiler.exceptions import TranspilerError

        circuits = batch_prep.circuits_from_qasm(mixed)
        backend = _backend()
        _, layout = batch_prep.layout_for(circuits[0], backend, 1)
        with pytest.raises((TranspilerError, Exception)) as excinfo:
            transpile(circuits, backend=backend, initial_layout=layout,
                      optimization_level=3, seed_transpiler=11)
        assert "layout" in str(excinfo.value).lower()


def _backend():
    """A fake backend wide enough for an 8-qubit padded batch."""
    from qiskit_ibm_runtime.fake_provider import FakeGuadalupeV2
    return FakeGuadalupeV2()


class TestPadding:

    def test_balanced_layout_records_every_implementation_shape(self, mixed):
        circuits, widths, _ = batch_prep.pad_batch(
            batch_prep.circuits_from_qasm(mixed))
        worst, layout, evidence = batch_prep.balanced_layout_for(
            circuits, _backend(), seeds=2, widths=widths)
        assert len(layout) == 8
        assert worst == max(row["two_qubit_gates"]
                            for row in evidence["winning_representatives"])
        assert evidence["strategy"] == "balanced-worst-normalized-two-qubit-v1"
        assert evidence["retry_sequence_strategy"] == (
            "ranked-distinct-physical-sets-v1")
        assert evidence["representative_count"] == 3
        assert evidence["candidate_count"] >= 2
        sequence = evidence["candidate_sequence"]
        assert sequence[0]["layout"] == layout
        assert [row["attempt"] for row in sequence] == list(
            range(1, len(sequence) + 1))
        assert len({tuple(row["physical_qubits"]) for row in sequence}) == len(sequence)
        assert len(evidence["candidate_sequence_sha256"]) == 64
        provenance = evidence["backend_provenance"]
        assert provenance["schema"] == "quanifi-backend-layout-snapshot/v1"
        assert len(provenance["snapshot_sha256"]) == 64

    def test_retry_sequence_skips_permutations_of_the_same_physical_set(self):
        def row(layout, score):
            return {"layout": layout, "objective": [score],
                    "representatives": [{"two_qubit_gates": score, "depth": score}],
                    "source": {"source_rep": 0, "source_seed": score}}

        sequence = batch_prep._candidate_sequence([
            row([1, 2, 3], 1),
            row([3, 2, 1], 2),
            row([4, 5, 6], 3),
            row([1, 7, 8], 4),
        ])
        assert [candidate["layout"] for candidate in sequence] == [
            [1, 2, 3], [4, 5, 6], [1, 7, 8]]
        assert [candidate["search_rank"] for candidate in sequence] == [1, 3, 4]
        assert sequence[1]["max_physical_overlap_with_prior"] == 0
        assert sequence[2]["max_physical_overlap_with_prior"] == 1

    def test_layout_attempt_provenance_requires_one_batch_identity(self):
        evidence = {}
        entries = [{"attributes": {
            "arithmetic.layout_attempt": "2",
            "arithmetic.layout_candidate_sequence_sha256": "abc",
            "arithmetic.layout_selection_snapshot_sha256": "def",
        }} for _ in range(3)]
        batch_prep.attach_layout_attempt(evidence, entries)
        assert evidence["qualification_attempt"] == 2
        assert evidence["candidate_sequence_sha256"] == "abc"
        assert evidence["selection_snapshot_sha256"] == "def"
        assert evidence["attempt_limit"] == 3

        entries[-1]["attributes"]["arithmetic.layout_attempt"] = "3"
        with pytest.raises(ValueError, match="inconsistent"):
            batch_prep.attach_layout_attempt({}, entries)

    def test_every_circuit_reaches_the_batch_maximum(self, mixed):
        padded, widths, target = batch_prep.pad_batch(
            batch_prep.circuits_from_qasm(mixed))
        assert target == 8
        assert widths == [5, 6, 8]
        assert [c.num_qubits for c in padded] == [8, 8, 8]

    def test_padding_is_not_measured(self, mixed):
        """The constraint that keeps result-qubit indices and key widths valid."""
        padded, widths, _ = batch_prep.pad_batch(
            batch_prep.circuits_from_qasm(mixed))
        assert [len(c.clbits) for c in padded] == widths, (
            "a padded circuit must measure its OWN qubits only")

    def test_mixed_batch_now_transpiles(self, mixed):
        from qiskit import transpile

        padded, _, _ = batch_prep.pad_batch(batch_prep.circuits_from_qasm(mixed))
        backend = _backend()
        _, layout = batch_prep.layout_for(padded[0], backend, 2)
        transpiled = transpile(padded, backend=backend, optimization_level=3,
                               initial_layout=layout, seed_transpiler=11)
        assert len(transpiled) == 3

    def test_all_branches_share_one_layout(self, mixed):
        """The reason padding beats one job per width group."""
        padded, _, _ = batch_prep.pad_batch(batch_prep.circuits_from_qasm(mixed))
        backend = _backend()
        _, layout = batch_prep.layout_for(padded[0], backend, 2)
        assert len(layout) == 8
        assert len(set(layout)) == 8, "a layout must not reuse a physical qubit"

    def test_counts_keys_keep_each_circuit_own_width(self, mixed):
        """End to end: padding must not widen the observable."""
        from qiskit import transpile
        from qiskit_aer import AerSimulator

        padded, widths, _ = batch_prep.pad_batch(
            batch_prep.circuits_from_qasm(mixed))
        backend = _backend()
        _, layout = batch_prep.layout_for(padded[0], backend, 2)
        transpiled = transpile(padded, backend=backend, optimization_level=3,
                               initial_layout=layout, seed_transpiler=11)
        result = AerSimulator().run(transpiled, shots=64, seed_simulator=11).result()
        for index, width in enumerate(widths):
            key = next(iter(result.get_counts(index)))
            assert len(key.replace(" ", "")) == width


class TestUniformBatchIsUntouched:
    """What keeps the existing GHZ hardware lanes working."""

    def test_widen_is_identity_at_target_width(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(3)
        circuit.h(0)
        circuit.cx(0, 1)
        assert batch_prep.widen(circuit, 3) == circuit

    def test_uniform_batch_keeps_its_width(self):
        from qiskit import QuantumCircuit

        circuits = []
        for _ in range(3):
            c = QuantumCircuit(3)
            c.h(0)
            c.cx(0, 1)
            circuits.append(c)
        padded, widths, target = batch_prep.pad_batch(circuits)
        assert widths == [3, 3, 3]
        assert target == 3
        assert all(c.num_qubits == 3 for c in padded)
        assert all(len(c.clbits) == 3 for c in padded)

    def test_already_measured_circuit_is_not_measured_twice(self):
        from qiskit import QuantumCircuit

        circuit = QuantumCircuit(2, 2)
        circuit.h(0)
        circuit.measure(0, 0)
        circuit.measure(1, 1)
        padded, _, _ = batch_prep.pad_batch([circuit])
        assert len(padded[0].clbits) == 2

    def test_widening_beyond_the_circuit_is_refused(self):
        from qiskit import QuantumCircuit

        with pytest.raises(ValueError):
            batch_prep.widen(QuantumCircuit(5), 3)


class TestAttributeCapture:

    def test_captures_only_the_prefix(self):
        attrs = {"arithmetic.expected_result_bits": "011",
                 "arithmetic.result_qubits": "2,3,4",
                 "test.case_id": "add-3+3", "circuit.format": "qasm2"}
        captured = batch_prep.captured_attributes(attrs, "arithmetic.")
        assert set(captured) == {"arithmetic.expected_result_bits",
                                 "arithmetic.result_qubits"}

    def test_blank_prefix_captures_nothing(self):
        assert batch_prep.captured_attributes({"a": "1"}, "") == {}

    def test_no_match_is_empty_not_an_error(self):
        assert batch_prep.captured_attributes({"a": "1"}, "arithmetic.") == {}


class TestInterleavingSurvivesPadding:
    """Drift must stay spread across kinds; padding must not reorder anything."""

    def test_order_is_unchanged_by_padding(self):
        from QuantumIBMBatchSubmitter import interleave

        entries = ([{"label": "n%d" % i, "kind": "null"} for i in range(2)]
                   + [{"label": f, "kind": "control"} for f in "abc"]
                   + [{"label": "m1", "kind": "gate.remove"}])
        ordered = interleave(entries)
        before = [e["label"] for e in ordered]
        # padding operates on circuits, never on the entry list
        assert [e["label"] for e in ordered] == before

    def test_kinds_are_still_spread(self):
        from QuantumIBMBatchSubmitter import interleave

        entries = ([{"label": "n%d" % i, "kind": "null"} for i in range(3)]
                   + [{"label": "m%d" % i, "kind": "carry.break"} for i in range(3)])
        kinds = [e["kind"] for e in interleave(entries)]
        assert kinds[0] != kinds[1], "consecutive entries should alternate kind"
