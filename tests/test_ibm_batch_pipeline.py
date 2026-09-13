import json
from datetime import datetime, timedelta, timezone

from conftest import MockContext, MockFlowFile, result_to_flowfile
from batch_prep import qasm_digest as batch_digest
from QuantumIBMBatchSubmitter import (
    QuantumIBMBatchSubmitter,
    _result_aware_metadata,
)
from QuantumIBMBatchPoller import (
    QuantumIBMBatchPoller,
    _usage_seconds,
    extract_counts,
)

QASM = ("OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[2];\n"
        "h q[0];\ncx q[0],q[1];\n")


def submit_context(tmp_path, k=2, **overrides):
    props = {"Batch Label": "run-ibm-kingston", "Expected Circuits": str(k),
             "Device": "ibm_kingston", "API Token": "token", "Instance": "test",
             "Shots": "100", "Circuit Kind": "${test.kind}",
             "Circuit Label": "${test.framework}", "Layout Search Seeds": "2",
             "Maximum Pending Jobs": "10", "Maximum Estimated Usage Seconds": "120",
             "Maximum Circuits": "256", "Maximum Shots Per Circuit": "4096",
             # armed on purpose: the processor now defaults to preflight, so a
             # test that means to submit has to say so, exactly like an operator
             "Submit Mode": "armed", "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "state"), "Slot TTL Seconds": "3600",
             "Manifest Directory": str(tmp_path / "manifests")}
    props.update(overrides)
    return MockContext(**props)


# The transform now runs in two phases -- prepare() costs the batch and never
# contacts the provider, run_job() is the single call that spends QPU time -- so
# a stub replaces both rather than the old one-shot submit().
PLAN = {"backend": None, "transpiled": [], "layout": [1, 2], "two_qubit_gates": 36,
        "pending": 1, "estimated_usage": 0.25, "widths": [2, 2],
        "padded_width": 2, "capacity": 127}


class StubSubmitter(QuantumIBMBatchSubmitter):
    def prepare(self, *args, **kwargs):
        return dict(PLAN)

    def run_job(self, plan, shots):
        return "ibm-job"

    def serialize_isa(self, plan, expected):
        return [QASM] * expected

    def service_for(self, token, instance):
        now = datetime.now(timezone.utc)
        class Service:
            @staticmethod
            def usage():
                return {"usage": {"seconds": 1},
                        "usage_limit": {"seconds": 600},
                        "usage_period": {
                            "start_time": now - timedelta(days=28),
                            "end_time": now}}
        return Service()


def test_completed_metrics_are_actual_usage_not_the_provider_estimate():
    class CompletedJob:
        usage_estimation = {"quantum_seconds": 6.010557634}

        @staticmethod
        def metrics():
            return {
                "usage": {"quantum_seconds": 5, "status": "complete"},
                "bss": {"seconds": 5},
            }

    assert _usage_seconds(CompletedJob()) == "5"


def test_usage_estimation_alone_is_not_reported_as_actual_usage():
    class UnaccountedJob:
        usage_estimation = {"quantum_seconds": 6.010557634}

        @staticmethod
        def metrics():
            return {"timestamps": {"finished": "2026-09-05T07:56:16Z"}}

    # attempts=1: the point is that an estimate is never promoted, not how
    # long the poller waits for a charge that has not been posted.
    assert _usage_seconds(UnaccountedJob(), attempts=1) == ""


def flow(kind, label):
    return MockFlowFile(QASM.encode(), {"test.kind": kind, "test.framework": label})


def test_submitter_buffers_and_emits_manifest(tmp_path):
    proc = StubSubmitter()
    assert proc.transform(submit_context(tmp_path), flow("null", "ref")).relationship == "waiting"
    result = proc.transform(submit_context(tmp_path), flow("control", "qiskit"))
    assert result.relationship == "submitted"
    ff = result_to_flowfile(result)
    assert ff.getAttributes()["batch.job_id"] == "ibm-job"
    assert ff.getAttributes()["batch.estimated_usage_seconds"] == "0.250000"
    manifest = json.loads(bytes(ff.getContentsAsBytes()))
    assert len(manifest["entries"]) == 2
    assert all(len(entry["isa_qasm_sha256"]) == 64
               for entry in manifest["entries"])
    assert all("isa_qasm" not in entry for entry in manifest["entries"])
    archived = json.loads(
        (tmp_path / "manifests" / "ibm-job.json").read_text())
    for entry in archived["entries"]:
        assert entry["qasm_sha256"] == batch_digest(entry["qasm"])
        assert entry["isa_qasm_sha256"] == batch_digest(entry["isa_qasm"])


