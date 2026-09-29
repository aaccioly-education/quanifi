# Quanifi — quantum-computing components for Apache NiFi
# Copyright (C) 2026 Neilson Ramalho
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License, version 3, as published by
# the Free Software Foundation. This program is distributed WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the license along with this program; if not,
# see <https://www.gnu.org/licenses/>. Commercial licensing is also available:
# see COMMERCIAL.md at the repository root.

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.relationship import Relationship
from nifiapi.__jvm__ import JvmHolder

import batch_prep
from batch_prep import persist_manifest
from QuantumSuccessProbabilityOracle import required_shots

#: Submit modes. See QuantumIBMBatchSubmitter for why preflight is the default.
PREFLIGHT, ARMED = "preflight", "armed"

# Ordering used when interleaving the batch. Device calibration drifts during a
# job, so the kinds must be spread through it -- if all the replicates ran first
# and all the mutants last, the drift would land entirely on the mutants and be
# indistinguishable from a defect.
_KIND_CYCLE = ("null", "control", "mutant")


def interleave(entries):
    """Round-robin null / control / mutant so drift cannot bias one kind."""
    buckets = {}
    for entry in entries:
        kind = entry["kind"] if entry["kind"] in ("null", "control") else "mutant"
        buckets.setdefault(kind, []).append(entry)
    out = []
    while any(buckets.values()):
        for kind in _KIND_CYCLE:
            if buckets.get(kind):
                out.append(buckets[kind].pop(0))
    return out


#: Ranking used by ``case_major_order``. ``readout`` (the X-prepare baseline)
#: comes first, then ``control``, then ``null`` (the calibration replicate);
#: everything else -- mutants -- is not a named calibration kind and falls
#: through to rank 3. Copied from QuantumInspireBatchSubmitter rather than
#: imported -- see ``_parse_layout``'s docstring for why sibling imports are
#: unsafe here.
_KIND_RANK = {"readout": 0, "control": 1, "null": 2}


def case_major_order(entries):
    """Order a batch by test case first, builder second, kind third.

    ``interleave`` above spreads null/control/mutant round-robin so device
    calibration drift cannot bias one kind -- the right answer when the
    comparison of interest is control-vs-mutant, which is every existing use
    of this submitter (the arithmetic adder study). It is the wrong answer for
    the Grover N-M hardware matrix, whose primary comparison is
    builder-vs-builder *within one test case*: with every cell the same kind
    ("control"), interleaving by kind alone degenerates to arrival order, and
    arrival order is non-deterministic because the three builder branches run
    in parallel. Sorting on declared keys (the submitter-stamped
    ``group``/``member``) instead makes the layout reproducible regardless of
    which branch happened to finish first. Used only when a ``Batch Group
    Key`` is configured; blank keeps ``interleave()``, so every existing flow
    is unaffected.
    """
    def key(entry):
        kind = entry.get("kind") or ""
        return (entry.get("group") or "", entry.get("member") or "",
               _KIND_RANK.get(kind, 3), kind, entry.get("label") or "")
    return sorted(entries, key=key)


def _parse_layout(raw):
    """'122,144,124' -> [122, 144, 124]; blank/None -> None (search).

    Deliberately duplicated from QuantumIBMBatchSubmitter rather than imported.
    NiFi loads each processor module by file path, so a plain sibling import
    raises ModuleNotFoundError at load time and the processor is skipped with
    no UI feedback -- which is exactly what happened to QuantumMutator on
    2026-08-23. tests/test_iqm_fixed_layout.py asserts the two copies agree.
    """
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if not parts:
        return None
    if not all(p.isdigit() for p in parts):
        raise ValueError("Fixed Layout must be comma-separated integers, got %r" % (raw,))
    return [int(p) for p in parts]


def _discard_slot(path):
    """Remove a completed/failed batch slot so a retry starts cleanly."""
    try:
        os.remove(path)
    except OSError:
        pass


