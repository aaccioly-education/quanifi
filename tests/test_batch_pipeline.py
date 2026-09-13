"""Tests for the hardware batch pipeline: submitter -> poller -> calibrated oracle.

Everything runs offline. The submitter's `submit()` is stubbed (no SDK, no
network) and the poller's single HTTP entry point `_get` is monkeypatched with
canned Server API v1 payloads, the same technique tests/test_iqm.py uses.

The end-to-end test is the one that matters: it wires all three processors
together and checks the batch survives the round trip with counts landing on the
right circuits.
"""
import json

from conftest import MockContext, MockFlowFile, result_to_flowfile

import batch_prep
from QuantumIQMBatchSubmitter import (
    QuantumIQMBatchSubmitter, interleave, case_major_order,
)
from QuantumBatchPoller import QuantumBatchPoller, merge
from QuantumCalibratedOracle import QuantumCalibratedOracle

GHZ3 = ('OPENQASM 2.0;\ninclude "qelib1.inc";\n'
        "qreg q[3];\nh q[0];\ncx q[0],q[1];\ncx q[0],q[2];\n")


def sub_ctx(tmp_path, k=4, **overrides):
    props = {"Batch Label": "run-1", "Expected Circuits": str(k),
             "Device": "garnet:mock", "Server URL": "https://resonance.iqm.tech",
             "API Token": "tok", "Shots": "100", "Circuit Kind": "${test.kind}",
             "Circuit Label": "${circuit.framework}", "Layout Search Seeds": "2",
             # armed on purpose: the submitter now defaults to preflight, so a
             # test that means to spend has to say so, exactly like an operator
             "Submit Mode": "armed", "Maximum Circuits": "100",
             "Maximum Shots Per Circuit": "4096",
             "Maximum Estimated Usage Seconds": "120",
             "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "state"),
             "Manifest Directory": str(tmp_path / "manifests")}
    props.update(overrides)
    return MockContext(**props)


def circuit_ff(kind, framework, qasm=GHZ3):
    return MockFlowFile(content=qasm.encode(),
                        attributes={"test.kind": kind, "circuit.framework": framework})


class StubSubmitter(QuantumIQMBatchSubmitter):
    """Submitter with the SDK calls replaced; records what it was asked to send.

    The transform now runs in two phases: prepare() costs the batch without
    touching Resonance, run_job() is the single call that spends credits. Both
    are stubbed so no test can reach the API.
    """
    sent = None

    def prepare(self, qasms, shots, device, server, token, seeds, max_usage=0,
                fixed_layout=None):
        StubSubmitter.sent = {"qasms": qasms, "shots": shots, "device": device,
                              "server": server, "seeds": seeds,
                              "fixed_layout": fixed_layout}
        widths = [3] * len(qasms)
        return {"backend": None, "transpiled": [], "layout": [0, 1, 2],
                "two_qubit_gates": 24, "estimated_usage": 0.25,
                "widths": widths, "padded_width": 3, "capacity": 20}

    def run_job(self, plan, shots):
        return "job-abc"

    def read_provider_credits(self, server, token):
        # The real implementation always reports "unreadable" (IQM exposes no
        # credit-balance endpoint) and refuses to arm on that -- see
        # TestProviderCredits below for that guard's own tests. A readable
        # stub value keeps every other armed test in this file unaffected.
        return 250.0


# ---------------------------------------------------------------------------

class TestInterleave:
    def test_kinds_are_spread_not_blocked(self):
        """Drift during a job must not land on one kind."""
        entries = ([{"label": "r%d" % i, "kind": "null"} for i in range(4)]
                   + [{"label": f, "kind": "control"} for f in "abc"]
                   + [{"label": "m1", "kind": "gate.remove"}])
        kinds = [e["kind"] for e in interleave(entries)]
        # the first three positions must cover all three kinds
        assert set(k if k in ("null", "control") else "mutant"
                   for k in kinds[:3]) == {"null", "control", "mutant"}
        assert len(kinds) == 8

    def test_preserves_every_entry(self):
        entries = [{"label": str(i), "kind": "null"} for i in range(5)]
        assert len(interleave(entries)) == 5


