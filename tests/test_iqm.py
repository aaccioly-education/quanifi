"""Tests for the IQM lane: QrispIQMDevice (Qrisp Backend Interface) and
IQMJobPoller (IQM Resonance REST API).

Everything runs offline. QrispIQMDevice with Device Instance = 'local' uses
Qrisp's own statevector simulator, and the cloud path is exercised by stubbing
the backend factory. IQMJobPoller never touches the network: its single HTTP
entry point (`_get`) is monkeypatched with canned responses that mimic the
documented IQM Server API v1 payloads and headers.
"""
import json

import pytest

from conftest import MockContext, MockFlowFile

from QrispIQMDevice import QrispIQMDevice
from IQMJobPoller import IQMJobPoller


BELL_QASM2 = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
    "qreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q -> c;\n"
)
# q0 is |1>, q1 is |0> — non-palindromic, so a bit-order regression shows up.
ONE_ZERO_QASM2 = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
    "qreg q[2];\ncreg c[2];\nx q[0];\nmeasure q -> c;\n"
)


def qasm_flowfile(qasm=BELL_QASM2, fmt="qasm2", attributes=None):
    attrs = {"circuit.format": fmt}
    attrs.update(attributes or {})
    return MockFlowFile(content=qasm.encode(), attributes=attrs)


def fake_clock(monkeypatch):
    """Replace time.sleep/time.time with a virtual clock.

    Both processors bound their polling with `time.time() + wait >= deadline`,
    so a no-op sleep stub would spin forever: the clock has to advance by the
    slept amount. Returns the list of slept durations, in order.
    """
    import time as _time

    now = [_time.time()]
    slept = []

    def _sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(_time, "sleep", _sleep)
    monkeypatch.setattr(_time, "time", lambda: now[0])
    return slept


# ---------------------------------------------------------------------------
# Fake Qrisp Job / Backend for the cloud path
# ---------------------------------------------------------------------------

class _FakeJobResult:
    def __init__(self, counts):
        self._counts = counts

    def get_counts(self, index=0):
        return self._counts


class _FakeJob:
    """Mimics the qrisp.interface.Job contract: status() then result()."""

    def __init__(self, statuses, counts, job_id="job-1234"):
        self._statuses = list(statuses)
        self._counts = counts
        self.job_id = job_id
        self.status_calls = 0

    def status(self):
        self.status_calls += 1
        # Repeat the final status once the script is exhausted.
        return self._statuses[min(self.status_calls - 1, len(self._statuses) - 1)]

    def result(self):
        return _FakeJobResult(self._counts)


class _FakeBackend:
    def __init__(self, job):
        self._job = job
        self.submitted = []

    def run_async(self, circuit, shots=None):
        self.submitted.append((circuit, shots))
        return self._job


def _patch_backend(monkeypatch, job, adapter="iqm.qrisp_iqm"):
    backend = _FakeBackend(job)
    monkeypatch.setattr(
        QrispIQMDevice, "_iqm_backend",
        lambda self, token, device, url: (backend, adapter),
    )
    return backend


# ---------------------------------------------------------------------------
# QrispIQMDevice — local (offline) mode
# ---------------------------------------------------------------------------

class TestQrispIQMDeviceLocal:

    def test_local_runs_bell(self):
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "local", "Shots": "512"}),
            qasm_flowfile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["hw.provider"] == "iqm-resonance"
        assert a["hw.device"] == "local"
        assert a["hw.mode"] == "local-sim"
        assert a["sim.framework"] == "qrisp"
        assert a["sim.bit_order"] == "q0_left"
        assert a["report.type"] == "simulation"
        assert a["sim.shots"] == "512"
        counts = json.loads(res.contents.decode("utf-8"))
        assert set(counts) == {"00", "11"}
        assert sum(counts.values()) == 512

    def test_local_mode_sets_no_server_url(self):
        """Nothing to poll offline — the poller must not be handed a fake URL."""
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "local", "Shots": "64"}),
            qasm_flowfile())
        assert "hw.server_url" not in res.attributes

    def test_bit_order_is_q0_left(self):
        """X on q0 only -> '10' (q0 leftmost), never Qrisp's native '01'."""
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "local", "Shots": "128"}),
            qasm_flowfile(ONE_ZERO_QASM2))
        assert res.attributes["sim.top_result"] == "10"

    def test_unmeasured_circuit_gets_measured(self):
        unmeasured = ('OPENQASM 2.0;\ninclude "qelib1.inc";\n'
                      "qreg q[2];\nx q[0];\n")
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "local", "Shots": "64"}),
            qasm_flowfile(unmeasured))
        assert res.relationship == "success"
        assert res.attributes["sim.top_result"] == "10"

    def test_already_measured_circuit_is_not_measured_twice(self):
        """A second readout register would double the key width on a QPU."""
        circuit = QrispIQMDevice._circuit_from_flowfile(qasm_flowfile())
        measures = [i for i in circuit.data if i.op.name == "measure"]
        assert len(measures) == 2

    def test_seed_makes_local_runs_reproducible(self):
        ctx = MockContext(**{"Device Instance": "local", "Shots": "256",
                             "Random Seed": "1234"})
        first = QrispIQMDevice().transform(ctx, qasm_flowfile())
        second = QrispIQMDevice().transform(ctx, qasm_flowfile())
        assert first.contents == second.contents
        assert first.attributes["run.seed"] == "1234"

    def test_unsupported_format_routes_to_failure(self):
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "local"}),
            qasm_flowfile(fmt="qasm3"))
        assert res.relationship == "failure"
        assert "qasm2" in res.attributes["hw.error"]