class QuantumIQMBatchSubmitter(FlowFileTransform):
    """
    Accumulates circuits and submits them to IQM Resonance as a SINGLE job,
    without waiting for the result.

    Why batching is mandatory, not an optimisation
    ----------------------------------------------
    * IQM bills per second with per-job overhead and caps at 100 circuits per
      job, so N jobs cost far more than one job of N circuits.
    * Device calibration drifts between jobs. A threshold calibrated from
      control replicates in job A does not describe a mutant submitted in job B,
      so controls, replicates and mutants must ride together.
    * One layout must serve the whole batch. Letting the transpiler choose per
      circuit would make the comparison measure qubit placement, not the defect.
      (Pinning to q0..qn-1 is worse still: 43 two-qubit gates on garnet where a
      searched layout needs 24.)

    Why it does not wait
    --------------------
    A synchronous submit-and-wait died after 1h14m on a queued garnet job with
    a connection reset -- the QPU time was spent and the results were lost. This
    processor submits and emits the job id plus a manifest; wire 'submitted'
    into QuantumBatchPoller so no NiFi thread is parked on a queue.

    Collection model
    ----------------
    The same disk-slot pattern as QuantumConsensusOracle: FlowFiles accumulate
    under Batch Label until Expected Circuits have arrived, then the batch is
    submitted on the last one.

    Output relationships
    --------------------
    submitted - job launched; body is the manifest, hw.job_id is set.
    waiting   - buffering; fewer than K circuits so far. Auto-terminate.
    failure   - submission failed or the input was malformed.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Accumulates OpenQASM circuits and submits them to IQM Resonance as a "
            "single job with one shared, searched qubit layout — required so that "
            "control replicates and mutants share a calibration snapshot, and so "
            "per-job cost is paid once. Does not block: emits the job id and a "
            "manifest for QuantumBatchPoller to collect."
        )
        tags = ["quantum", "iqm", "resonance", "hardware", "qpu", "batch", "submit"]
        # Its own venv, so the qiskit pin this brings in cannot disturb any other
        # processor -- NiFi installs dependencies per processor and version.
        dependencies = ["iqm-client[qiskit]>=35.0.0", "qiskit>=2.0.0,<2.5"]

    DEFAULT_SERVER = "https://resonance.iqm.tech"

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.batch_label = PropertyDescriptor(
            name="Batch Label",
            description=("Slot key (Expression Language). Every circuit of one "
                         "batch must produce the same key."),
            required=True, default_value="${test.run_id}",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.expected_circuits = PropertyDescriptor(
            name="Expected Circuits",
            description=("How many circuits form one batch before it is "
                         "submitted. IQM caps a job at 100."),
            required=True, default_value="16",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.device = PropertyDescriptor(
            name="Device",
            description=("IQM quantum computer: 'garnet', 'emerald', 'sirius', or "
                         "a ':mock' variant. Mocks are free but return RANDOM "
                         "counts, so they validate transport only."),
            required=True, default_value="garnet:mock",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.server_url = PropertyDescriptor(
            name="Server URL",
            description=("IQM server base URL. Note the CoCoS host that the "
                         "Resonance REST API still advertises is obsolete and "
                         "rejected by the SDK."),
            required=True, default_value="https://resonance.iqm.tech",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        )
        self.token = PropertyDescriptor(
            name="API Token",
            description="IQM Resonance API token. Use a sensitive parameter.",
            required=True, default_value="", sensitive=True,
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.shots = PropertyDescriptor(
            name="Shots",
            description="Shots per circuit (IQM allows up to 20000).",
            required=True, default_value="662",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        )
        self.circuit_kind = PropertyDescriptor(
            name="Circuit Kind",
            description=("Expression Language yielding 'null' for a calibration "
                         "replicate, 'control' for a correct version, or the "
                         "mutation operator name for a mutant."),
            required=True, default_value="${test.kind}",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.circuit_label = PropertyDescriptor(
            name="Circuit Label",
            description="Expression Language naming this circuit in the report.",
            required=False, default_value="${circuit.framework}",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.batch_group_key = PropertyDescriptor(
            name="Batch Group Key",
            description=(
                "Expression Language giving each circuit's case-major GROUP -- "
                "the primary sort key for case_major_order. Optional; blank "
                "(the default) keeps interleave() ordering, so every existing "
                "flow (the arithmetic adder study) is unaffected. Set together "
                "with Batch Member Key to switch this batch to case-major "
                "ordering, so every builder's circuit for one test case lands "
                "adjacent regardless of which branch happened to finish first."
            ),
            required=False, default_value="",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.batch_member_key = PropertyDescriptor(
            name="Batch Member Key",
            description=(
                "Expression Language giving each circuit's case-major MEMBER "
                "-- the secondary sort key, typically the builder identity. "
                "Optional; only consulted when Batch Group Key is also set."
            ),
            required=False, default_value="",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.fixed_layout = PropertyDescriptor(
            name="Fixed Layout",
            description=(
                "Comma-separated physical qubits to pin this batch to. Blank "
                "(the default) searches Layout Search Seeds transpiler seeds "
                "and keeps the best, which is the historical behaviour. Pin it "
                "whenever several jobs are to be compared with each other: on "
                "IBM, two jobs twelve minutes apart with independently searched "
                "layouts moved one implementation's control success from 0.78 "
                "to 0.16, which makes job-to-job variation uninterpretable. "
                "Must name exactly as many qubits as the padded batch width."
            ),
            required=False,
            default_value="",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.layout_seeds = PropertyDescriptor(
            name="Layout Search Seeds",
            description=("How many transpiler seeds to try when searching for a "
                         "layout. The best is reused for the whole batch."),
            required=True, default_value="8",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        )
        self.max_usage = PropertyDescriptor(
            name="Maximum Estimated Usage Seconds",
            description=(
                "Hard cap on estimated circuit execution time for this job. The "
                "estimate is gate duration times shots and omits reset, readout "
                "and queue overhead, so it is a FLOOR on what will be billed "
                "against your credits, not a ceiling. IQM does not expose a "
                "timing model on every backend; when duration is unavailable the "
                "estimate is 0 and this cap cannot protect you, which is why "
                "Maximum Circuits and Maximum Shots Per Circuit also exist."
            ),
            required=True, default_value="120",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        )
        self.max_circuits = PropertyDescriptor(
            name="Maximum Circuits",
            description="Refuse to submit a batch larger than this.",
            required=True, default_value="100",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        )
        self.max_shots = PropertyDescriptor(
            name="Maximum Shots Per Circuit",
            description="Refuse to submit if Shots exceeds this.",
            required=True, default_value="4096",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        )
        self.submit_mode = PropertyDescriptor(
            name="Submit Mode",
            description=(
                "preflight (default) transpiles, searches a layout, estimates "
                "usage and stops WITHOUT contacting the provider. armed submits "
                "and spends credits. The safe value is the default."
            ),
            required=True, default_value=PREFLIGHT,
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.allow_unreadable_credits = PropertyDescriptor(
            name="Allow Unreadable Credit Balance",
            description=(
                "IQM exposes no balance endpoint, so the balance is always "
                "unreadable; setting this true submits WITHOUT any credit "
                "check, and a batch may fail partway with 'Not enough "
                "credits to execute job' after some circuits have already "
                "run and been billed. Default false refuses to arm, which "
                "is the safe behaviour."
            ),
            required=True, default_value="false",
            allowable_values=["true", "false"],
        )
        self.attribute_prefix = PropertyDescriptor(
            name="Attribute Prefix",
            description=(
                "FlowFile attributes with this prefix are captured onto each "
                "batch entry and travel through to the polled result, so a "
                "returned circuit can be scored against its own ground truth. "
                "Blank captures nothing."
            ),
            required=False, default_value="arithmetic.",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.control_success = PropertyDescriptor(
            name="Control Success Probability",
            description=(
                "Expected success probability of a CORRECT circuit on this "
                "device, used by preflight to report the shots per arm the "
                "design needs. The default is measured, not optimistic."
            ),
            required=False, default_value="0.43",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.min_difference = PropertyDescriptor(
            name="Minimum Detectable Difference",
            description=(
                "Smallest drop in success probability the run must detect. Cost "
                "scales with the inverse square of this."
            ),
            required=False, default_value="0.10",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.state_dir = PropertyDescriptor(
            name="State Directory",
            description="Where partial batches are buffered between FlowFiles.",
            required=True,
            default_value="reports/tmp/quanifi_batch_state",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        )
        self.manifest_dir = PropertyDescriptor(
            name="Manifest Directory",
            description=("Where a submitted job's manifest is written, as "
                         "<job_id>.json. It is the only record of which circuit "
                         "produced which result."),
            required=True,
            default_value="reports/manifests",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        )
        self.slot_ttl = PropertyDescriptor(
            name="Slot TTL Seconds",
            description=(
                "Discard a partially filled slot older than this and start "
                "fresh. Guards against a branch that never arrives, which would "
                "otherwise buffer forever and silently swallow every later run. "
                "0 disables the check."
            ),
            required=True,
            default_value="3600",
            validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
        )
        self.descriptors = [
            self.slot_ttl,
            self.batch_label, self.expected_circuits, self.device, self.server_url,
            self.token, self.shots, self.circuit_kind, self.circuit_label,
            self.batch_group_key, self.batch_member_key,
            self.fixed_layout,
            self.layout_seeds, self.max_usage, self.max_circuits, self.max_shots,
            self.submit_mode, self.allow_unreadable_credits,
            self.attribute_prefix, self.control_success,
            self.min_difference, self.state_dir, self.manifest_dir,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def getRelationships(self):
        return [
            Relationship(name="submitted", description="Job launched; manifest emitted.",
                         auto_terminated=False),
            Relationship(name="waiting", description="Buffering; <K circuits so far.",
                         auto_terminated=False),
            Relationship(name="preflight",
                         description="Batch costed but NOT submitted.",
                         auto_terminated=False),
            Relationship(name="failure", description="Submission failed or bad input.",
                         auto_terminated=False),
        ]

    def _slot_path(self, state_dir, label):
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
        return os.path.join(state_dir, "%s.json" % safe)

    def _load_slot(self, path, ttl_seconds=0):
        """Load a partial slot, discarding it if it has gone stale.

        Without this a slot buffers forever: if one branch fails or is never
        sent, the Kth FlowFile never arrives, the batch never fires, and every
        later run joins the same orphaned slot and is silently swallowed. A TTL
        makes the failure self-healing -- a stale slot is dropped and the next
        arrival starts a fresh one.
        """
        try:
            if ttl_seconds > 0 and os.path.exists(path):
                age = time.time() - os.path.getmtime(path)
                if age > ttl_seconds:
                    self.logger.warn(
                        "{}: discarding slot {} -- {:.0f}s old, past the {}s TTL; "
                        "a branch never arrived".format(
                            type(self).__name__, os.path.basename(path),
                            age, ttl_seconds))
                    os.remove(path)
                    return {"entries": []}
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, ValueError, OSError):
            return {"entries": []}

    def backend_for(self, server, device, token):
        """Resolve the IQM backend. Reads device metadata only; submits nothing."""
        from iqm.qiskit_iqm import IQMProvider  # noqa: PLC0415

        # Assignment, not setdefault. The Python controller process is
        # long-lived and shared across invocations, so one earlier call with a
        # blank token would pin IQM_TOKEN="" for the life of the process and
        # then silently ignore every real token supplied afterwards -- the
        # failure looks exactly like a wrong credential, and restarting NiFi is
        # the only thing that clears it. Fall back to whatever is already in
        # the environment when no token is configured on the processor.
        if token:
            os.environ["IQM_TOKEN"] = token
        return IQMProvider(server, quantum_computer=device).get_backend()

    def prepare(self, qasms, shots, device, server, token, seeds, max_usage,
                fixed_layout=None, metadata=None, excluded_physical_qubits=(),
                prior_layouts=()):
        """Everything up to submission: pad, lay out, transpile, cost.

        Separated from the run call so preflight can produce the whole cost
        model without spending a credit. IQM has no local simulator, so this
        boundary is the only offline-verifiable part of the lane.
        """
        from qiskit import transpile  # noqa: PLC0415

        backend = self.backend_for(server, device, token)

        # Pad before layout search: one layout must serve the whole batch, and
        # the arithmetic adders differ in width. See batch_prep.
        circuits, widths, target = batch_prep.pad_batch(
            batch_prep.circuits_from_qasm(qasms))
        capacity = int(getattr(backend, "num_qubits", 0) or 0)
        if capacity and target > capacity:
            raise RuntimeError(
                "batch needs {} qubits after padding; {} has {}".format(
                    target, device, capacity))

        if fixed_layout:
            if len(fixed_layout) != target:
                raise RuntimeError(
                    "Fixed Layout names {} qubits but the padded batch is {} "
                    "wide".format(len(fixed_layout), target))
            if capacity and max(fixed_layout) >= capacity:
                raise RuntimeError(
                    "Fixed Layout names qubit {} but {} has {}".format(
                        max(fixed_layout), device, capacity))
            if len(set(fixed_layout)) != len(fixed_layout):
                raise RuntimeError("Fixed Layout repeats a physical qubit")
            layout = list(fixed_layout)
            # Count two-qubit gates the same way the search does, so the
            # reported metric means the same thing either way.
            evidence = batch_prep.pinned_layout_evidence(
                circuits, backend, layout, widths, metadata=metadata)
            two_q = max(row["two_qubit_gates"]
                        for row in evidence["winning_representatives"])
        else:
            two_q, layout, evidence = batch_prep.balanced_layout_for(
                circuits, backend, seeds, widths, metadata=metadata,
                excluded_physical_qubits=excluded_physical_qubits,
                prior_layouts=prior_layouts)
        transpiled = transpile(circuits, backend=backend, optimization_level=3,
                               initial_layout=layout, seed_transpiler=11)
        profile_metadata = (list(metadata) if metadata is not None
                            else [{} for _ in transpiled])
        profiles = [batch_prep._profile_transpiled(circuit, backend, attrs)
                    for circuit, attrs in zip(transpiled, profile_metadata)]
        execution_profile = batch_prep._result_profile(
            profiles, profile_metadata, excluded_physical_qubits)
        execution_profile["result_mapping_complete"] = (
            all(profile["result_mapping_complete"] for profile in profiles)
            if metadata is not None else None)
        execution_profile["source"] = "final-submission-transpilation"
        evidence.update(execution_profile)
        if execution_profile["excluded_active_hits"]:
            raise RuntimeError(
                "final transpiled circuits touch excluded physical qubit(s): {}"
                .format(execution_profile["excluded_active_hits"]))
        if metadata is not None and not execution_profile["result_mapping_complete"]:
            raise RuntimeError("final transpiled result measurement map is incomplete")
        dt = float(getattr(backend, "dt", 0) or 0)
        estimated_usage = sum(float(getattr(c, "duration", 0) or 0) * dt * shots
                              for c in transpiled)
        if max_usage and estimated_usage > max_usage:
            raise RuntimeError("estimated QPU usage {:.2f}s exceeds {:.2f}s cap".format(
                estimated_usage, max_usage))
        # Per-builder two-qubit count is this study's independent variable, so
        # it is recorded per circuit, not just as the batch-wide worst case
        # already captured in ``two_qubit_gates`` above.
        isa_profile = batch_prep.isa_profile(transpiled)
        return {"backend": backend, "transpiled": transpiled, "layout": layout,
                "two_qubit_gates": two_q, "estimated_usage": estimated_usage,
                "layout_evidence": evidence,
                "widths": widths, "padded_width": target, "capacity": capacity,
                "isa_profile": isa_profile,
                "provenance": batch_prep.transpiler_provenance(
                    backend, seed_transpiler=11, optimization_level=3)}


    @staticmethod
    def serialize_isa(plan, expected):
        """Serialize exactly what IQM will receive, before spending."""
        from qiskit import qasm2  # noqa: PLC0415

        values = [qasm2.dumps(circuit) for circuit in plan["transpiled"]]
        if len(values) != expected:
            raise ValueError("ISA archive length differs from batch")
        if any(not isinstance(value, str) or "OPENQASM" not in value
               for value in values):
            raise ValueError("ISA serialization did not produce OpenQASM")
        return values

    def run_job(self, plan, shots):
        """The one call that spends credits."""
        job = plan["backend"].run(plan["transpiled"], shots=shots)
        return str(job.job_id())

    def read_provider_credits(self, server, token):
        """Best-effort account credit balance, for the preflight report.

        Checked against ``experiments/iqm_probe.py`` -- the only place this
        codebase has read a real IQM account -- and against iqm-client 35.0.0
        / qiskit-iqm: neither exposes a credit or balance endpoint. IQMClient
        only offers ``get_health``/``get_about``/job/calibration calls, and
        the probe itself never reads credits, only job status and counts.
        Resonance's REST API publishes no documented endpoint for this
        either. So this deliberately returns ``None`` ("unreadable") rather
        than guessing at an undocumented URL. The v5 campaign died on "Not
        enough credits to execute job" with no warning beforehand; refusing
        to arm on an unreadable balance (see ``transform``) is the safest
        response available until IQM documents a real endpoint, at which
        point it belongs here -- the call site already refuses to arm on
        ``None``.
        """
        return None


    def submit(self, qasms, shots, device, server, token, seeds, max_usage=0,
               fixed_layout=None):
        """Prepare and submit. Kept for callers that always intended to spend."""
        plan = self.prepare(qasms, shots, device, server, token, seeds, max_usage,
                            fixed_layout)
        return (self.run_job(plan, shots), plan["two_qubit_gates"],
                [int(q) for q in plan["layout"]])

    def transform(self, context, flowFile):
        def get(prop):
            return context.getProperty(prop).evaluateAttributeExpressions(flowFile).getValue()

        state_dir = context.getProperty(self.state_dir).getValue()
        label = get(self.batch_label)
        try:
            k = int(get(self.expected_circuits))
        except (TypeError, ValueError):
            k = 16
        kind = (get(self.circuit_kind) or "").strip() or "control"
        name = (get(self.circuit_label) or "").strip()
        group_key = (get(self.batch_group_key) or "").strip()
        member_key = (get(self.batch_member_key) or "").strip()

        raw = bytes(flowFile.getContentsAsBytes())
        try:
            qasm = raw.decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            return FlowFileTransformResult(relationship="failure", contents=raw,
                                           attributes={"batch.error": str(exc)})
        if "OPENQASM" not in qasm:
            msg = "body is not OpenQASM; this processor consumes circuits"
            self.logger.error("QuantumIQMBatchSubmitter: {}".format(msg))
            return FlowFileTransformResult(relationship="failure", contents=raw,
                                           attributes={"batch.error": msg})

        os.makedirs(state_dir, exist_ok=True)
        slot_path = self._slot_path(state_dir, label)
        try:
            ttl = int(context.getProperty(self.slot_ttl).getValue())
        except (TypeError, ValueError):
            ttl = 3600
        slot = self._load_slot(slot_path, ttl_seconds=ttl)
        if not name:
            name = "circuit-%d" % (len(slot["entries"]) + 1)
        entry = {"label": name, "kind": kind, "qasm": qasm,
                 "group": group_key, "member": member_key}
        # Ground truth rides with the circuit, or the polled result is unscorable.
        captured = batch_prep.captured_attributes(
            flowFile.getAttributes(), get(self.attribute_prefix) or "")
        if captured:
            entry["attributes"] = captured
        slot["entries"].append(entry)

        if len(slot["entries"]) < k:
            with open(slot_path, "w", encoding="utf-8") as handle:
                json.dump(slot, handle)
            self.logger.warn(
                "QuantumIQMBatchSubmitter [{}]: buffered '{}' ({}/{}).".format(
                    label, name, len(slot["entries"]), k))
            return FlowFileTransformResult(
                relationship="waiting", contents=raw,
                attributes={"batch.status": "waiting", "batch.label": label,
                            "batch.have": str(len(slot["entries"])),
                            "batch.need": str(k)})
        if len(slot["entries"]) != k:
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="failure", contents=raw,
                attributes={"batch.error": "slot exceeded Expected Circuits",
                            "batch.label": label})

        # When a Batch Group Key is configured, the comparison of interest is
        # builder-vs-builder within a test case, so case_major_order is used
        # in its place -- blank keeps interleave() unchanged for every
        # existing flow (the arithmetic adder study).
        ordered = (case_major_order(slot["entries"]) if group_key
                  else interleave(slot["entries"]))
        device = get(self.device)
        server = context.getProperty(self.server_url).getValue()
        token = get(self.token) or ""
        shots = int(context.getProperty(self.shots).getValue())
        seeds = int(context.getProperty(self.layout_seeds).getValue())

        mode = (get(self.submit_mode) or PREFLIGHT).strip().lower()
        # Reported at preflight so Checkpoint 3 can see it before arming, and
        # enforced only when actually arming: the v5 campaign died on "Not
        # enough credits to execute job" with no earlier warning.
        credits = self.read_provider_credits(server, token)
        credits_display = "unreadable" if credits is None else str(credits)

        guard = self._check_guards(context, get, ordered, shots)
        if guard is not None:
            self.logger.error("QuantumIQMBatchSubmitter: {}".format(guard))
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="failure", contents=raw,
                attributes={"batch.error": guard, "batch.label": label})

        try:
            max_usage = float(context.getProperty(self.max_usage).getValue())
        except (TypeError, ValueError):
            max_usage = 0.0
        try:
            plan = self.prepare([e["qasm"] for e in ordered], shots, device,
                                server, token, seeds, max_usage,
                                _parse_layout(get(self.fixed_layout)))
        except Exception as exc:  # noqa: BLE001 - surface any SDK/API failure
            self.logger.error("QuantumIQMBatchSubmitter: preparation failed: {}".format(exc))
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="failure", contents=raw,
                attributes={"batch.error": "preparation failed: {}".format(exc),
                            "batch.label": label})

        try:
            batch_prep.attach_layout_attempt(
                plan.setdefault("layout_evidence", {}), ordered)
        except Exception as exc:  # noqa: BLE001 - provenance must fail closed
            self.logger.error(
                "QuantumIQMBatchSubmitter: layout provenance failed: {}".format(exc))
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="failure", contents=raw,
                attributes={"batch.error": "layout provenance failed: {}".format(exc),
                            "batch.label": label})

        two_q, layout = plan["two_qubit_gates"], [int(q) for q in plan["layout"]]
        sizing = self._sizing_attributes(get, shots)
        entries_out = self._manifest_entries(ordered, plan)
        provenance = plan.get("provenance") or {}
        # Per-builder routed count, submission order, as a JSON list on the
        # relationship itself -- Checkpoint 3 reads this to decide whether a
        # 4-qubit builder is already at the noise floor before arming.
        isa_two_qubit_gates_json = json.dumps(
            [row.get("isa_two_qubit_gates") for row in entries_out])

        noise_floor_risk = any(
            w == 4 and (row.get("isa_two_qubit_gates") or 0) > 200
            for w, row in zip(plan.get("widths", []), entries_out))

        if mode != ARMED:
            # No credit has been spent and none will be. A new trigger creates a
            # new run id, so keeping this completed slot would only block retry.
            report = dict(provenance,
                          job_id=None, device=device, shots=shots,
                          server=server, layout=layout,
                          layout_evidence=plan.get("layout_evidence", {}),
                          padded_width=plan["padded_width"],
                          estimated_usage_seconds=plan["estimated_usage"],
                          submit_mode=PREFLIGHT,
                          entries=entries_out)
            attrs = {"batch.status": "preflight", "batch.submitted": "false",
                     "batch.label": label, "batch.device": device,
                     "batch.size": str(len(ordered)), "batch.shots": str(shots),
                     "batch.two_qubit_gates": str(two_q),
                     "batch.isa_two_qubit_gates": isa_two_qubit_gates_json,
                     "batch.layout": ",".join(str(q) for q in layout),
                     "batch.padded_width": str(plan["padded_width"]),
                     "batch.circuit_widths": ",".join(str(w) for w in plan["widths"]),
                     "batch.device_qubits": str(plan["capacity"]),
                     "batch.estimated_usage_seconds": "{:.6f}".format(plan["estimated_usage"]),
                     "batch.provider_credits": credits_display,
                     "hw.provider": "iqm-resonance", "hw.server_url": server}
            if noise_floor_risk:
                attrs["batch.noise_floor_risk"] = "true"
            attrs.update(sizing)
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="preflight",
                contents=json.dumps(report, indent=2).encode("utf-8"),
                attributes=attrs)

        allow_unreadable_credits = (
            context.getProperty(self.allow_unreadable_credits).getValue()
            or "false").strip().lower() == "true"
        credit_check_skipped = "false"
        if credits is None:
            if not allow_unreadable_credits:
                self.logger.error(
                    "QuantumIQMBatchSubmitter: provider credit balance is "
                    "unreadable; refusing to arm")
                _discard_slot(slot_path)
                return FlowFileTransformResult(
                    relationship="failure", contents=raw,
                    attributes={"batch.error": ("IQM provider credit balance is "
                                                "unreadable; refusing to arm"),
                                "batch.provider_credits": "unreadable",
                                "batch.label": label})
            self.logger.warn(
                "QuantumIQMBatchSubmitter: provider credit balance is "
                "unreadable; credit check explicitly overridden, "
                "submitting anyway")
            credit_check_skipped = "true"

        try:
            job_id = self.run_job(plan, shots)
        except Exception as exc:  # noqa: BLE001 - surface any SDK/API failure
            self.logger.error("QuantumIQMBatchSubmitter: submit failed: {}".format(exc))
            _discard_slot(slot_path)
            return FlowFileTransformResult(
                relationship="failure", contents=raw,
                attributes={"batch.error": "submit failed: {}".format(exc),
                            "batch.label": label})
        _discard_slot(slot_path)

        # The manifest carries label+kind in submission order; the poller merges
        # counts back onto it by index. QASM is dropped from the FlowFile copy --
        # it would bloat every downstream FlowFile -- but not from the archived
        # one: "reproducible from the flow" holds only while the flow is
        # unchanged, and a mutation campaign changes it by design.
        manifest = dict(provenance,
                        job_id=job_id, device=device, shots=shots,
                        server=server, layout=layout,
                        layout_evidence=plan.get("layout_evidence", {}),
                        padded_width=plan["padded_width"],
                        estimated_usage_seconds=plan["estimated_usage"],
                        entries=entries_out)
        written, manifest_error = persist_manifest(
            context.getProperty(self.manifest_dir).getValue(), manifest,
            [e["qasm"] for e in ordered])
        attrs = {
            "batch.manifest_path": written or "",
            "batch.manifest_error": manifest_error or "",
            "batch.status": "submitted", "batch.submitted": "true",
            "batch.label": label,
            "batch.size": str(len(ordered)), "batch.device": device,
            "batch.job_id": job_id, "batch.shots": str(shots),
            "batch.two_qubit_gates": str(two_q),
            "batch.isa_two_qubit_gates": isa_two_qubit_gates_json,
            "batch.layout": ",".join(str(q) for q in layout),
            "batch.padded_width": str(plan["padded_width"]),
            "batch.circuit_widths": ",".join(str(w) for w in plan["widths"]),
            "batch.device_qubits": str(plan["capacity"]),
            "batch.estimated_usage_seconds": "{:.6f}".format(plan["estimated_usage"]),
            "batch.provider_credits": credits_display,
            "batch.credit_check_skipped": credit_check_skipped,
            # hw.* mirrors the vocabulary IQMJobPoller already uses.
            "hw.job_id": job_id, "hw.provider": "iqm-resonance",
            "hw.server_url": server,
        }
        if noise_floor_risk:
            attrs["batch.noise_floor_risk"] = "true"
        attrs.update(sizing)
        if device.endswith(":mock"):
            attrs["batch.warning"] = ("mock device: counts are RANDOM, not "
                                      "simulated; transport validation only")
        self.logger.warn(
            "QuantumIQMBatchSubmitter [{}]: submitted {} circuits to {} as job {} "
            "({} two-qubit gates, layout {})".format(
                label, len(ordered), device, job_id, two_q, layout))
        return FlowFileTransformResult(
            relationship="submitted",
            contents=json.dumps(manifest, indent=2).encode("utf-8"),
            attributes=attrs)

    # -- helpers -------------------------------------------------------------

    def _check_guards(self, context, get, ordered, shots):
        """Budget guards, evaluated before anything touches IQM."""
        try:
            max_circuits = int(context.getProperty(self.max_circuits).getValue())
            max_shots = int(context.getProperty(self.max_shots).getValue())
        except (TypeError, ValueError) as exc:
            return "non-numeric guard setting: {}".format(exc)
        if len(ordered) > max_circuits:
            return "batch has {} circuits; Maximum Circuits is {}".format(
                len(ordered), max_circuits)
        if shots > max_shots:
            return "requested {} shots; Maximum Shots Per Circuit is {}".format(
                shots, max_shots)
        return None

    def _sizing_attributes(self, get, shots):
        """What the shot budget should have been, next to what it is."""
        try:
            p_ref = float(get(self.control_success))
            min_diff = float(get(self.min_difference))
            needed = required_shots(p_ref, min_diff)
        except (TypeError, ValueError):
            return {}
        return {"batch.control_success_probability": "{:.4f}".format(p_ref),
                "batch.minimum_detectable_difference": "{:.4f}".format(min_diff),
                "batch.required_shots_per_arm": str(needed),
                "batch.shots_are_sufficient": "true" if shots >= needed else "false"}

    @staticmethod
    def _manifest_entries(ordered, plan):
        """Self-describing entries: enough to score a result on its own."""
        rows = batch_prep.manifest_entries(ordered, plan["widths"])
        return batch_prep.merge_isa_profile(rows, plan.get("isa_profile"))