class TestSubmitter:
    def test_buffers_until_k_then_submits_once(self, tmp_path):
        proc = StubSubmitter()
        StubSubmitter.sent = None
        for i in range(3):
            res = proc.transform(sub_ctx(tmp_path), circuit_ff("null", "ref%d" % i))
            assert res.relationship == "waiting"
        assert StubSubmitter.sent is None, "submitted before the batch was full"

        res = proc.transform(sub_ctx(tmp_path), circuit_ff("control", "qiskit"))
        assert res.relationship == "submitted"
        assert StubSubmitter.sent is not None
        assert len(StubSubmitter.sent["qasms"]) == 4, "must submit ONE job of 4"

        attrs = result_to_flowfile(res).getAttributes()
        assert attrs["batch.job_id"] == "job-abc"
        assert attrs["hw.job_id"] == "job-abc"
        assert attrs["batch.size"] == "4"
        assert attrs["batch.two_qubit_gates"] == "24"
        assert "mock" in attrs["batch.warning"]

    def test_manifest_records_labels_and_kinds_in_order(self, tmp_path):
        proc = StubSubmitter()
        for kind, fw in [("null", "r0"), ("null", "r1"),
                         ("control", "qiskit"), ("gate.remove", "cirq[gate.remove]")]:
            res = proc.transform(sub_ctx(tmp_path), circuit_ff(kind, fw))
        manifest = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        assert manifest["job_id"] == "job-abc"
        assert len(manifest["entries"]) == 4
        assert {e["kind"] for e in manifest["entries"]} == {
            "null", "control", "gate.remove"}
        # qasm must not be carried downstream
        assert all("qasm" not in e for e in manifest["entries"])

    def test_rejects_non_qasm_body(self, tmp_path):
        res = StubSubmitter().transform(
            sub_ctx(tmp_path), MockFlowFile(content=b'{"counts": 1}'))
        assert res.relationship == "failure"
        assert "OpenQASM" in result_to_flowfile(res).getAttributes()["batch.error"]

    def test_submit_failure_routes_to_failure(self, tmp_path):
        class Boom(QuantumIQMBatchSubmitter):
            def prepare(self, *a, **k):
                raise RuntimeError("resonance exploded")
        proc = Boom()
        for i in range(2):
            res = proc.transform(sub_ctx(tmp_path, k=2), circuit_ff("null", "r%d" % i))
        assert res.relationship == "failure"
        assert "resonance exploded" in result_to_flowfile(res).getAttributes()["batch.error"]
        assert not (tmp_path / "state" / "run-1.json").exists()

    def test_preflight_discards_completed_slot(self, tmp_path):
        proc = StubSubmitter()
        context = sub_ctx(tmp_path, k=2, **{"Submit Mode": "preflight"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))

        assert result.relationship == "preflight"
        assert not (tmp_path / "state" / "run-1.json").exists()


