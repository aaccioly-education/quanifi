"""Tests for the hardware gateways (BraketDevice, QiskitRuntimeSampler).

Everything here runs offline: BraketDevice with Device=local uses the Braket
LocalSimulator, and QiskitRuntimeSampler with a fake_* backend runs
qiskit-ibm-runtime's local mode with the backend's calibrated noise model.
The cloud paths are exercised by stubbing the provider entry points so no
network or credentials are ever needed.
"""
import json

import pytest

from conftest import MockContext, MockFlowFile

from BraketDevice import BraketDevice
from QiskitRuntimeSampler import QiskitRuntimeSampler

BELL_QASM2 = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
    "qreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\nmeasure q -> c;\n"
)


def bell_flowfile(fmt="qasm2"):
    return MockFlowFile(content=BELL_QASM2.encode(),
                        attributes={"circuit.format": fmt})


class TestBraketDevice:

    def test_local_device_runs_bell(self):
        res = BraketDevice().transform(
            MockContext(**{"Device": "local", "Shots": "1024"}), bell_flowfile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["hw.provider"] == "aws-braket"
        assert a["hw.device"] == "local"
        assert a["sim.framework"] == "braket"
        assert a["report.type"] == "simulation"
        counts = json.loads(res.contents.decode("utf-8"))
        assert set(counts) == {"00", "11"}

    def test_unsupported_format_routes_to_failure(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "qpy"})
        res = BraketDevice().transform(MockContext(), ff)
        assert res.relationship == "failure"
        assert "hw.error" in res.attributes

    def test_aws_error_routes_to_failure(self, monkeypatch):
        import braket.aws

        class _Boom:
            def __init__(self, arn):
                raise RuntimeError("no AWS credentials configured")

        monkeypatch.setattr(braket.aws, "AwsDevice", _Boom)
        res = BraketDevice().transform(
            MockContext(**{"Device": "arn:aws:braket:::device/qpu/ionq/Aria-1"}),
            bell_flowfile())
        assert res.relationship == "failure"
        assert "credentials" in res.attributes["hw.error"]

    def test_local_run_error_routes_to_failure(self, monkeypatch):
        import braket.devices

        class _Boom:
            def run(self, *args, **kwargs):
                raise RuntimeError("local simulator exploded")

        monkeypatch.setattr(braket.devices, "LocalSimulator", _Boom)
        res = BraketDevice().transform(
            MockContext(**{"Device": "local", "Shots": "64"}), bell_flowfile())
        assert res.relationship == "failure"
        assert "local simulator exploded" in res.attributes["hw.error"]


class TestQiskitRuntimeSampler:

    def test_fake_backend_runs_bell_locally(self):
        res = QiskitRuntimeSampler().transform(
            MockContext(**{"Backend": "fake_manila", "Shots": "2000"}),
            bell_flowfile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["hw.provider"] == "ibm-runtime"
        assert a["hw.mode"] == "local-fake"
        assert a["report.type"] == "simulation"
        counts = json.loads(res.contents.decode("utf-8"))
        # noisy backend: the Bell pair still dominates
        good = sum(v for k, v in counts.items() if k in ("00", "11"))
        assert good / 2000 > 0.8

    def test_unknown_fake_backend_routes_to_failure(self):
        res = QiskitRuntimeSampler().transform(
            MockContext(**{"Backend": "fake_nonexistent_qpu"}), bell_flowfile())
        assert res.relationship == "failure"
        assert "hw.error" in res.attributes

    def test_unsupported_format_routes_to_failure(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "cirq_json"})
        res = QiskitRuntimeSampler().transform(MockContext(), ff)
        assert res.relationship == "failure"
        assert "hw.error" in res.attributes

    def test_cloud_error_routes_to_failure(self, monkeypatch):
        import qiskit_ibm_runtime

        class _Boom:
            def __init__(self, *a, **k):
                raise RuntimeError("Unable to find account")

        monkeypatch.setattr(qiskit_ibm_runtime, "QiskitRuntimeService", _Boom)
        res = QiskitRuntimeSampler().transform(
            MockContext(**{"Backend": "ibm_brisbane"}), bell_flowfile())
        assert res.relationship == "failure"
        assert "hw.error" in res.attributes
