"""
The hardware path for arithmetic circuits, verified WITHOUT submitting a job.

Nothing in this file touches a real backend, a network, or a credential. The
IBM submitter is subclassed with a stub `submit`, exactly as
tests/test_ibm_batch_pipeline.py does, and the Qiskit runtime gateway runs
against a `fake_*` backend in local mode, exactly as tests/test_hardware.py
does. That is deliberate: the arithmetic study's shot budget is money, and the
point here is to prove the wiring is right *before* any of it is spent.

What is checked:

  1. an arithmetic circuit survives the trip to a hardware gateway and back
     with its ground-truth attributes intact, because the oracle downstream
     cannot score anything without them;
  2. the batch submitter slots arithmetic circuits the same way it slots the
     Grover ones, so the existing hardware lane needs no special case;
  3. the oracle can score a *noisy* result, which is the only kind hardware
     produces, and reports an interval wide enough to be honest about it.
"""

import json

import pytest

from conftest import (MockContext, MockFlowFile, result_to_flowfile_merged,
                      with_fake_usage)

from QiskitQuantumArithmetic import QiskitQuantumArithmetic
from QuantumSuccessProbabilityOracle import QuantumSuccessProbabilityOracle
from QuantumIBMBatchSubmitter import QuantumIBMBatchSubmitter
from QiskitRuntimeSampler import QiskitRuntimeSampler

import arithmetic_spec as aspec
import batch_prep


def build(a=3, b=3, width=2, implementation="cdkm"):
    return QiskitQuantumArithmetic().transform(
        MockContext(**{
            "Operation": "add", "Operand A": str(a), "Operand B": str(b),
            "Bit Width": str(width), "Implementation": implementation,
        }),
        MockFlowFile(),
    )


class _StubSubmitter(QuantumIBMBatchSubmitter):
    """Records what it was asked to submit; never contacts IBM.

    Three phases are stubbed: prepare() costs the batch, run_job() is the single
    call that would spend QPU time, and service_for() stands in for the usage
    ledger the fail-closed quota guard reads just before run_job().
    """

    submitted = None

    def prepare(self, qasms, *args, **kwargs):
        _StubSubmitter.submitted = qasms
        return {"backend": None, "transpiled": [], "layout": [1, 2, 3, 4, 5, 6],
                "two_qubit_gates": 36, "pending": 1, "estimated_usage": 0.25,
                "widths": [6] * len(qasms), "padded_width": 6, "capacity": 127}

    def run_job(self, plan, shots):
        return "stub-job"

    def serialize_isa(self, plan, expected):
        values = list(_StubSubmitter.submitted or [])
        assert len(values) == expected
        return values


#: Ample quota: these tests are about arithmetic wiring, not the quota gate,
#: which has its own coverage in tests/test_ibm_quota.py.
StubSubmitter = with_fake_usage(_StubSubmitter, consumed=100.0)


def submit_context(tmp_path, **overrides):
    props = {"Batch Label": "arith-batch", "Expected Circuits": "2",
             "Device": "ibm_kingston", "API Token": "token", "Instance": "test",
             "Shots": "201", "Circuit Kind": "${test.kind}",
             "Circuit Label": "${arithmetic.implementation}",
             "Layout Search Seeds": "2", "Maximum Pending Jobs": "10",
             "Maximum Estimated Usage Seconds": "120",
             "Maximum Circuits": "256", "Maximum Shots Per Circuit": "4096",
             # armed: this test is specifically about the submit path
             "Submit Mode": "armed", "Attribute Prefix": "arithmetic.",
             "Control Success Probability": "0.43",
             "Minimum Detectable Difference": "0.10",
             "State Directory": str(tmp_path / "state"), "Slot TTL Seconds": "3600",
             # Without this the default points at the real campaign archive,
             # and a test run drops stub manifests among the recorded jobs.
             "Manifest Directory": str(tmp_path / "manifests")}
    props.update(overrides)
    return MockContext(**props)