# ---------------------------------------------------------------------------
# QrispIQMDevice — cloud path (stubbed backend)
# ---------------------------------------------------------------------------

class TestQrispIQMDeviceCloud:

    def test_fire_and_forget_emits_job_id(self, monkeypatch):
        from qrisp.interface import JobStatus

        _patch_backend(monkeypatch, _FakeJob([JobStatus.QUEUED], {"00": 1}))
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "tok",
                           "Shots": "100", "Wait For Results": "false"}),
            qasm_flowfile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["hw.mode"] == "cloud"
        assert a["hw.device"] == "garnet"
        assert a["hw.job_id"] == "job-1234"
        assert a["hw.status"] == "queued"
        assert a["hw.server_url"] == QrispIQMDevice.DEFAULT_SERVER_URL
        assert a["hw.adapter"] == "iqm.qrisp_iqm"
        # No results yet, so no simulation card and no top result.
        assert "report.type" not in a
        assert "sim.top_result" not in a
        assert json.loads(res.contents.decode("utf-8"))["job_id"] == "job-1234"

    def test_wait_polls_until_done_then_emits_counts(self, monkeypatch):
        from qrisp.interface import JobStatus

        job = _FakeJob([JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.DONE],
                       {"01": 30, "00": 70})
        _patch_backend(monkeypatch, job)
        fake_clock(monkeypatch)

        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "tok",
                           "Shots": "100", "Wait For Results": "true",
                           "Poll Interval Seconds": "1"}),
            qasm_flowfile())
        assert res.relationship == "success"
        assert job.status_calls == 3
        a = res.attributes
        assert a["hw.status"] == "done"
        assert a["report.type"] == "simulation"
        # Little-endian '01' from the backend becomes q0-left '10'.
        counts = json.loads(res.contents.decode("utf-8"))
        assert counts == {"00": 70, "10": 30}
        assert a["sim.top_result"] == "00"
        assert a["sim.top_probability"] == "0.7000"

    def test_wait_times_out_to_failure(self, monkeypatch):
        from qrisp.interface import JobStatus

        job = _FakeJob([JobStatus.QUEUED], {"00": 1})
        _patch_backend(monkeypatch, job)
        fake_clock(monkeypatch)

        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "tok",
                           "Wait For Results": "true",
                           "Poll Timeout Seconds": "1",
                           "Poll Interval Seconds": "1"}),
            qasm_flowfile())
        assert res.relationship == "failure"
        assert res.attributes["hw.status"] == "queued"
        assert "IQMJobPoller" in res.attributes["hw.error"]

    def test_error_status_routes_to_failure(self, monkeypatch):
        from qrisp.interface import JobStatus

        class _FailingJob(_FakeJob):
            def result(self):
                raise RuntimeError("calibration set expired")

        _patch_backend(monkeypatch, _FailingJob([JobStatus.ERROR], {}))
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "tok",
                           "Wait For Results": "true"}),
            qasm_flowfile())
        assert res.relationship == "failure"
        assert res.attributes["hw.status"] == "error"
        assert "calibration set expired" in res.attributes["hw.error"]

    def test_submission_error_routes_to_failure(self, monkeypatch):
        def _boom(self, token, device, url):
            raise RuntimeError("401 Unauthorized")

        monkeypatch.setattr(QrispIQMDevice, "_iqm_backend", _boom)
        res = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "bad"}),
            qasm_flowfile())
        assert res.relationship == "failure"
        assert "401 Unauthorized" in res.attributes["hw.error"]
        assert "API Token" in res.attributes["hw.error"]