def test_manifest_archives_the_queue_depth(tmp_path):
    """The FlowFile attribute is transient; the archived manifest is not.

    Queue depth is preregistered as a per-window drift term, so a submitted job
    that cannot say how busy the device was cannot be defended later.
    """
    proc = StubSubmitter()
    proc.transform(submit_context(tmp_path), flow("null", "ref"))
    result = proc.transform(submit_context(tmp_path), flow("control", "qiskit"))
    manifest = json.loads(bytes(result_to_flowfile(result).getContentsAsBytes()))
    assert manifest["pending_jobs"] == PLAN["pending"]
    archived = json.loads((tmp_path / "manifests" / "ibm-job.json").read_text())
    assert archived["pending_jobs"] == PLAN["pending"]


def test_submit_failure_discards_slot_so_retry_starts_cleanly(tmp_path):
    class Failing(QuantumIBMBatchSubmitter):
        def prepare(self, *args, **kwargs):
            raise RuntimeError("budget cap")
    proc = Failing()
    proc.transform(submit_context(tmp_path), flow("null", "ref"))
    result = proc.transform(submit_context(tmp_path), flow("control", "qiskit"))
    assert result.relationship == "failure"
    assert "budget cap" in result_to_flowfile(result).getAttributes()["batch.error"]
    assert not (tmp_path / "state" / "run-ibm-kingston.json").exists()


def test_preflight_discards_completed_slot(tmp_path):
    proc = StubSubmitter()
    context = submit_context(tmp_path, **{"Submit Mode": "preflight"})
    proc.transform(context, flow("null", "ref"))
    result = proc.transform(context, flow("control", "qiskit"))

    assert result.relationship == "preflight"
    assert not (tmp_path / "state" / "run-ibm-kingston.json").exists()


class Counts:
    def __init__(self, counts):
        self._counts = counts
    def get_counts(self):
        return self._counts


class Data:
    def __init__(self, counts):
        self.meas = Counts(counts)
    def keys(self):
        return ["meas"]


class Pub:
    def __init__(self, counts):
        self.data = Data(counts)


def test_extract_counts_preserves_manifest_order():
    entries = [{"label": "a", "kind": "null"}, {"label": "b", "kind": "control"}]
    merged = extract_counts([Pub({"00": 5}), Pub({"11": 7})], entries)
    assert merged == [{"label": "a", "kind": "null", "counts": {"00": 5}},
                      {"label": "b", "kind": "control", "counts": {"11": 7}}]


class Job:
    def __init__(self, status="DONE"):
        self._status = status
    def status(self):
        return self._status
    def metrics(self):
        # A DONE job IBM has already charged for: the ordinary case, and the
        # one that keeps the poller from waiting out its settle window here.
        return {"usage": {"quantum_seconds": 5, "seconds": 5,
                          "status": "complete"}}
    def result(self):
        return [Pub({"00": 60, "11": 40}), Pub({"00": 55, "11": 45})]


class Service:
    def __init__(self, job):
        self._job = job
    def job(self, job_id):
        assert job_id == "ibm-job"
        return self._job


class StubPoller(QuantumIBMBatchPoller):
    job = Job()
    def service(self, token, instance):
        return Service(self.job)


def poll_context():
    return MockContext(**{"API Token": "token", "Instance": "test",
                          "Job ID": "${batch.job_id}", "Poll Timeout Seconds": "1",
                          "Poll Interval Seconds": "1"})


def manifest():
    body = {"job_id": "ibm-job", "device": "ibm_kingston",
            "entries": [{"label": "a", "kind": "null"},
                        {"label": "b", "kind": "control"}]}
    return MockFlowFile(json.dumps(body).encode(), {"batch.job_id": "ibm-job"})


def test_poller_emits_oracle_entries():
    result = StubPoller().transform(poll_context(), manifest())
    assert result.relationship == "success"
    assert json.loads(result.contents)["entries"][1]["counts"]["11"] == 45


def test_poller_routes_running_job_to_pending():
    proc = StubPoller()
    proc.job = Job("RUNNING")
    assert proc.transform(poll_context(), manifest()).relationship == "pending"


