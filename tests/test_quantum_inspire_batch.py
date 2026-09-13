"""The Quantum Inspire batch lane, proven offline against a real hardware result.

Nothing here touches a provider. The Tuna-17 fixture in
`tests/data/quantum_inspire_tuna17_probe.json` is a frozen record of a real
armed run (2-qubit Grover, marked state `10`, 1024 shots), so the whole
poll -> expand -> score chain can be verified against ground truth that a QPU
actually produced, with no network and no NiFi.

`qiskit-quantuminspire` is deliberately NOT imported: its `qiskit<2.4.0` pin
must not constrain the project, so the plugin lives in an optional extra and
these tests build `qiskit.result.Result` objects by hand the way
`QIJob._process_results` does.
"""
import importlib.util
import json
import pathlib
import random

import pytest
from qiskit.providers.jobstatus import JobStatus

from conftest import MockContext, MockFlowFile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "data" / "quantum_inspire_tuna17_probe.json"


def _load(name, directory="nifi_extensions"):
    """Load a processor module by path, the way NiFi does."""
    path = ROOT / directory / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


poller_mod = _load("QuantumInspireBatchPoller")
submitter_mod = _load("QuantumInspireBatchSubmitter")
expander_mod = _load("QuantumBatchResultExpander")
oracle_mod = _load("QuantumSuccessProbabilityOracle")


class _Experiment:
    """Stand-in for qiskit.result.models.ExperimentResult (shots only)."""

    def __init__(self, shots):
        self.shots = shots


@pytest.fixture(scope="module")
def tuna17():
    return json.loads(FIXTURE.read_text())


# ---------------------------------------------------------------- ordering


class TestCaseMajorOrder:
    """Builder-vs-builder is the comparison, so a case's circuits must be adjacent."""

    @staticmethod
    def _entries():
        out = []
        for case in ("grover-hw-000", "grover-hw-001", "grover-hw-002", "grover-hw-003"):
            for builder in ("qiskit", "cirq", "pennylane"):
                out.append({"group": case, "member": builder, "kind": "control",
                            "label": "{}@{}".format(builder, case)})
        return out

    def test_each_case_is_contiguous(self):
        entries = self._entries()
        random.Random(7).shuffle(entries)
        ordered = submitter_mod.case_major_order(entries)
        positions = {}
        for index, entry in enumerate(ordered):
            positions.setdefault(entry["group"], []).append(index)
        for case, idxs in positions.items():
            assert idxs == list(range(min(idxs), min(idxs) + len(idxs))), (
                "case {} is not contiguous: {}".format(case, idxs))

    def test_control_precedes_mutant_in_a_cell(self):
        entries = [
            {"group": "c0", "member": "qiskit", "kind": "gate.remove#middle",
             "label": "qiskit@c0@mut"},
            {"group": "c0", "member": "qiskit", "kind": "control",
             "label": "qiskit@c0"},
        ]
        ordered = submitter_mod.case_major_order(entries)
        assert ordered[0]["kind"] == "control"

    def test_is_a_total_order_under_reshuffle(self):
        """Three builder branches run in parallel, so arrival order is not stable.

        Sorting on declared keys must therefore give the same answer whatever
        order the entries turned up in.
        """
        entries = self._entries()
        a = list(entries)
        b = list(entries)
        random.Random(1).shuffle(a)
        random.Random(2).shuffle(b)
        labels = lambda rows: [r["label"] for r in submitter_mod.case_major_order(rows)]
        assert labels(a) == labels(b)


# ------------------------------------------------------------- merge_counts


