"""
Preflight: cost a batch fully, submit nothing.

The default matters more than the feature. A processor started by accident on a
canvas, or a flow restored from a backup, must not be able to spend QPU time or
credits. So the safe mode is the default and submitting is something a person
had to choose.

Every submitter here is subclassed so that neither `prepare` nor `run_job` can
reach a provider, and `run_job` records whether it was called at all. No test in
this file can contact IBM or IQM.
"""

import json
from datetime import datetime, timedelta, timezone

from conftest import MockContext, MockFlowFile, with_fake_usage

from QuantumIBMBatchSubmitter import QuantumIBMBatchSubmitter, PREFLIGHT, ARMED
from QuantumIQMBatchSubmitter import QuantumIQMBatchSubmitter
from QuantumInspireBatchSubmitter import QuantumInspireBatchSubmitter
from QuantumSuccessProbabilityOracle import required_shots

QASM = ('OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nh q[0];\ncx q[0],q[1];\n')

PLAN = {"backend": None, "transpiled": [], "layout": [1, 2, 3, 4, 5, 6],
        "two_qubit_gates": 49, "pending": 1, "estimated_usage": 0.25,
        "widths": [5, 6], "padded_width": 6, "capacity": 127}


class _Tripwire(QuantumIBMBatchSubmitter):
    """Costs the batch and records whether anything tried to submit.

    A recorded flag rather than a raise: the transform catches every exception
    from the job call and turns it into a `failure` relationship, so a raising
    stub would be swallowed and the test would pass for the wrong reason.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.submitted = False

    def prepare(self, *args, **kwargs):
        return dict(PLAN)

    def run_job(self, plan, shots):
        self.submitted = True
        return "would-have-spent-qpu-time"

    def serialize_isa(self, plan, expected):
        return [QASM] * expected


#: Ample remaining quota, so these tests isolate arming from the quota gate.
Tripwire = with_fake_usage(_Tripwire, consumed=100.0)


class IQMTripwire(QuantumIQMBatchSubmitter):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.submitted = False

    def prepare(self, *args, **kwargs):
        plan = dict(PLAN)
        plan.pop("pending")
        return plan

    def run_job(self, plan, shots):
        self.submitted = True
        return "would-have-spent-credits"

    def read_provider_credits(self, server, token):
        # The real implementation always reports "unreadable" (IQM exposes no
        # credit-balance endpoint) and the submitter refuses to arm on that --
        # a readable stub value keeps this file's arming tests independent of
        # that separate guard, which has its own tests in test_batch_pipeline.py.
        return 100.0


class QITripwire(QuantumInspireBatchSubmitter):
    """Like Tripwire/IQMTripwire, but with the chunking fields QI's own
    ``prepare`` returns (``chunks``/``chunk_size``/
    ``provider_batchjobs_per_queue_limit``) -- the chunk loop in ``transform``
    reads those even on the path that never reaches it, so preflight must
    still see a plan shaped like the real one."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.submitted = False

    def prepare(self, *args, **kwargs):
        plan = dict(PLAN)
        plan.pop("pending", None)
        plan.update({
            "provenance": {}, "provider_backend_type": {},
            "provider_max_batch_jobs": 5, "chunks": [(0, 2)], "chunk_size": 5,
            "provider_batchjobs_per_queue_limit": 5,
        })
        return plan

    def run_chunk(self, plan, shots, circuits):
        self.submitted = True
        return "would-have-spent-qpu-time"


def ibm_ctx(tmp_path, **overrides):
    props = {"Batch Label": "pf", "Expected Circuits": "2", "Device": "ibm_test",
             "API Token": "t", "Instance": "test", "Shots": "512",
             "Circuit Kind": "${test.kind}", "Circuit Label": "${test.framework}",
             "Layout Search Seeds": "2", "Maximum Pending Jobs": "10",
             "Maximum Estimated Usage Seconds": "120", "Maximum Circuits": "256",
             "Maximum Shots Per Circuit": "4096", "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "s"), "Slot TTL Seconds": "3600",
             # Default points at the real campaign archive; keep stub jobs out.
             "Manifest Directory": str(tmp_path / "manifests")}
    props.update(overrides)
    return MockContext(**props)