class TestProviderCredits:
    """WP6 item 2: report the IQM credit balance and refuse to arm without it.

    iqm-client 35.0.0 / qiskit-iqm expose no credit-balance endpoint (checked
    against experiments/iqm_probe.py, the only place this codebase has read a
    real IQM account -- it reads only job status and counts), so the real
    implementation always reports "unreadable". That is exercised directly,
    offline, with no stub -- there is nothing to mock because it never
    contacts anything.
    """

    def test_real_implementation_has_no_endpoint_to_read(self):
        assert QuantumIQMBatchSubmitter().read_provider_credits(
            "https://resonance.iqm.tech", "tok") is None

    def test_preflight_reports_credits_from_a_readable_balance(self, tmp_path):
        proc = StubSubmitter()
        context = sub_ctx(tmp_path, k=2, **{"Submit Mode": "preflight"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "preflight"
        assert result.attributes["batch.provider_credits"] == "250.0"

    def test_unreadable_balance_refuses_to_arm(self, tmp_path):
        class UnreadableCredits(StubSubmitter):
            def read_provider_credits(self, server, token):
                return None

        proc = UnreadableCredits()
        context = sub_ctx(tmp_path, k=2, **{"Submit Mode": "armed"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "failure"
        attrs = result_to_flowfile(result).getAttributes()
        assert attrs["batch.provider_credits"] == "unreadable"
        assert "unreadable" in attrs["batch.error"]


class UnreadableCreditsTripwire(StubSubmitter):
    """Like StubSubmitter, but reports an unreadable balance and records
    whether run_job (the spend path) was actually called -- a flag rather
    than a raise, since transform() catches every run_job exception and
    turns it into a `failure` relationship, which would make a raising stub
    pass for the wrong reason (see tests/test_preflight.py's _Tripwire)."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.spent = False

    def read_provider_credits(self, server, token):
        return None

    def run_job(self, plan, shots):
        self.spent = True
        return super().run_job(plan, shots)


class TestAllowUnreadableCreditBalanceOverride:
    """The repo owner has explicitly accepted the risk of submitting to IQM
    without a credit check for this study. The override must be opt-in: an
    operator who sets nothing, or sets it to false, still gets today's
    refusal (WP6 item 2's safe default is unchanged); only an explicit
    "true" arms submission on an unreadable balance, and it must say so on
    the result.
    """

    def test_override_absent_still_refuses_to_arm(self, tmp_path):
        proc = UnreadableCreditsTripwire()
        context = sub_ctx(tmp_path, k=2, **{"Submit Mode": "armed"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "failure"
        assert proc.spent is False
        attrs = result_to_flowfile(result).getAttributes()
        assert "credit" in attrs["batch.error"].lower()

    def test_override_explicitly_false_still_refuses_to_arm(self, tmp_path):
        proc = UnreadableCreditsTripwire()
        context = sub_ctx(tmp_path, k=2, **{
            "Submit Mode": "armed",
            "Allow Unreadable Credit Balance": "false"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "failure"
        assert proc.spent is False

    def test_override_true_submits_and_records_the_skip(self, tmp_path):
        proc = UnreadableCreditsTripwire()
        context = sub_ctx(tmp_path, k=2, **{
            "Submit Mode": "armed",
            "Allow Unreadable Credit Balance": "true"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "submitted"
        assert proc.spent is True
        attrs = result_to_flowfile(result).getAttributes()
        assert attrs["batch.credit_check_skipped"] == "true"
        assert attrs["batch.provider_credits"] == "unreadable"

    def test_override_true_does_not_turn_preflight_into_a_submission(self, tmp_path):
        proc = UnreadableCreditsTripwire()
        context = sub_ctx(tmp_path, k=2, **{
            "Submit Mode": "preflight",
            "Allow Unreadable Credit Balance": "true"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "preflight"
        assert proc.spent is False

    def test_normal_armed_submission_marks_the_check_as_not_skipped(self, tmp_path):
        """StubSubmitter's default reports a readable balance; the override
        never comes into play, so the flag must read false, not be silently
        left off the result."""
        proc = StubSubmitter()
        context = sub_ctx(tmp_path, k=2, **{"Submit Mode": "armed"})
        proc.transform(context, circuit_ff("null", "ref"))
        result = proc.transform(context, circuit_ff("control", "qiskit"))
        assert result.relationship == "submitted"
        attrs = result_to_flowfile(result).getAttributes()
        assert attrs["batch.credit_check_skipped"] == "false"


class TestOrderingSwitch:
    """case_major_order replaces interleave() only when Batch Group Key is
    set, so the adder study (which never configures it) is untouched."""

    def test_blank_group_key_keeps_interleave(self, tmp_path):
        proc = StubSubmitter()
        plan = [("null", "r0"), ("null", "r1"),
                ("control", "qiskit"), ("gate.remove", "cirq")]
        res = None
        for kind, fw in plan:
            res = proc.transform(sub_ctx(tmp_path, k=len(plan)), circuit_ff(kind, fw))
        manifest = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        expected = [e["kind"] for e in interleave(
            [{"label": fw, "kind": kind} for kind, fw in plan])]
        assert [e["kind"] for e in manifest["entries"]] == expected

    def test_group_key_switches_to_case_major_order(self, tmp_path):
        proc = StubSubmitter()
        entries = [
            ("control", "case-b/qiskit", "case-b", "qiskit"),
            ("control", "case-a/qiskit", "case-a", "qiskit"),
            ("control", "case-a/cirq", "case-a", "cirq"),
        ]
        overrides = {"Batch Group Key": "${test.case}",
                     "Batch Member Key": "${test.builder}"}
        res = None
        for kind, label, case, builder in entries:
            ff = MockFlowFile(
                content=GHZ3.encode(),
                attributes={"test.kind": kind, "circuit.framework": label,
                            "test.case": case, "test.builder": builder})
            res = proc.transform(
                sub_ctx(tmp_path, k=len(entries), **overrides), ff)
        manifest = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        assert [e["label"] for e in manifest["entries"]] == [
            "case-a/cirq", "case-a/qiskit", "case-b/qiskit"]


class TestGroverHardwareMatrixMetrics:
    """WP2 acceptance: per-builder post-routing two-qubit counts, as the
    manifest itself would record them (batch_prep.isa_profile +
    manifest_entries + merge_isa_profile), on the pinned line-topology
    backend the preregistration measured (PREREG-grover-hw-matrix.md, B2):
    Qiskit 103 < PennyLane 125 < Cirq 148 two-qubit gates after routing.
    """

    @staticmethod
    def _grover_qasm(cls):
        return cls().transform(
            MockContext(**{"Marked State": "0111", "Num Iterations": "2",
                          "Output Format": "qasm2"}),
            MockFlowFile()).contents.decode()

    @classmethod
    def _fixture(cls):
        from qiskit import QuantumCircuit, transpile
        from qiskit.providers.fake_provider import GenericBackendV2
        from qiskit.transpiler import CouplingMap

        from QiskitGroverCircuit import QiskitGroverCircuit
        from PennylaneGroverCircuit import PennylaneGroverCircuit
        from CirqGroverCircuit import CirqGroverCircuit

        qasms = [cls._grover_qasm(builder) for builder in
                 (QiskitGroverCircuit, PennylaneGroverCircuit, CirqGroverCircuit)]
        circuits = [QuantumCircuit.from_qasm_str(q) for q in qasms]
        backend = GenericBackendV2(num_qubits=5, basis_gates=["rz", "sx", "x", "cx"],
                                   coupling_map=CouplingMap.from_line(5), seed=1)
        transpiled = transpile(circuits, backend=backend, optimization_level=3,
                              seed_transpiler=11)
        return backend, circuits, transpiled, qasms

    def test_manifest_entries_carry_three_different_ordered_counts(self):
        _backend, circuits, transpiled, qasms = self._fixture()
        ordered = [{"label": name, "kind": "control", "qasm": qasm}
                   for name, qasm in zip(("qiskit", "pennylane", "cirq"), qasms)]
        widths = [c.num_qubits for c in circuits]
        rows = batch_prep.manifest_entries(ordered, widths)
        rows = batch_prep.merge_isa_profile(rows, batch_prep.isa_profile(transpiled))
        qiskit_n, pennylane_n, cirq_n = (row["isa_two_qubit_gates"] for row in rows)
        assert len({qiskit_n, pennylane_n, cirq_n}) == 3, (qiskit_n, pennylane_n, cirq_n)
        assert qiskit_n < pennylane_n < cirq_n
        # Reproduced exactly on this pinned backend/seed; see PREREG B2.
        assert (qiskit_n, pennylane_n, cirq_n) == (103, 125, 148)

    def test_provenance_keys_present_and_nonempty(self):
        backend, _circuits, _transpiled, _qasms = self._fixture()
        provenance = batch_prep.transpiler_provenance(
            backend, seed_transpiler=11, optimization_level=3)
        assert provenance["qiskit_version"]
        assert provenance["transpiler_seed"] == 11
        assert provenance["optimization_level"] == 3
        assert provenance["basis_gates"]
        assert provenance["coupling_map_edges"] > 0


class TestMerge:
    def test_attaches_counts_by_index(self):
        entries = [{"label": "a", "kind": "null"}, {"label": "b", "kind": "control"}]
        payload = [{"counts": {"000": 5}}, {"counts": {"111": 7}}]
        merged = merge(entries, payload)
        assert merged[0]["counts"] == {"000": 5}
        assert merged[1]["label"] == "b" and merged[1]["counts"] == {"111": 7}

    def test_size_mismatch_is_an_error(self):
        try:
            merge([{"label": "a", "kind": "null"}], [{"counts": {"0": 1}},
                                                     {"counts": {"1": 1}}])
        except ValueError as exc:
            assert "batch size mismatch" in str(exc)
        else:
            raise AssertionError("expected a size mismatch error")

    def test_ground_truth_survives_merge(self):
        """attributes/num_qubits are a circuit's only ground truth past the
        round trip; dropping them makes the expander emit batch.scorable=0 and
        the lane silently degrades to a distributional oracle."""
        entries = [{"label": "a", "kind": "control", "num_qubits": 2,
                    "attributes": {"arithmetic.expected_result_bits": "1",
                                   "arithmetic.result_qubits": "0"}}]
        merged = merge(entries, [{"counts": {"10": 5}}])
        assert merged[0]["attributes"]["arithmetic.expected_result_bits"] == "1"
        assert merged[0]["num_qubits"] == 2


class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._payload = code, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def poll_ctx(**overrides):
    props = {"API Token": "tok", "Job ID": "${batch.job_id}",
             "Server URL": "https://resonance.meetiqm.com",
             "Poll Timeout Seconds": "1", "Poll Interval Seconds": "1"}
    props.update(overrides)
    return MockContext(**props)


def manifest_ff(job_id="job-abc", entries=None):
    body = {"job_id": job_id, "device": "garnet:mock", "shots": 100,
            "entries": entries or [{"label": "r0", "kind": "null"},
                                   {"label": "qiskit", "kind": "control"}]}
    return MockFlowFile(content=json.dumps(body).encode(),
                        attributes={"batch.job_id": job_id})


class TestPoller:
    def test_pending_while_queued(self, monkeypatch):
        proc = QuantumBatchPoller()
        monkeypatch.setattr(proc, "_get",
                            lambda s, url, timeout=30: _Resp(200, {"status": "processing"}))
        res = proc.transform(poll_ctx(), manifest_ff())
        assert res.relationship == "pending"
        assert result_to_flowfile(res).getAttributes()["batch.status"] == "processing"

    def test_completed_emits_merged_entries(self, monkeypatch):
        proc = QuantumBatchPoller()

        def fake(session, url, timeout=30):
            if url.endswith("measurement_counts"):
                return _Resp(200, [{"counts": {"000": 60, "111": 40}},
                                   {"counts": {"000": 55, "111": 45}}])
            return _Resp(200, {"status": "completed"})
        monkeypatch.setattr(proc, "_get", fake)

        res = proc.transform(poll_ctx(), manifest_ff())
        assert res.relationship == "success"
        body = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        assert [e["label"] for e in body["entries"]] == ["r0", "qiskit"]
        assert body["entries"][0]["counts"] == {"000": 60, "111": 40}
        assert result_to_flowfile(res).getAttributes()["batch.size"] == "2"

    def test_failed_job_routes_to_failure(self, monkeypatch):
        proc = QuantumBatchPoller()
        monkeypatch.setattr(proc, "_get",
                            lambda s, url, timeout=30: _Resp(200, {"status": "failed"}))
        res = proc.transform(poll_ctx(), manifest_ff())
        assert res.relationship == "failure"

    def test_completed_payload_advertises_q0_left(self, monkeypatch):
        """The IQM serializer already emits q0_left keys. If the payload stays
        silent, the expander falls back to its q0_right default and mirrors
        every key — every ground-truth score then reads a mirrored bitstring."""
        proc = QuantumBatchPoller()

        def fake(session, url, timeout=30):
            if url.endswith("measurement_counts"):
                return _Resp(200, [{"counts": {"10": 90, "00": 10}},
                                   {"counts": {"10": 80, "00": 20}}])
            return _Resp(200, {"status": "completed"})
        monkeypatch.setattr(proc, "_get", fake)

        res = proc.transform(poll_ctx(), manifest_ff())
        assert res.relationship == "success"
        body = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        assert body["bit_order"] == "q0_left"
        assert result_to_flowfile(res).getAttributes()["batch.bit_order"] == "q0_left"

    def test_poller_output_expands_scorable_and_unmirrored(self, monkeypatch):
        """The end of the IQM lane: keys stay q0_left through the expander and
        the manifest ground truth survives, so the batch is scorable."""
        from QuantumBatchResultExpander import QuantumBatchResultExpander
        entries = [{"label": "impl-%d" % i, "kind": "control", "num_qubits": 2,
                    "attributes": {"arithmetic.expected_result_bits": "1",
                                   "arithmetic.result_qubits": "0"}}
                   for i in range(2)]
        proc = QuantumBatchPoller()

        def fake(session, url, timeout=30):
            if url.endswith("measurement_counts"):
                return _Resp(200, [{"counts": {"10": 90, "00": 10}},
                                   {"counts": {"10": 85, "00": 15}}])
            return _Resp(200, {"status": "completed"})
        monkeypatch.setattr(proc, "_get", fake)

        res = proc.transform(poll_ctx(), manifest_ff(entries=entries))
        assert res.relationship == "success"
        body = json.loads(bytes(result_to_flowfile(res).getContentsAsBytes()).decode())
        assert body["entries"][0]["num_qubits"] == 2
        assert (body["entries"][0]["attributes"]
                ["arithmetic.expected_result_bits"]) == "1"

        expanded = QuantumBatchResultExpander().transform(
            MockContext(**{"Source Bit Order": "q0_right"}),
            MockFlowFile(content=json.dumps(body).encode()))
        assert expanded.relationship == "success"
        assert expanded.attributes["batch.scorable"] == "2"
        rows = json.loads(expanded.contents.decode())
        assert "10" in rows[0]["counts"]  # not mirrored to "01"
        assert (rows[0]["attributes"]
                ["arithmetic.expected_result_bits"]) == "1"

    def test_network_error_is_retryable_not_fatal(self, monkeypatch):
        proc = QuantumBatchPoller()

        def boom(session, url, timeout=30):
            raise OSError("connection reset by peer")
        monkeypatch.setattr(proc, "_get", boom)
        res = proc.transform(poll_ctx(), manifest_ff())
        # the garnet failure mode: must not lose the batch
        assert res.relationship == "pending"

    def test_bad_manifest_routes_to_failure(self):
        res = QuantumBatchPoller().transform(
            poll_ctx(), MockFlowFile(content=b"not json"))
        assert res.relationship == "failure"


class TestEndToEnd:
    def test_submitter_poller_oracle_round_trip(self, tmp_path, monkeypatch):
        """Full canvas path: 6 circuits in, one calibrated verdict out."""
        proc = StubSubmitter()
        plan = ([("null", "r%d" % i) for i in range(4)]
                + [("control", "qiskit"), ("control", "cirq")])
        res = None
        for kind, fw in plan:
            res = proc.transform(sub_ctx(tmp_path, k=len(plan)), circuit_ff(kind, fw))
        assert res.relationship == "submitted"
        manifest = result_to_flowfile(res)

        poller = QuantumBatchPoller()
        histograms = [{"counts": {"000": 50 + i, "111": 50 - i}} for i in range(6)]
        monkeypatch.setattr(
            poller, "_get",
            lambda s, url, timeout=30: _Resp(200, histograms)
            if url.endswith("measurement_counts") else _Resp(200, {"status": "completed"}))
        polled = poller.transform(
            poll_ctx(), MockFlowFile(content=bytes(manifest.getContentsAsBytes()),
                                     attributes=dict(manifest.getAttributes())))
        assert polled.relationship == "success"
        polled_ff = result_to_flowfile(polled)

        oracle = QuantumCalibratedOracle()
        verdict = oracle.transform(
            MockContext(**{"Reports Directory": str(tmp_path / "r"),
                           "Raw Results Directory": str(tmp_path / "raw"),
                           "Flow Name": "e2e", "Significance Level": "0.05",
                           "Threshold Quantile": "0.95", "Minimum Replicates": "4",
                           "Expected Support": ""}),
            MockFlowFile(content=bytes(polled_ff.getContentsAsBytes()),
                         attributes=dict(polled_ff.getAttributes())))
        attrs = result_to_flowfile(verdict).getAttributes()
        assert verdict.relationship == "pass"
        assert attrs["oracle.replicates"] == "4"
        assert attrs["oracle.false_alarms"] == "0"
        assert attrs["batch.device"] == "garnet:mock"


class TestSlotTTL:
    """A partial batch must not buffer forever.

    Without a TTL, one failed branch means the Kth circuit never arrives, the
    batch never fires, and every later run joins the same orphaned slot and is
    silently swallowed. The TTL makes that self-healing.
    """

    def test_stale_slot_is_discarded(self, tmp_path):
        import os
        import time as _time
        proc = StubSubmitter()
        StubSubmitter.sent = None
        # two circuits of a four-circuit batch, then the branch "fails"
        for i in range(2):
            proc.transform(sub_ctx(tmp_path), circuit_ff("null", "r%d" % i))
        slot = tmp_path / "state" / "run-1.json"
        assert slot.exists()

        # age the slot past the TTL
        old = _time.time() - 7200
        os.utime(slot, (old, old))

        # a later run must start fresh rather than joining the orphan
        proc.transform(sub_ctx(tmp_path, **{"Slot TTL Seconds": "3600"}),
                       circuit_ff("null", "fresh"))
        entries = json.loads(slot.read_text())["entries"]
        assert [e["label"] for e in entries] == ["fresh"], entries
        assert StubSubmitter.sent is None

    def test_fresh_slot_is_kept(self, tmp_path):
        proc = StubSubmitter()
        for i in range(2):
            proc.transform(sub_ctx(tmp_path, **{"Slot TTL Seconds": "3600"}),
                           circuit_ff("null", "r%d" % i))
        entries = json.loads((tmp_path / "state" / "run-1.json").read_text())["entries"]
        assert len(entries) == 2

    def test_ttl_zero_disables_the_check(self, tmp_path):
        import os
        import time as _time
        proc = StubSubmitter()
        proc.transform(sub_ctx(tmp_path), circuit_ff("null", "r0"))
        slot = tmp_path / "state" / "run-1.json"
        old = _time.time() - 999999
        os.utime(slot, (old, old))
        proc.transform(sub_ctx(tmp_path, **{"Slot TTL Seconds": "0"}),
                       circuit_ff("null", "r1"))
        entries = json.loads(slot.read_text())["entries"]
        assert len(entries) == 2, "TTL 0 must not discard anything"