class TestMergeCounts:

    @staticmethod
    def _entries(n=2):
        return [{"label": "cell-{}".format(i), "kind": "control", "num_qubits": 2,
                 "attributes": {"grover.expected": "10"}} for i in range(n)]

    def test_length_mismatch_is_refused(self):
        with pytest.raises(ValueError, match="Refusing to align"):
            poller_mod.merge_counts([{"01": 1}], None, self._entries(2))

    def test_results_length_mismatch_is_refused(self):
        with pytest.raises(ValueError, match="Refusing to align"):
            poller_mod.merge_counts([{"01": 1}, {"01": 1}],
                                    [_Experiment(1024)], self._entries(2))

    def test_ground_truth_and_shots_done_survive(self):
        merged, failed = poller_mod.merge_counts(
            [{"01": 900, "00": 124}, {"01": 800, "11": 224}],
            [_Experiment(1024), _Experiment(512)], self._entries(2))
        assert failed == []
        assert [r["shots_done"] for r in merged] == [1024, 512]
        assert all(r["attributes"]["grover.expected"] == "10" for r in merged)

    def test_an_empty_experiment_is_reported_not_raised(self):
        """QI reports a failed circuit as an empty histogram, not an error.

        The paid rows that DID come back must survive; only the failed label is
        collected.
        """
        merged, failed = poller_mod.merge_counts(
            [{"01": 1024}, {}], [_Experiment(1024), _Experiment(0)],
            self._entries(2))
        assert [r["label"] for r in merged] == ["cell-0"]
        assert failed == ["cell-1"]

    def test_batch_index_survives_the_merge(self):
        """A chunked QI batch stamps ``batch_index`` on its manifest entries;
        the poller must carry it through onto the polled row, or a chunk's
        rows could not be pooled back into the whole batch's submission
        order."""
        entries = self._entries(2)
        entries[0]["batch_index"] = 5
        entries[1]["batch_index"] = 6
        merged, failed = poller_mod.merge_counts(
            [{"01": 900, "00": 124}, {"01": 800, "11": 224}],
            [_Experiment(1024), _Experiment(512)], entries)
        assert failed == []
        assert [r["batch_index"] for r in merged] == [5, 6]


# ----------------------------------------------- Checkpoint 1: the real chain


class TestCheckpointOneRealHardwareChain:
    """poll -> expand -> score, against counts a real QPU produced."""

    @staticmethod
    def _polled(tuna17):
        merged, failed = poller_mod.merge_counts(
            [tuna17["counts_q0_right"]], [_Experiment(tuna17["shots"])],
            [{"label": "qiskit@grover-hw-000", "kind": "control", "num_qubits": 2,
              "attributes": {"grover.expected": tuna17["marked_state_q0_left"],
                             "grover.builder": "qiskit"}}])
        assert failed == []
        return {"entries": merged, "job_id": "834002", "device": tuna17["device"],
                "layout": [0, 1], "padded_width": 2, "bit_order": "q0_right",
                "estimated_usage_seconds": 0.0, "actual_usage_seconds": "",
                "calibration_set_id": None}

    def test_expander_normalises_to_q0_left(self, tuna17):
        rows = expander_mod.expand(self._polled(tuna17), source_bit_order="q0_right")
        assert len(rows) == 1
        counts = rows[0]["counts"]
        # The device's most frequent key is `01`; canonical q0-left is `10`.
        assert max(counts, key=counts.get) == tuna17["marked_state_q0_left"]
        assert counts["10"] == 945

    def test_oracle_scores_the_real_run(self, tuna17):
        rows = expander_mod.expand(self._polled(tuna17), source_bit_order="q0_right")
        successes, shots = oracle_mod.QuantumSuccessProbabilityOracle._score(
            rows[0]["counts"], tuna17["marked_state_q0_left"])
        assert successes == 945
        assert shots == 1024
        # 945/1024 exactly. The plan's 0.923828 was an arithmetic slip
        # (that is 946/1024); the frozen record says 945.
        assert round(successes / shots, 6) == 0.922852

    def test_a_palindromic_state_could_not_have_caught_this(self, tuna17):
        """Why the fixture uses `10` and not `11`.

        Reversing a palindrome is the identity, so a bit-order regression is
        invisible on `11` or `0110`. This asserts the fixture stays asymmetric.
        """
        marked = tuna17["marked_state_q0_left"]
        assert marked != marked[::-1]


# --------------------------------------------------------------- guardrails


class TestUsageEvidence:

    def test_actual_usage_is_empty_string_never_zero(self, tuna17):
        """A zero reads as a free job and corrupts a budget total.

        QI publishes no QPU-seconds metric, so the field must stay empty.
        """
        polled = TestCheckpointOneRealHardwareChain._polled(tuna17)
        assert polled["actual_usage_seconds"] == ""
        assert polled["actual_usage_seconds"] is not 0  # noqa: F632 - intent


class TestPreflightIsTheDefault:

    def test_submit_mode_defaults_to_preflight(self):
        assert submitter_mod.PREFLIGHT == "preflight"
        processor = submitter_mod.QuantumInspireBatchSubmitter(jvm=None)
        modes = [d for d in processor.descriptors if d.name == "Submit Mode"]
        assert modes, "Submit Mode property is missing"
        assert modes[0].default_value == submitter_mod.PREFLIGHT

    def test_no_api_token_property_exists(self):
        """QI auth is a file written by `qi login`, never a NiFi property."""
        processor = submitter_mod.QuantumInspireBatchSubmitter(jvm=None)
        names = {d.name for d in processor.descriptors}
        assert "API Token" not in names
        assert "Server URL" not in names

    def test_usage_cap_is_declared_inert(self):
        """QI's target sets no `dt`, so estimated usage is always 0.0.

        The processor must say so rather than let a zero read as a safe job.
        """
        source = (ROOT / "nifi_extensions" / "QuantumInspireBatchSubmitter.py").read_text()
        assert "usage_cap_effective" in source