def iqm_ctx(tmp_path, **overrides):
    props = {"Batch Label": "pf", "Expected Circuits": "2", "Device": "garnet:mock",
             "Server URL": "https://resonance.iqm.tech", "API Token": "t",
             "Shots": "512", "Circuit Kind": "${test.kind}",
             "Circuit Label": "${test.framework}", "Layout Search Seeds": "2",
             "Maximum Estimated Usage Seconds": "120", "Maximum Circuits": "100",
             "Maximum Shots Per Circuit": "4096", "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "s"), "Slot TTL Seconds": "3600",
             # Default points at the real campaign archive; keep stub jobs out.
             "Manifest Directory": str(tmp_path / "manifests")}
    props.update(overrides)
    return MockContext(**props)


def qi_ctx(tmp_path, **overrides):
    props = {"Batch Label": "pf", "Expected Circuits": "2", "Device": "Tuna-17",
             "Shots": "512", "Circuit Kind": "${test.kind}",
             "Circuit Label": "${test.framework}", "Layout Search Seeds": "2",
             "Maximum Estimated Usage Seconds": "120", "Maximum Circuits": "50",
             "Maximum Shots Per Circuit": "4096",
             "Maximum Circuits Per Batch Job": "0", "Maximum Batch Jobs": "12",
             "Maximum Batch Jobs In Flight": "0", "Chunk Queue Wait Seconds": "900",
             "Chunk Queue Poll Interval Seconds": "10",
             "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "s"), "Slot TTL Seconds": "3600",
             # Default points at the real campaign archive; keep stub jobs out.
             "Manifest Directory": str(tmp_path / "manifests"),
             "Credentials File": str(tmp_path / "no-such-config.json")}
    props.update(overrides)
    return MockContext(**props)


def flow(kind="control", label="qiskit"):
    return MockFlowFile(QASM.encode(), {
        "test.kind": kind, "test.framework": label,
        "arithmetic.expected_result_bits": "011",
        "arithmetic.result_qubits": "2,3,4"})


def fill(processor, context_factory, tmp_path, **overrides):
    """Push two circuits through so the batch completes on the second."""
    processor.transform(context_factory(tmp_path, **overrides), flow("null", "ref"))
    return processor.transform(context_factory(tmp_path, **overrides), flow())


class TestDefaultIsSafe:

    def test_ibm_default_mode_is_preflight(self, tmp_path):
        result = fill(Tripwire(), ibm_ctx, tmp_path)
        assert result.relationship == "preflight"
        assert result.attributes["batch.submitted"] == "false"

    def test_iqm_default_mode_is_preflight(self, tmp_path):
        result = fill(IQMTripwire(), iqm_ctx, tmp_path)
        assert result.relationship == "preflight"
        assert result.attributes["batch.submitted"] == "false"

    def test_qi_default_mode_is_preflight(self, tmp_path):
        result = fill(QITripwire(), qi_ctx, tmp_path)
        assert result.relationship == "preflight"
        assert result.attributes["batch.submitted"] == "false"

    def test_the_property_default_is_the_safe_one(self):
        for cls in (QuantumIBMBatchSubmitter, QuantumIQMBatchSubmitter):
            descriptor = next(d for d in cls().getPropertyDescriptors()
                              if d.name == "Submit Mode")
            assert descriptor.default_value == PREFLIGHT

    def test_preflight_relationship_exists_on_both(self):
        for cls in (QuantumIBMBatchSubmitter, QuantumIQMBatchSubmitter):
            names = {r.name for r in cls().getRelationships()}
            assert "preflight" in names