class TestTheFinalChargeIsWaitedFor:
    """IBM posts a job's charge just after DONE, not at the same instant.

    Marrakesh calibration job dae0nre42tqs73au21vg exposed this: the poller
    read metrics the moment the status flipped, got no usage, and emitted an
    empty batch.actual_usage_seconds. The campaign's completion validator
    then refused a result whose 84 histograms were all present and correct,
    stranding QPU seconds that had already been spent.
    """

    class Job:
        def __init__(self, sequence):
            self.sequence = list(sequence)
            self.reads = 0

        def metrics(self):
            value = self.sequence[min(self.reads, len(self.sequence) - 1)]
            self.reads += 1
            return value

    def test_an_unsettled_charge_is_re_read_until_it_settles(self):
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([
            {"usage": {"status": "running"}},
            {"usage": {"status": "running"}},
            {"usage": {"quantum_seconds": 14, "seconds": 14,
                       "status": "complete"}},
        ])
        assert _usage_seconds(job, attempts=6, interval=0) == "14"
        assert job.reads == 3

    def test_a_settled_charge_is_taken_on_the_first_read(self):
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([{"usage": {"quantum_seconds": 14, "status": "complete"}}])
        assert _usage_seconds(job, attempts=6, interval=0) == "14"
        assert job.reads == 1

    def test_a_charge_that_never_settles_still_reports_ibms_own_number(self):
        """Provisional beats empty -- but it is never the pre-run estimate."""
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([{"usage": {"quantum_seconds": 14, "status": "running"}}])
        assert _usage_seconds(job, attempts=3, interval=0) == "14"

    def test_no_usage_at_all_stays_empty_rather_than_zero(self):
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([{"timestamps": {}}])
        assert _usage_seconds(job, attempts=3, interval=0) == ""

    def test_the_pre_run_estimate_is_never_used_as_actual_usage(self):
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([{"timestamps": {}}])
        job.usage_estimation = {"quantum_seconds": 14.507394241}
        assert _usage_seconds(job, attempts=2, interval=0) == ""

    def test_legacy_shapes_without_a_status_are_treated_as_final(self):
        """Older responses cannot say 'provisional', so a value is the answer."""
        from QuantumIBMBatchPoller import _usage_seconds

        job = self.Job([{"bss": {"seconds": 17}}])
        assert _usage_seconds(job, attempts=4, interval=0) == "17"
        assert job.reads == 1


class TestResultAwareMetadata:
    """The layout screen must not veto a campaign that declares no result register.

    batch_prep's result-aware screen reads the result qubits from
    "arithmetic.result_qubits" and marks a candidate ineligible when the batch
    declares none. The Grover matrix declares none, so passing its attributes
    in made every candidate ineligible and failed the whole batch with
    "layout search found no candidate outside excluded qubits" -- before a
    single circuit was submitted.
    """

    def test_a_grover_batch_declares_no_result_register(self):
        ordered = [{"attributes": {"grover.builder": "qiskit",
                                   "grover.marked_state": "0111"}},
                   {"attributes": {"grover.builder": "cirq"}}]
        assert _result_aware_metadata(ordered) is None

    def test_an_arithmetic_batch_keeps_its_attributes(self):
        ordered = [{"attributes": {"arithmetic.result_qubits": "2,3,4"}},
                   {"attributes": {"arithmetic.implementation": "ripple"}}]
        out = _result_aware_metadata(ordered)
        assert out == [{"arithmetic.result_qubits": "2,3,4"},
                       {"arithmetic.implementation": "ripple"}]

    def test_one_declaring_entry_is_enough_to_keep_the_screen(self):
        ordered = [{"attributes": {"grover.builder": "qiskit"}},
                   {"attributes": {"arithmetic.result_qubits": "5"}}]
        assert _result_aware_metadata(ordered) is not None

    def test_a_present_but_empty_declaration_is_not_a_result_register(self):
        for raw in ("", "   ", ",", " , , "):
            ordered = [{"attributes": {"arithmetic.result_qubits": raw}}]
            assert _result_aware_metadata(ordered) is None, raw

    def test_entries_without_attributes_do_not_raise(self):
        assert _result_aware_metadata([{}, {"attributes": None}]) is None

    def test_an_empty_batch_declares_nothing(self):
        assert _result_aware_metadata([]) is None


class TestGroverBatchReachesPrepareWithoutMetadata:
    """End to end at the transform boundary: what prepare() actually receives."""

    @staticmethod
    def _capturing(seen):
        class Capturing(StubSubmitter):
            def prepare(self, *args, **kwargs):
                seen.append(kwargs.get("metadata"))
                return dict(PLAN)
        return Capturing()

    def test_grover_attributes_reach_prepare_as_none(self, tmp_path):
        seen = []
        proc = self._capturing(seen)
        ctx = submit_context(tmp_path, k=2, **{"Attribute Prefix": "grover."})
        proc.transform(ctx, MockFlowFile(QASM.encode(), {"test.kind": "null",
                                                         "test.framework": "ref"}))
        result = proc.transform(ctx, MockFlowFile(
            QASM.encode(), {"test.kind": "control", "test.framework": "qiskit"}))
        assert result.relationship == "submitted"
        assert seen == [None]

    def test_arithmetic_attributes_still_reach_prepare_intact(self, tmp_path):
        seen = []
        proc = self._capturing(seen)
        ctx = submit_context(tmp_path, k=2)
        proc.transform(ctx, MockFlowFile(
            QASM.encode(), {"test.kind": "null", "test.framework": "ref",
                            "arithmetic.result_qubits": "1,2"}))
        result = proc.transform(ctx, MockFlowFile(
            QASM.encode(), {"test.kind": "control", "test.framework": "qiskit",
                            "arithmetic.result_qubits": "1,2"}))
        assert result.relationship == "submitted"
        assert seen and seen[0] is not None
        assert any(a.get("arithmetic.result_qubits") == "1,2" for a in seen[0])