# ----------------------------------------------------------- chunk_ranges


class TestChunkRanges:
    """Contiguous partitioning, never round-robin -- see the function's own
    docstring for why case_major_order requires that."""

    def test_fortyfour_at_five_is_nine_chunks(self):
        chunks = submitter_mod.chunk_ranges(44, 5)
        sizes = [stop - start for start, stop in chunks]
        assert sizes == [5, 5, 5, 5, 5, 5, 5, 5, 4]

    def test_a_batch_at_the_limit_is_one_chunk(self):
        assert submitter_mod.chunk_ranges(5, 5) == [(0, 5)]

    def test_twenty_control_side_circuits_split_exactly_on_case_boundaries(self):
        """The control-only design is 4 cases x 5 (3 builders + readout +
        null); a 5-circuit chunk limit must land exactly on those boundaries."""
        chunks = submitter_mod.chunk_ranges(20, 5)
        assert chunks == [(0, 5), (5, 10), (10, 15), (15, 20)]

    def test_ranges_are_contiguous_and_cover_every_index(self):
        chunks = submitter_mod.chunk_ranges(44, 5)
        covered = []
        for start, stop in chunks:
            covered.extend(range(start, stop))
        assert covered == list(range(44))

    def test_global_index_is_chunk_times_limit_plus_position(self):
        limit = 5
        chunks = submitter_mod.chunk_ranges(44, limit)
        for chunk_index, (start, stop) in enumerate(chunks):
            assert start == chunk_index * limit
            for j in range(stop - start):
                assert start + j == chunk_index * limit + j

    def test_a_zero_limit_means_one_chunk(self):
        assert submitter_mod.chunk_ranges(44, 0) == [(0, 44)]
        assert submitter_mod.chunk_ranges(0, 5) == []


# ----------------------------------------------------- native_basis_gates


class _FakeBackendType:
    """Stand-in for qiskit_quantuminspire's pydantic BackendType.

    Only what ``native_basis_gates``/``prepare`` actually read: ``gateset``
    (or its absence), plus the two batch-limit fields ``prepare`` reads off
    the same object right after, and a ``model_dump`` so the plan dict's
    ``provider_backend_type`` still serializes.
    """

    def __init__(self, gateset, max_jobs_per_batch_job=20,
                batchjobs_per_queue_limit=4):
        self.gateset = gateset
        self.max_jobs_per_batch_job = max_jobs_per_batch_job
        self.batchjobs_per_queue_limit = batchjobs_per_queue_limit

    def model_dump(self, mode="json"):
        return {"gateset": self.gateset}


class _BackendTypeNoGateset:
    """No ``gateset`` attribute at all -- an older/different BackendType shape."""

    max_jobs_per_batch_job = 20
    batchjobs_per_queue_limit = 4


TUNA17_GATESET = ["I", "Rx", "X", "X90", "mX90", "Ry", "Y", "Y90", "mY90", "Rz",
                  "Z", "S", "T", "Sdag", "Tdag", "H", "CZ", "init", "measure",
                  "reset", "barrier", "wait"]


class TestNativeBasisGates:
    """QI's Target basis is static and provider-wide (see the module
    docstring on ``native_basis_gates``); this derives the REAL, per-device
    basis from ``backend_type.gateset`` instead."""

    def test_tuna17_gateset_yields_cz_not_cp_cx_or_swap(self):
        basis = submitter_mod.native_basis_gates(_FakeBackendType(TUNA17_GATESET))
        assert basis is not None
        assert "cz" in basis
        for absent in ("cp", "cx", "swap"):
            assert absent not in basis

    def test_specific_names_map_correctly_and_case_insensitively(self):
        basis = submitter_mod.native_basis_gates(
            _FakeBackendType(["Sdag", "Tdag", "I", "CNOT", "cz", "sDAG"]))
        assert basis == sorted({"sdg", "tdg", "id", "cx", "cz"})

    def test_unmappable_names_are_dropped_and_barrier_delay_never_appear(self):
        basis = submitter_mod.native_basis_gates(_FakeBackendType(TUNA17_GATESET))
        for dropped in ("x90", "my90", "init", "wait", "barrier"):
            assert dropped not in basis
        assert "barrier" not in basis
        assert "delay" not in basis

    def test_no_gateset_attribute_is_none(self):
        assert submitter_mod.native_basis_gates(_BackendTypeNoGateset()) is None

    def test_empty_gateset_is_none(self):
        assert submitter_mod.native_basis_gates(_FakeBackendType([])) is None
        assert submitter_mod.native_basis_gates(_FakeBackendType(None)) is None

    def test_cnot_in_gateset_yields_cx_generically(self):
        """Guards against hard-coding Tuna-17's CZ-only shape: a device whose
        REAL gateset carries CNOT must get cx back, derived from the same
        table, not a special case."""
        basis = submitter_mod.native_basis_gates(
            _FakeBackendType(["H", "CNOT", "measure", "reset"]))
        assert "cx" in basis