class TestPreflightReports:

    def test_reports_the_cost_model(self, tmp_path):
        attrs = fill(Tripwire(), ibm_ctx, tmp_path).attributes
        assert attrs["batch.size"] == "2"
        assert attrs["batch.estimated_usage_seconds"] == "0.250000"
        assert attrs["batch.two_qubit_gates"] == "49"
        assert attrs["batch.layout"] == "1,2,3,4,5,6"

    def test_reports_the_padding(self, tmp_path):
        attrs = fill(Tripwire(), ibm_ctx, tmp_path).attributes
        assert attrs["batch.padded_width"] == "6"
        assert attrs["batch.circuit_widths"] == "5,6"
        assert attrs["batch.device_qubits"] == "127"

    def test_body_is_a_manifest_without_a_job(self, tmp_path):
        report = json.loads(fill(Tripwire(), ibm_ctx, tmp_path).contents.decode())
        assert report["job_id"] is None
        assert report["submit_mode"] == PREFLIGHT
        assert len(report["entries"]) == 2

    def test_ibm_preflight_proves_every_isa_is_serializable(self, tmp_path):
        report = json.loads(fill(Tripwire(), ibm_ctx, tmp_path).contents.decode())
        for entry in report["entries"]:
            assert len(entry["qasm_sha256"]) == 64
            assert len(entry["isa_qasm_sha256"]) == 64
            assert "qasm" not in entry
            assert "isa_qasm" not in entry

    def test_ground_truth_is_already_on_the_entries(self, tmp_path):
        report = json.loads(fill(Tripwire(), ibm_ctx, tmp_path).contents.decode())
        scorable = [e for e in report["entries"]
                    if e.get("attributes", {}).get("arithmetic.expected_result_bits")]
        assert len(scorable) == 2, (
            "preflight is where you find out ground truth is missing, not after "
            "the job has been paid for")

    def test_costing_records_the_queue_depth_it_was_judged_against(self, tmp_path):
        report = json.loads(fill(Tripwire(), ibm_ctx, tmp_path).contents.decode())
        assert report["pending_jobs"] == PLAN["pending"]

    def test_completed_preflight_slot_is_removed_for_clean_retry(self, tmp_path):
        fill(Tripwire(), ibm_ctx, tmp_path)
        assert not (tmp_path / "s" / "pf.json").exists()


class TestSizing:

    def test_reports_required_shots(self, tmp_path):
        attrs = fill(Tripwire(), ibm_ctx, tmp_path).attributes
        assert attrs["batch.required_shots_per_arm"] == str(required_shots(0.43, 0.10))

    def test_flags_an_insufficient_shot_budget(self, tmp_path):
        attrs = fill(Tripwire(), ibm_ctx, tmp_path, **{"Shots": "100"}).attributes
        assert attrs["batch.shots_are_sufficient"] == "false"

    def test_accepts_a_sufficient_budget(self, tmp_path):
        attrs = fill(Tripwire(), ibm_ctx, tmp_path, **{"Shots": "1024"}).attributes
        assert attrs["batch.shots_are_sufficient"] == "true"

    def test_default_control_probability_is_the_measured_one(self):
        """0.90 was the assumption; 0.43 is the measurement."""
        for cls in (QuantumIBMBatchSubmitter, QuantumIQMBatchSubmitter):
            descriptor = next(d for d in cls().getPropertyDescriptors()
                              if d.name == "Control Success Probability")
            assert float(descriptor.default_value) < 0.5

    def test_sizing_is_reported_on_iqm_too(self, tmp_path):
        attrs = fill(IQMTripwire(), iqm_ctx, tmp_path).attributes
        assert attrs["batch.required_shots_per_arm"] == str(required_shots(0.43, 0.10))