# ---------------------------------------------------------------------------
# IQMJobPoller — canned REST responses
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.text = text or json.dumps(payload if payload is not None else {})

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _counts_artifact(counts):
    return [{"measurement_keys": ["m_0", "m_1"], "counts": counts}]


def _patch_http(monkeypatch, responses):
    """Serve `responses` in order, recording the URLs requested."""
    calls = []

    def _get(self, session, url, timeout=30):
        calls.append(url)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    monkeypatch.setattr(IQMJobPoller, "_get", _get)
    return calls


def poller_flowfile(job_id="job-1234", server="https://resonance.meetiqm.com"):
    return MockFlowFile(content=b'{"job_id": "job-1234"}',
                        attributes={"hw.job_id": job_id, "hw.server_url": server})


class TestIQMJobPoller:

    def test_completed_job_emits_counts(self, monkeypatch):
        calls = _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "completed", "queue_position": None}),
            _FakeResponse(payload=_counts_artifact({"10": 30, "00": 70})),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok"}), poller_flowfile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["hw.status"] == "completed"
        assert a["hw.poll_attempts"] == "1"
        assert a["sim.shots"] == "100"
        assert a["sim.top_result"] == "00"
        assert a["sim.top_probability"] == "0.7000"
        assert a["sim.bit_order"] == "q0_left"
        assert a["report.type"] == "simulation"
        assert json.loads(res.contents.decode("utf-8")) == {"00": 70, "10": 30}
        assert calls[0] == "https://resonance.meetiqm.com/api/v1/jobs/job-1234"
        assert calls[1].endswith("/artifacts/measurement_counts")

    def test_waiting_job_routes_to_pending_with_queue_position(self, monkeypatch):
        fake_clock(monkeypatch)
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "waiting", "queue_position": 7}),
        ])
        ff = poller_flowfile()
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Poll Timeout Seconds": "1",
                           "Poll Interval Seconds": "1"}), ff)
        assert res.relationship == "pending"
        assert res.attributes["hw.status"] == "waiting"
        assert res.attributes["hw.queue_position"] == "7"
        # The FlowFile passes through untouched so the loop can retry it.
        assert res.contents == b'{"job_id": "job-1234"}'

    def test_poll_attempts_accumulate_across_loop_passes(self, monkeypatch):
        fake_clock(monkeypatch)
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "processing"}),
        ])
        ff = MockFlowFile(content=b"{}", attributes={"hw.job_id": "job-1234",
                                                     "hw.poll_attempts": "4"})
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Poll Timeout Seconds": "1",
                           "Poll Interval Seconds": "1"}), ff)
        assert res.relationship == "pending"
        assert res.attributes["hw.poll_attempts"] == "5"

    def test_retry_after_header_is_honoured(self, monkeypatch):
        slept = fake_clock(monkeypatch)
        calls = _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "waiting"},
                          headers={"Retry-After": "3"}),
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"11": 100})),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Poll Timeout Seconds": "30",
                           "Poll Interval Seconds": "5"}), poller_flowfile())
        assert res.relationship == "success"
        # 3s from the header, not the 5s configured fallback.
        assert slept == [3.0]
        assert len(calls) == 3

    def test_retry_after_is_capped(self, monkeypatch):
        slept = fake_clock(monkeypatch)
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "waiting"},
                          headers={"Retry-After": "86400"}),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Poll Timeout Seconds": "600",
                           "Max Poll Interval Seconds": "10"}), poller_flowfile())
        assert res.relationship == "pending"
        assert slept and max(slept) == 10.0

    def test_retry_after_http_date_is_parsed(self):
        import email.utils
        import time as _time

        response = _FakeResponse(headers={
            "Retry-After": email.utils.formatdate(_time.time() + 8, usegmt=True)})
        wait = IQMJobPoller._retry_after_seconds(response, default=5, cap=60)
        assert 6.0 <= wait <= 9.0

    def test_rate_limit_is_retried_not_failed(self, monkeypatch):
        fake_clock(monkeypatch)
        _patch_http(monkeypatch, [
            _FakeResponse(status_code=429, headers={"Retry-After": "1"}),
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"01": 50, "11": 50})),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Poll Timeout Seconds": "30"}),
            poller_flowfile())
        assert res.relationship == "success"

    def test_failed_job_routes_to_failure_with_server_errors(self, monkeypatch):
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "failed",
                                   "errors": ["qubit QB3 is offline"]}),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok"}), poller_flowfile())
        assert res.relationship == "failure"
        assert res.attributes["hw.status"] == "failed"
        assert "qubit QB3 is offline" in res.attributes["hw.error"]

    def test_cancelled_job_routes_to_failure(self, monkeypatch):
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "cancelled"}),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok"}), poller_flowfile())
        assert res.relationship == "failure"
        assert res.attributes["hw.status"] == "cancelled"

    def test_http_error_routes_to_failure(self, monkeypatch):
        _patch_http(monkeypatch, [
            _FakeResponse(status_code=401, payload=None, text="invalid token"),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "bad"}), poller_flowfile())
        assert res.relationship == "failure"
        assert res.attributes["hw.http_status"] == "401"
        assert "invalid token" in res.attributes["hw.error"]

    def test_network_error_routes_to_failure(self, monkeypatch):
        def _boom(self, session, url, timeout=30):
            raise OSError("connection refused")

        monkeypatch.setattr(IQMJobPoller, "_get", _boom)
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok"}), poller_flowfile())
        assert res.relationship == "failure"
        assert "connection refused" in res.attributes["hw.error"]

    def test_missing_job_id_routes_to_failure(self, monkeypatch):
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok"}),
            MockFlowFile(content=b"{}", attributes={}))
        assert res.relationship == "failure"
        assert "hw.job_id" in res.attributes["hw.error"]

    def test_reverse_bit_order_property(self, monkeypatch):
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"01": 90, "00": 10})),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Reverse Bit Order": "true"}),
            poller_flowfile())
        assert json.loads(res.contents.decode("utf-8")) == {"10": 90, "00": 10}

    def test_bad_circuit_index_routes_to_failure(self, monkeypatch):
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"11": 100})),
        ])
        res = IQMJobPoller().transform(
            MockContext(**{"API Token": "tok", "Circuit Index": "3"}),
            poller_flowfile())
        assert res.relationship == "failure"
        assert "out of range" in res.attributes["hw.error"]

    def test_server_url_property_overrides_attribute(self, monkeypatch):
        calls = _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"11": 100})),
        ])
        IQMJobPoller().transform(
            MockContext(**{"API Token": "tok",
                           "Server URL": "https://iqm.example.org/"}),
            poller_flowfile())
        assert calls[0] == "https://iqm.example.org/api/v1/jobs/job-1234"

    def test_relationships_declared(self):
        names = {r.name for r in IQMJobPoller().getRelationships()}
        assert names == {"success", "pending", "failure"}