class TestPrepareUsesRestrictedBasis:
    """``prepare``'s FINAL transpile call, not the layout search, must use
    the restricted basis when one is available -- see
    ``native_basis_gates``'s docstring for why the advertised Target is not
    trustworthy enough to transpile the submitted batch against."""

    @staticmethod
    def _fake_backend(gateset):
        from qiskit.providers.fake_provider import GenericBackendV2

        backend = GenericBackendV2(num_qubits=5, seed=11)
        backend.get_backend_type = lambda: _FakeBackendType(gateset)
        return backend

    def test_final_transpile_gets_basis_gates_not_backend(self, monkeypatch):
        import qiskit

        real_transpile = qiskit.transpile
        calls = []

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real_transpile(*args, **kwargs)

        monkeypatch.setattr(qiskit, "transpile", spy)
        processor = submitter_mod.QuantumInspireBatchSubmitter()
        monkeypatch.setattr(processor, "backend_for",
                            lambda device: self._fake_backend(TUNA17_GATESET))

        plan = processor.prepare([QI_QASM], 256, "Tuna-17", 2, 0)

        assert plan["native_basis_gates"] and "cz" in plan["native_basis_gates"]
        # The layout-search transpile calls (inside batch_prep) pass a single
        # circuit and `backend=`, never `basis_gates=` -- only the FINAL
        # submission transpile in `prepare` itself does.
        final_calls = [c for c in calls if "basis_gates" in c]
        assert final_calls, "expected the final transpile call to set basis_gates"
        final = final_calls[-1]
        assert final["basis_gates"] == plan["native_basis_gates"]
        assert "backend" not in final, "must not pass backend= alongside basis_gates"
        assert "coupling_map" in final

    def test_no_gateset_falls_back_to_backend_unchanged(self, monkeypatch):
        import qiskit

        real_transpile = qiskit.transpile
        calls = []

        def spy(*args, **kwargs):
            calls.append(kwargs)
            return real_transpile(*args, **kwargs)

        monkeypatch.setattr(qiskit, "transpile", spy)
        processor = submitter_mod.QuantumInspireBatchSubmitter()
        monkeypatch.setattr(processor, "backend_for",
                            lambda device: self._fake_backend(None))

        plan = processor.prepare([QI_QASM], 256, "Tuna-17", 2, 0)

        assert plan["native_basis_gates"] == []
        final_calls = [c for c in calls if "basis_gates" not in c
                      and c.get("backend") is not None]
        assert final_calls, "fallback call must pass backend= (unchanged behaviour)"


# -------------------------------------------------------- chunked submission


QI_QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\ncreg c[2];\n'
          'h q[0];\ncx q[0],q[1];\nmeasure q -> c;\n')


class _StubCircuitRunDatum:
    def __init__(self, job_id):
        self.job_id = job_id


class _FakeQIJob:
    """Stand-in for qiskit_quantuminspire.qi_jobs.QIJob.

    ``batch_job_id`` increments so every job has a distinct identity, exactly
    like the real one populated by ``job.submit()``. ``status()`` optionally
    walks a scripted sequence (the last value repeats once exhausted), so a
    test can make a job look QUEUED for N reads and then DONE, without any
    real waiting. Every ``status()`` call is recorded on the shared
    ``call_log``, alongside ``run_chunk`` calls, so a test can prove ORDERING
    between "a chunk was submitted" and "a queue slot was checked".
    """

    def __init__(self, batch_job_id, n_circuits, call_log=None, status_sequence=None):
        self.batch_job_id = batch_job_id
        self.circuits_run_data = [
            _StubCircuitRunDatum("{}-{}".format(batch_job_id, i))
            for i in range(n_circuits)]
        self._call_log = call_log
        self._status_sequence = (list(status_sequence)
                                 if status_sequence is not None else None)

    def serialize(self, path):
        with open(path, "wb") as handle:
            handle.write(b"fake-qpy-handle")

    def status(self):
        if self._call_log is not None:
            self._call_log.append("status:{}".format(self.batch_job_id))
        if not self._status_sequence:
            return JobStatus.DONE
        if len(self._status_sequence) > 1:
            return self._status_sequence.pop(0)
        return self._status_sequence[0]