class TestGuards:

    def test_ibm_circuit_cap(self, tmp_path):
        result = fill(Tripwire(), ibm_ctx, tmp_path, **{"Maximum Circuits": "1"})
        assert result.relationship == "failure"
        assert "Maximum Circuits" in result.attributes["batch.error"]

    def test_ibm_shot_cap(self, tmp_path):
        result = fill(Tripwire(), ibm_ctx, tmp_path, **{"Maximum Shots Per Circuit": "8"})
        assert result.relationship == "failure"
        assert "Maximum Shots Per Circuit" in result.attributes["batch.error"]

    def test_iqm_circuit_cap(self, tmp_path):
        result = fill(IQMTripwire(), iqm_ctx, tmp_path, **{"Maximum Circuits": "1"})
        assert result.relationship == "failure"
        assert "Maximum Circuits" in result.attributes["batch.error"]

    def test_iqm_shot_cap(self, tmp_path):
        result = fill(IQMTripwire(), iqm_ctx, tmp_path, **{"Maximum Shots Per Circuit": "8"})
        assert result.relationship == "failure"
        assert "Maximum Shots Per Circuit" in result.attributes["batch.error"]

    def test_a_guard_breach_never_reaches_preparation(self, tmp_path):
        class Never(QuantumIBMBatchSubmitter):
            def prepare(self, *a, **k):
                raise AssertionError("guards must run before preparation")

            def run_job(self, plan, shots):
                raise AssertionError("must not submit")

        result = fill(Never(), ibm_ctx, tmp_path, **{"Maximum Circuits": "1"})
        assert result.relationship == "failure"

    def test_usage_cap_is_enforced_in_prepare(self, tmp_path):
        """The cap lives with the estimate, so preflight sees it too."""
        class OverBudget(QuantumIBMBatchSubmitter):
            def prepare(self, *a, **k):
                raise RuntimeError("estimated QPU usage 900.00s exceeds 120.00s cap")

            def run_job(self, plan, shots):
                raise AssertionError("must not submit")

        result = fill(OverBudget(), ibm_ctx, tmp_path)
        assert result.relationship == "failure"
        assert "exceeds" in result.attributes["batch.error"]


class TestArming:

    def test_preflight_never_calls_the_job(self, tmp_path):
        """The assertion the whole change exists for."""
        for processor, factory in ((Tripwire(), ibm_ctx), (IQMTripwire(), iqm_ctx)):
            fill(processor, factory, tmp_path)
            assert processor.submitted is False

    def test_armed_ibm_submits(self, tmp_path):
        processor = Tripwire()
        result = fill(processor, ibm_ctx, tmp_path, **{"Submit Mode": ARMED})
        assert processor.submitted is True
        assert result.relationship == "submitted"
        assert result.attributes["batch.submitted"] == "true"

    def test_armed_iqm_submits(self, tmp_path):
        processor = IQMTripwire()
        result = fill(processor, iqm_ctx, tmp_path, **{"Submit Mode": ARMED})
        assert processor.submitted is True
        assert result.relationship == "submitted"

    def test_an_unknown_mode_is_treated_as_preflight(self, tmp_path):
        """Fail safe: a typo must not spend."""
        processor = Tripwire()
        result = fill(processor, ibm_ctx, tmp_path, **{"Submit Mode": "armd"})
        assert result.relationship == "preflight"
        assert processor.submitted is False

    def test_guard_breach_never_submits_even_when_armed(self, tmp_path):
        processor = Tripwire()
        fill(processor, ibm_ctx, tmp_path,
             **{"Submit Mode": ARMED, "Maximum Circuits": "1"})
        assert processor.submitted is False