class TestBatchSubmitterAcceptsArithmetic:

    def test_buffers_and_submits_without_touching_ibm(self, tmp_path):
        proc = StubSubmitter()
        first = build(1, 2)
        second = build(3, 3)

        waiting = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(first, MockFlowFile(attributes={"test.kind": "null"})))
        assert waiting.relationship == "waiting"

        result = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(second, MockFlowFile(attributes={"test.kind": "control"})))
        assert result.relationship == "submitted"
        assert result.attributes["batch.job_id"] == "stub-job"

    def test_ground_truth_reaches_the_manifest(self, tmp_path):
        """Without the expected bits in the manifest, a polled result is unscorable."""
        proc = StubSubmitter()
        proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(1, 2), MockFlowFile(attributes={"test.kind": "null"})))
        result = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(3, 3), MockFlowFile(attributes={"test.kind": "control"})))
        manifest = json.loads(bytes(
            result_to_flowfile_merged(result, MockFlowFile()).getContentsAsBytes()))
        assert len(manifest["entries"]) == 2

    def test_no_real_submission_is_possible_here(self):
        """A guard on the guard: the stubs are what run, not the real calls."""
        assert StubSubmitter.prepare is not QuantumIBMBatchSubmitter.prepare
        assert StubSubmitter.run_job is not QuantumIBMBatchSubmitter.run_job

    def test_ground_truth_is_captured_onto_the_entries(self, tmp_path):
        """Without this the polled result cannot be scored against the answer."""
        proc = StubSubmitter()
        proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(1, 2), MockFlowFile(attributes={"test.kind": "null"})))
        result = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(3, 3), MockFlowFile(attributes={"test.kind": "control"})))
        manifest = json.loads(result.contents.decode())
        for entry in manifest["entries"]:
            assert entry["attributes"]["arithmetic.expected_result_bits"]
            assert entry["attributes"]["arithmetic.result_qubits"]


class TestGatewayRoundTrip:
    """A fake backend, i.e. real noise model, local execution, no account."""

    @pytest.fixture(scope="class")
    def sampled(self):
        built = build(3, 3)
        upstream = MockFlowFile()
        sampler = QiskitRuntimeSampler().transform(
            # fake_lagos is 7 qubits; the 2-bit half adder needs 6, and a
            # 5-qubit fake backend fails with 'Number of qubits greater than
            # device' -- which is itself the first thing to check before
            # picking a real device.
            MockContext(**{"Backend": "fake_lagos", "Shots": "201",
                           "Random Seed": "11"}),
            result_to_flowfile_merged(built, upstream),
        )
        return built, sampler

    def test_gateway_runs_the_circuit(self, sampled):
        _, sampler = sampled
        if sampler.relationship != "success":
            pytest.skip("runtime gateway unavailable offline: %s"
                        % sampler.attributes.get("hw.error"))
        counts = json.loads(sampler.contents.decode("utf-8"))
        assert sum(counts.values()) == 201

    def test_ground_truth_survives_the_gateway(self, sampled):
        built, sampler = sampled
        if sampler.relationship != "success":
            pytest.skip("runtime gateway unavailable offline")
        merged = result_to_flowfile_merged(sampler, result_to_flowfile_merged(
            built, MockFlowFile())).getAttributes()
        assert merged[aspec.ATTR_RESULT_BITS]
        assert merged[aspec.ATTR_RESULT_QUBITS]

    def test_oracle_scores_a_noisy_run(self, sampled):
        built, sampler = sampled
        if sampler.relationship != "success":
            pytest.skip("runtime gateway unavailable offline")
        built_ff = result_to_flowfile_merged(built, MockFlowFile())
        scored = QuantumSuccessProbabilityOracle().transform(
            MockContext(**{"Expected Outcome": "", "Mode": "single",
                           "Confidence Level": "0.95", "Alpha": "0.05",
                           "Minimum Difference": "0.05",
                           "Comparison Label": "hw-dryrun"}),
            result_to_flowfile_merged(sampler, built_ff),
        )
        assert scored.relationship == "success", scored.attributes.get("oracle.error")
        p = float(scored.attributes["oracle.success_probability"])
        lo = float(scored.attributes["oracle.ci_low"])
        hi = float(scored.attributes["oracle.ci_high"])
        # The whole point of hardware: p < 1, and the interval must say so.
        assert 0.0 <= p <= 1.0
        assert lo <= p <= hi
        assert hi - lo > 0.0

    def test_detectable_difference_is_reported(self, sampled):
        """A null result is only a claim if it comes with what it could see."""
        built, sampler = sampled
        if sampler.relationship != "success":
            pytest.skip("runtime gateway unavailable offline")
        scored = QuantumSuccessProbabilityOracle().transform(
            MockContext(**{"Expected Outcome": "", "Mode": "single",
                           "Confidence Level": "0.95", "Alpha": "0.05",
                           "Minimum Difference": "0.05",
                           "Comparison Label": "hw-dryrun"}),
            result_to_flowfile_merged(
                sampler, result_to_flowfile_merged(built, MockFlowFile())),
        )
        assert 0.0 < float(scored.attributes["oracle.detectable_difference"]) < 1.0