class StubQISubmitter(submitter_mod.QuantumInspireBatchSubmitter):
    """Costs and "submits" a batch with no provider, no network, no JVM.

    ``prepare`` returns a plan built entirely offline: ``transpiled`` is a
    placeholder list (only its length and slicing matter, since ``run_chunk``
    is overridden too), and ``chunks``/``chunk_size``/
    ``provider_batchjobs_per_queue_limit`` are computed with the same
    ``chunk_ranges`` the real ``prepare`` uses, so the partitioning under test
    is the real logic, only the provider call is faked.

    ``run_chunk`` is the tripwire: if a test's ``fail_at_call`` matches, or if
    the tripwire is never called at all (preflight, or a guard that refuses
    before spending), that is the proof nothing paid for QPU time.
    """

    def __init__(self, chunk_size=5, queue_limit=5, fail_at_call=None,
                status_sequences=None, call_log=None, **kwargs):
        super().__init__(**kwargs)
        self.chunk_size = chunk_size
        self.queue_limit = queue_limit
        self.fail_at_call = fail_at_call
        self.status_sequences = status_sequences or {}
        self.call_log = call_log if call_log is not None else []
        self.chunk_calls = []
        self._next_id = 9000

    def prepare(self, qasms, shots, device, seeds, max_usage, fixed_layout=None,
               metadata=None, excluded_physical_qubits=(), prior_layouts=(),
               chunk_size_override=0, max_batch_jobs=0):
        count = len(qasms)
        limit = chunk_size_override if chunk_size_override else self.chunk_size
        chunks = submitter_mod.chunk_ranges(count, limit)
        if max_batch_jobs and len(chunks) > max_batch_jobs:
            raise RuntimeError(
                "batch of {} circuits needs {} batch jobs at {} circuits "
                "each; Maximum Batch Jobs is {}".format(
                    count, len(chunks), limit, max_batch_jobs))
        return {
            "backend": None, "transpiled": list(range(count)),
            "layout": [0, 1], "two_qubit_gates": 1, "estimated_usage": 0.0,
            "layout_evidence": {}, "widths": [2] * count, "padded_width": 2,
            "capacity": 17,
            "isa_profile": [{"two_qubit_gates": 1, "depth": 3, "gate_count": 5}
                           for _ in range(count)],
            "provenance": {"transpiler_seed": 11},
            "provider_backend_type": {"name": "Tuna-17"},
            "provider_max_batch_jobs": self.chunk_size,
            "chunks": chunks, "chunk_size": limit,
            "provider_batchjobs_per_queue_limit": self.queue_limit,
        }

    def run_chunk(self, plan, shots, circuits):
        call_number = len(self.chunk_calls) + 1
        self.chunk_calls.append(list(circuits))
        self.call_log.append("run_chunk:{}".format(call_number))
        if self.fail_at_call == call_number:
            raise RuntimeError("stub failure at chunk {}".format(call_number))
        batch_job_id = self._next_id
        self._next_id += 1
        sequence = self.status_sequences.get(len(self.chunk_calls) - 1)
        return _FakeQIJob(batch_job_id, len(circuits), call_log=self.call_log,
                          status_sequence=sequence)


def qi_ctx(tmp_path, **overrides):
    props = {
        "Batch Label": "qi-chunk", "Expected Circuits": "44", "Device": "Tuna-17",
        "Shots": "256", "Circuit Kind": "${test.kind}",
        "Circuit Label": "${circuit.label}",
        "Batch Group Key": "${test.case_id}", "Batch Member Key": "${grover.builder}",
        "Fixed Layout": "", "Layout Search Seeds": "4",
        "Submit Mode": "armed", "Attribute Prefix": "grover.",
        "Maximum Estimated Usage Seconds": "120", "Maximum Circuits": "50",
        "Maximum Shots Per Circuit": "4096",
        "Maximum Circuits Per Batch Job": "0", "Maximum Batch Jobs": "12",
        "Maximum Batch Jobs In Flight": "0", "Chunk Queue Wait Seconds": "5",
        "Chunk Queue Poll Interval Seconds": "0",
        "Control Success Probability": "0.30", "Minimum Detectable Difference": "0.10",
        "State Directory": str(tmp_path / "state"), "Slot TTL Seconds": "3600",
        "Manifest Directory": str(tmp_path / "manifests"),
        "Credentials File": str(tmp_path / "no-such-config.json"),
    }
    props.update(overrides)
    return MockContext(**props)