class TestTheQuotaGateIsFailClosed:
    """The last gate before SamplerV2.run(): no usable ledger, no spending.

    The v6 campaign runs on a rolling 600-second Open-Plan allowance shared by
    every job, so the submitter re-reads IBM usage immediately before it
    submits. That read closes the race between the runner's own reservation and
    the processor actually reaching the provider, and it refuses on any doubt.
    """

    def test_armed_submit_is_refused_when_too_little_quota_remains(self, tmp_path):
        # 595 of 600 consumed leaves 5s against a 10s floor.
        processor = with_fake_usage(_Tripwire, consumed=595.0)()
        result = fill(processor, ibm_ctx, tmp_path,
                      **{"Submit Mode": ARMED,
                         "Minimum Remaining IBM QPU Seconds": "10"})
        assert processor.submitted is False
        assert result.relationship == "failure"

    def test_armed_submit_is_refused_when_usage_is_unavailable(self, tmp_path):
        processor = with_fake_usage(
            _Tripwire, error=RuntimeError("usage endpoint down"))()
        result = fill(processor, ibm_ctx, tmp_path, **{"Submit Mode": ARMED})
        assert processor.submitted is False
        assert result.relationship == "failure"

    def test_the_runner_reservation_floor_is_honoured(self, tmp_path):
        """A campaign runner passes its whole remaining-ledger floor, not 10s."""
        processor = with_fake_usage(_Tripwire, consumed=400.0)()  # 200s left
        result = fill(processor, ibm_ctx, tmp_path,
                      **{"Submit Mode": ARMED,
                         "Minimum Remaining IBM QPU Seconds": "244"})
        assert processor.submitted is False
        assert result.relationship == "failure"

    def test_the_same_batch_submits_once_the_floor_is_met(self, tmp_path):
        """Same batch, same floor, enough quota: the refusals above are the gate."""
        processor = with_fake_usage(_Tripwire, consumed=100.0)()  # 500s left
        result = fill(processor, ibm_ctx, tmp_path,
                      **{"Submit Mode": ARMED,
                         "Minimum Remaining IBM QPU Seconds": "244"})
        assert processor.submitted is True
        assert result.relationship == "submitted"

    def test_preflight_does_not_query_usage_at_all(self, tmp_path):
        """Costing a batch must stay free of provider ledger calls."""
        cls = with_fake_usage(_Tripwire, consumed=100.0)
        processor = cls()
        fill(processor, ibm_ctx, tmp_path)
        assert processor.submitted is False
        assert cls.quota_service.calls == 0


def test_isa_proof_precedes_the_immediate_quota_read_and_run(tmp_path):
    """No QASM serialization or digest construction is left after usage()."""
    events = []

    class Ordered(_Tripwire):
        def serialize_isa(self, plan, expected):
            events.append("serialize-isa")
            return [QASM] * expected

        @staticmethod
        def service_for(token, instance):
            events.append("create-service")

            class Service:
                @staticmethod
                def usage():
                    events.append("read-quota")
                    now = datetime.now(timezone.utc)
                    return {
                        "usage": {"seconds": 100},
                        "usage_limit": {"seconds": 600},
                        "usage_period": {
                            "start_time": now - timedelta(days=28),
                            "end_time": now,
                        },
                    }

            return Service()

        def run_job(self, plan, shots):
            events.append("run")
            return super().run_job(plan, shots)

    result = fill(Ordered(), ibm_ctx, tmp_path, **{"Submit Mode": ARMED})
    assert result.relationship == "submitted"
    assert events == ["serialize-isa", "create-service", "read-quota", "run"]


def test_isa_serialization_failure_prevents_quota_read_and_submission(tmp_path):
    class Broken(_Tripwire):
        quota_calls = 0

        def serialize_isa(self, plan, expected):
            raise ValueError("unsupported ISA instruction")

        @staticmethod
        def service_for(token, instance):
            Broken.quota_calls += 1
            raise AssertionError("quota is after ISA proof")

    processor = Broken()
    result = fill(processor, ibm_ctx, tmp_path, **{"Submit Mode": ARMED})
    assert result.relationship == "failure"
    assert "ISA archive preparation failed" in result.attributes["batch.error"]
    assert Broken.quota_calls == 0
    assert processor.submitted is False