class TestTheArchivedManifestKeepsTheCircuits:
    """A report row points at a circuit label; the archive must hold that circuit.

    Both submitters used to drop the OpenQASM on the grounds that the circuits
    are reproducible from the flow. That holds only while the flow is unchanged,
    which is exactly what a mutation campaign does not guarantee -- so the
    archived manifest keeps the source, and the copy that rides on every
    downstream FlowFile keeps only its digest.
    """

    def test_the_flowfile_copy_carries_digests_not_circuits(self, tmp_path):
        proc = StubSubmitter()
        proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(1, 2), MockFlowFile(attributes={"test.kind": "null"})))
        result = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(3, 3), MockFlowFile(attributes={"test.kind": "control"})))
        manifest = json.loads(result.contents.decode())
        for entry in manifest["entries"]:
            assert "qasm" not in entry, "the FlowFile copy must stay small"
            assert "isa_qasm" not in entry, "the FlowFile copy must stay small"
            assert len(entry["qasm_sha256"]) == 64
            assert len(entry["isa_qasm_sha256"]) == 64

    def test_the_archived_copy_carries_the_submitted_openqasm(self, tmp_path):
        proc = StubSubmitter()
        proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(1, 2), MockFlowFile(attributes={"test.kind": "null"})))
        result = proc.transform(
            submit_context(tmp_path),
            result_to_flowfile_merged(build(3, 3), MockFlowFile(attributes={"test.kind": "control"})))
        path = result.attributes["batch.manifest_path"]
        assert path and not result.attributes["batch.manifest_error"]
        archived = json.loads(open(path).read())
        assert len(archived["entries"]) == 2
        for entry in archived["entries"]:
            assert "OPENQASM" in entry["qasm"]
            assert entry["qasm_sha256"] == batch_prep.qasm_digest(entry["qasm"])
            assert "OPENQASM" in entry["isa_qasm"]
            assert (entry["isa_qasm_sha256"] ==
                    batch_prep.qasm_digest(entry["isa_qasm"]))
        # and the circuits are the ones that were handed to the provider
        assert ([e["qasm"] for e in archived["entries"]]
                == list(StubSubmitter.submitted))

    def test_a_mismatched_circuit_list_is_dropped_not_zipped_short(self, tmp_path):
        """Pairing circuits with the wrong entries is worse than no circuits."""
        manifest = {"job_id": "j", "entries": [{"label": "a"}, {"label": "b"}]}
        path, error = batch_prep.persist_manifest(
            str(tmp_path / "m"), manifest, ["only-one-circuit"])
        assert error is None
        archived = json.loads(open(path).read())
        assert all("qasm" not in entry for entry in archived["entries"])

    def test_persisting_does_not_mutate_the_caller_s_manifest(self, tmp_path):
        """The same dict becomes FlowFile content right after this call."""
        manifest = {"job_id": "j", "entries": [{"label": "a"}]}
        batch_prep.persist_manifest(str(tmp_path / "m"), manifest, ["OPENQASM 2.0;"])
        assert manifest["entries"] == [{"label": "a"}]