def _batch_entries(n, case_size=5):
    """``n`` circuits with group/member keys mimicking the case-major design
    (``case_size`` circuits share a case), in DETERMINISTIC label order."""
    entries = []
    for i in range(n):
        case = i // case_size
        entries.append({
            "label": "c%02d" % i, "kind": "control",
            "group": "case-%02d" % case, "member": "m-%02d" % (i % case_size),
            "qasm": QI_QASM, "attributes": {"grover.expected": "10"},
        })
    return entries


def _submit_batch(processor, context, entries):
    """Feed every entry through ``transform`` in arrival order; return the
    result of the FINAL (batch-completing) call."""
    result = None
    for entry in entries:
        flow = MockFlowFile(entry["qasm"].encode(), {
            "test.kind": entry["kind"], "circuit.label": entry["label"],
            "test.case_id": entry["group"], "grover.builder": entry["member"],
            "grover.expected": entry["attributes"]["grover.expected"],
        })
        result = processor.transform(context, flow)
    return result


class TestChunkedSubmission:

    def test_fortyfour_circuits_become_nine_batch_jobs(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter()
        result = _submit_batch(stub, qi_ctx(tmp_path), entries)
        assert result.relationship == "submitted"
        manifests = json.loads(result.contents.decode())
        assert len(manifests) == 9
        assert len(stub.chunk_calls) == 9
        assert result.attributes["batch.chunked"] == "true"
        assert result.attributes["batch.chunks_planned"] == "9"
        assert result.attributes["batch.chunks_submitted"] == "9"

    def test_every_circuit_is_submitted_exactly_once_in_order(self, tmp_path):
        entries = _batch_entries(44)
        shuffled = list(entries)
        random.Random(3).shuffle(shuffled)
        expected_order = [
            e["label"] for e in submitter_mod.case_major_order(
                [dict(e) for e in entries])]

        stub = StubQISubmitter()
        result = _submit_batch(stub, qi_ctx(tmp_path), shuffled)
        manifests = json.loads(result.contents.decode())
        submitted_labels = [row["label"] for cm in manifests for row in cm["entries"]]
        assert submitted_labels == expected_order
        assert sorted(submitted_labels) == sorted(e["label"] for e in entries)

    def test_batch_index_is_recoverable_from_chunk_index_and_position(self, tmp_path):
        limit = 5
        entries = _batch_entries(44)
        stub = StubQISubmitter(chunk_size=limit)
        result = _submit_batch(stub, qi_ctx(tmp_path), entries)
        manifests = json.loads(result.contents.decode())
        for chunk_manifest in manifests:
            offset = chunk_manifest["chunk_offset"]
            chunk_index = chunk_manifest["chunk_index"]
            for j, row in enumerate(chunk_manifest["entries"]):
                assert row["batch_index"] == offset + j
                assert row["batch_index"] == chunk_index * limit + j

    def test_a_five_circuit_batch_keeps_todays_single_job_shape(self, tmp_path):
        entries = _batch_entries(5)
        stub = StubQISubmitter()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Expected Circuits": "5"}), entries)
        assert result.relationship == "submitted"
        manifests = json.loads(result.contents.decode())
        assert len(manifests) == 1
        assert result.attributes["batch.chunked"] == "false"
        job_id = result.attributes["batch.job_id"]
        assert job_id
        assert result.attributes["hw.job_id"] == job_id
        assert result.attributes["batch.manifest_path"]
        chunk_manifest = manifests[0]
        assert chunk_manifest["job_id"] == job_id
        assert chunk_manifest["device"] == "Tuna-17"
        assert chunk_manifest["layout"] == [0, 1]
        assert len(chunk_manifest["entries"]) == 5
        assert chunk_manifest["provider_handle_path"]

    def test_one_manifest_and_one_handle_per_chunk_reach_disk(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter()
        _submit_batch(stub, qi_ctx(tmp_path), entries)
        manifest_dir = tmp_path / "manifests"
        manifest_files = sorted(manifest_dir.glob("*.json"))
        handle_files = sorted(manifest_dir.glob("*.qijob.qpy"))
        assert len(manifest_files) == 9
        assert len(handle_files) == 9
        manifest_job_ids = {json.loads(p.read_text())["job_id"] for p in manifest_files}
        assert len(manifest_job_ids) == 9  # every manifest filename is its own job_id

    def test_chunk_index_document_records_every_planned_chunk(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter()
        _submit_batch(stub, qi_ctx(tmp_path), entries)
        index_files = sorted((tmp_path / "manifests" / "chunks").glob("*.json"))
        assert len(index_files) == 1
        doc = json.loads(index_files[0].read_text())
        assert len(doc["chunks"]) == 9
        for chunk in doc["chunks"]:
            assert chunk["status"] == "submitted"
            assert chunk["job_id"]
            assert "batch_indices" in chunk
            assert "labels" in chunk

    def test_failure_at_the_fourth_chunk_keeps_the_three_already_paid(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter(fail_at_call=4)
        result = _submit_batch(stub, qi_ctx(tmp_path), entries)
        assert result.relationship == "submitted"
        assert result.attributes["batch.partial"] == "true"
        assert result.attributes["batch.chunks_submitted"] == "3"
        manifests = json.loads(result.contents.decode())
        assert len(manifests) == 3
        manifest_dir = tmp_path / "manifests"
        assert len(list(manifest_dir.glob("*.json"))) == 3
        index_files = list((manifest_dir / "chunks").glob("*.json"))
        doc = json.loads(index_files[0].read_text())
        for chunk in doc["chunks"][3:]:
            assert chunk["status"] == "not-submitted"
            assert "chunk 3" in chunk["error"]
        for chunk in doc["chunks"][:3]:
            assert chunk["status"] == "submitted"

    def test_a_failure_on_the_first_chunk_is_a_failure_relationship(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter(fail_at_call=1)
        result = _submit_batch(stub, qi_ctx(tmp_path), entries)
        assert result.relationship == "failure"
        assert "batch.error" in result.attributes
        manifest_dir = tmp_path / "manifests"
        assert not list(manifest_dir.glob("*.json"))
        assert not list(manifest_dir.glob("*.qijob.qpy"))

    def test_the_sixth_chunk_waits_for_the_first_to_leave_the_queue(self, tmp_path):
        call_log = []
        entries = _batch_entries(30)  # 6 chunks of 5
        stub = StubQISubmitter(chunk_size=5, queue_limit=5,
                               status_sequences={0: [JobStatus.DONE]},
                               call_log=call_log)
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Expected Circuits": "30"}), entries)
        assert result.relationship == "submitted"
        assert result.attributes["batch.chunks_submitted"] == "6"
        run_five = call_log.index("run_chunk:5")
        status_zero = next(i for i, e in enumerate(call_log) if e.startswith("status:"))
        run_six = call_log.index("run_chunk:6")
        assert run_five < status_zero < run_six, call_log

    def test_a_queue_slot_that_never_frees_stops_and_records(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter(chunk_size=5, queue_limit=5,
                               status_sequences={0: [JobStatus.QUEUED]})
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Chunk Queue Wait Seconds": "0"}), entries)
        assert result.relationship == "submitted"
        assert result.attributes["batch.partial"] == "true"
        assert result.attributes["batch.chunks_submitted"] == "5"

    def test_preflight_never_submits_a_chunk(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Submit Mode": "preflight"}), entries)
        assert result.relationship == "preflight"
        assert stub.chunk_calls == []
        report = json.loads(result.contents.decode())
        assert len(report["chunk_plan"]) == 9
        assert report["chunk_count"] == 9

    def test_maximum_batch_jobs_refuses_before_any_spend(self, tmp_path):
        entries = _batch_entries(44)
        stub = StubQISubmitter()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Maximum Batch Jobs": "4"}), entries)
        assert result.relationship == "failure"
        assert stub.chunk_calls == []


# ---------------------------------------- native_basis_gates on the FlowFile


class _StubQISubmitterWithBasis(StubQISubmitter):
    """``StubQISubmitter`` plus a restricted basis on the returned plan, to
    prove ``native_basis_gates`` reaches the FlowFile attributes/manifest
    through the normal ``transform`` path -- not just inside ``prepare``."""

    def prepare(self, *args, **kwargs):
        plan = super().prepare(*args, **kwargs)
        plan["native_basis_gates"] = ["cz", "h", "measure", "reset"]
        return plan


class TestNativeBasisGatesOnFlowFile:

    def test_armed_submission_carries_native_basis_gates(self, tmp_path):
        entries = _batch_entries(5)
        stub = _StubQISubmitterWithBasis()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Expected Circuits": "5"}), entries)
        assert result.relationship == "submitted"
        assert "cz" in result.attributes["batch.native_basis_gates"].split(",")
        manifests = json.loads(result.contents.decode())
        assert "cz" in manifests[0]["native_basis_gates"]

    def test_preflight_carries_native_basis_gates(self, tmp_path):
        entries = _batch_entries(5)
        stub = _StubQISubmitterWithBasis()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Submit Mode": "preflight",
                                      "Expected Circuits": "5"}), entries)
        assert result.relationship == "preflight"
        assert "cz" in result.attributes["batch.native_basis_gates"].split(",")
        report = json.loads(result.contents.decode())
        assert "cz" in report["native_basis_gates"]

    def test_fallback_path_is_an_empty_string_not_absent(self, tmp_path):
        """The StubQISubmitter baseline plan carries no native_basis_gates key
        at all (like a real fallback to backend=backend) -- the attribute
        must still be present, as an empty string, never missing."""
        entries = _batch_entries(5)
        stub = StubQISubmitter()
        result = _submit_batch(
            stub, qi_ctx(tmp_path, **{"Expected Circuits": "5"}), entries)
        assert result.relationship == "submitted"
        assert result.attributes["batch.native_basis_gates"] == ""


# --------------------------------------------------------- chunked expansion


class TestChunkedExpansion:
    """Two chunks of the same batch, polled separately, must pool into one
    contiguous submission-order series when their expanded rows are put back
    together -- see QuantumBatchResultExpander's batch_index docstring."""

    @staticmethod
    def _chunk(chunk_index, chunk_count, offset, count):
        return {
            "job_id": "job-{}".format(chunk_index), "device": "Tuna-17",
            "bit_order": "q0_right", "chunk_index": chunk_index,
            "chunk_count": chunk_count,
            "entries": [
                {"label": "c%02d" % (offset + i), "kind": "control",
                 "counts": {"01": 10}, "batch_index": offset + i,
                 "attributes": {"grover.expected": "10"}}
                for i in range(count)],
        }

    def test_pooled_rows_recover_one_contiguous_series(self):
        chunk0 = self._chunk(0, 2, 0, 5)
        chunk1 = self._chunk(1, 2, 5, 5)
        rows0 = expander_mod.expand(chunk0)
        rows1 = expander_mod.expand(chunk1)

        batch_indices = [int(r["attributes"]["batch.batch_index"])
                         for r in rows0 + rows1]
        assert batch_indices == list(range(10))

        entry_indices0 = [int(r["attributes"]["batch.entry_index"]) for r in rows0]
        entry_indices1 = [int(r["attributes"]["batch.entry_index"]) for r in rows1]
        assert entry_indices0 == [0, 1, 2, 3, 4]
        assert entry_indices1 == [0, 1, 2, 3, 4]  # restarts at 0 in chunk 2

        for row in rows0 + rows1:
            assert row["attributes"]["batch.chunk_index"] in ("0", "1")
            assert row["attributes"]["grover.expected"] == "10"


class TestUnreliableGatesAreExcluded:
    """Tuna-17 executes rx as though its angle were negated.

    Measured on one batch job (shared calibration and qubits): every 2-qubit
    control circuit whose transpilation contained rx came back as the bitwise
    complement of its marked state, and every circuit without rx was correct.
    Negating the rx angles of Cirq's own transpiled circuit reproduces the
    hardware result in simulation. Keeping rx out of the basis keeps every
    builder off the faulty gate; Ry and Rz still span the Bloch sphere.
    """

    def test_rx_is_dropped_even_though_the_device_advertises_it(self):
        gateset = ["I", "Rx", "X", "Ry", "Y", "Rz", "Z", "H", "CZ", "measure"]
        basis = submitter_mod.native_basis_gates(_FakeBackendType(gateset))
        assert "rx" not in basis

    def test_the_rotations_that_replace_it_survive(self):
        gateset = ["I", "Rx", "X", "Ry", "Y", "Rz", "Z", "H", "CZ", "measure"]
        basis = submitter_mod.native_basis_gates(_FakeBackendType(gateset))
        assert "ry" in basis and "rz" in basis

    def test_the_exclusion_is_declared_not_hidden_in_the_mapping(self):
        # rx must still MAP (so the table stays a faithful gateset translation);
        # it is removed by the explicit exclusion set, which is what a reader
        # and a future revert will look for.
        assert submitter_mod._QI_GATESET_TO_QISKIT["rx"] == "rx"
        assert "rx" in submitter_mod._QI_UNRELIABLE_GATES

    def test_a_device_with_no_rx_is_unaffected(self):
        gateset = ["I", "Ry", "Rz", "H", "CZ", "measure"]
        basis = submitter_mod.native_basis_gates(_FakeBackendType(gateset))
        assert basis == sorted(["id", "ry", "rz", "h", "cz", "measure"])