# ---------------------------------------------------------------------------
# End-to-end: submit -> poll -> report
# ---------------------------------------------------------------------------

class TestIQMChain:

    def test_device_to_poller_to_report(self, monkeypatch, tmp_path):
        from qrisp.interface import JobStatus
        from QuanifiReport import QuanifiReport
        from conftest import result_to_flowfile_merged

        _patch_backend(monkeypatch, _FakeJob([JobStatus.QUEUED], {"00": 1}))
        submitted = QrispIQMDevice().transform(
            MockContext(**{"Device Instance": "garnet", "API Token": "tok",
                           "Shots": "100", "Wait For Results": "false"}),
            qasm_flowfile())
        assert submitted.relationship == "success"

        ff = result_to_flowfile_merged(submitted, qasm_flowfile())
        _patch_http(monkeypatch, [
            _FakeResponse(payload={"status": "completed"}),
            _FakeResponse(payload=_counts_artifact({"00": 48, "11": 52})),
        ])
        polled = IQMJobPoller().transform(MockContext(**{"API Token": "tok"}), ff)
        assert polled.relationship == "success"

        report_ff = result_to_flowfile_merged(polled, ff)
        r = QuanifiReport().transform(
            MockContext(**{"Reports Directory": str(tmp_path),
                           "Flow Name": "iqm-chain"}), report_ff)
        assert r.relationship == "success"
        html = (tmp_path / "iqm-chain.html").read_text()
        assert "Hardware Execution" in html
        assert "iqm-resonance" in html
        assert "job-1234" in html
