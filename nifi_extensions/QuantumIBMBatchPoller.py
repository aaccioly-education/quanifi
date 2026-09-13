import json
import time
from datetime import datetime, timezone

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.relationship import Relationship
from nifiapi.__jvm__ import JvmHolder

import os


def extract_counts(result, entries):
    """Merge job counts back onto the manifest, preserving everything on it.

    The manifest entry is the only place a circuit's ground truth survives the
    round trip, so anything the submitter recorded there (``attributes``,
    ``num_qubits``) is carried forward rather than dropped. Without it a polled
    result cannot be scored against the classical answer and the lane silently
    falls back to a distributional oracle.
    """
    if len(result) != len(entries):
        raise ValueError("result has {} circuits; manifest has {}".format(len(result), len(entries)))
    merged = []
    for pub, entry in zip(result, entries):
        data = pub.data
        name = "meas" if hasattr(data, "meas") else next(iter(data.keys()))
        counts = getattr(data, name).get_counts()
        row = {"label": entry.get("label"), "kind": entry.get("kind"),
               "counts": counts}
        if entry.get("attributes"):
            row["attributes"] = entry["attributes"]
        if entry.get("num_qubits") is not None:
            row["num_qubits"] = entry["num_qubits"]
        merged.append(row)
    return merged


#: IBM posts a job's final charge some time AFTER the status flips to DONE,
#: and the gap is longer than it first appears. Marrakesh validation job
#: dae17le42tqs73au2j5g finished at 13:06:52 and still had no posted charge
#: 16 seconds later, which is what a twelve-second window missed. Three
#: minutes at five-second intervals is generous against that observation and
#: costs nothing when the charge is already settled, which is the usual case.
#: The poller is already willing to wait hours for the result itself, so this
#: is not the slow part of a lane -- and an unposted charge strands a job that
#: has already been paid for.
USAGE_SETTLE_ATTEMPTS = 36
USAGE_SETTLE_INTERVAL_SECONDS = 5


def _read_usage(job):
    """One read of IBM's charge: (seconds, settled). "" when absent.

    ``settled`` reports whether IBM called the charge final.  A job flips to
    DONE slightly before its usage is posted, so an unsettled read is a "not
    yet", not an answer.
    """
    try:
        value = getattr(job, "metrics")
        metrics = value() if callable(value) else value
    except Exception:  # noqa: BLE001 - provider surface varies by version
        return "", False
    if not isinstance(metrics, dict):
        return "", False
    # Current Runtime responses nest the final charge under ``usage``;
    # older responses may expose it directly or under ``bss``.
    candidates = (metrics.get("usage"), metrics.get("bss"), metrics)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("quantum_seconds", "usage_seconds", "seconds"):
            if key in candidate:
                # Only ``usage`` carries a status; the legacy shapes have no
                # way to say "provisional", so a value there counts as final.
                settled = str(candidate.get("status", "complete")).lower() == "complete"
                return str(candidate[key]), settled
    return "", False


def _usage_seconds(job, attempts=1, interval=0):
    """QPU seconds IBM reports for a finished job, or "" when it reports none.

    Deliberately empty rather than 0 when unavailable: a zero would read as a
    free job and quietly corrupt a running budget total.  ``usage_estimation``
    is intentionally excluded: it is computed before execution and must never
    be relabelled as actual provider usage.

    IBM posts the final charge a moment *after* the job reports DONE, so a
    single read taken the instant the status flips comes back empty and the
    campaign's completion validator then rejects an otherwise perfect result.
    Re-read until the charge settles rather than accept that gap; returning ""
    stays the honest answer if it never does.
    """
    seconds = ""
    for attempt in range(max(1, attempts)):
        seconds, settled = _read_usage(job)
        if settled and seconds != "":
            return seconds
        if attempt + 1 < max(1, attempts) and interval > 0:
            time.sleep(interval)
    # Provisional-but-present beats nothing: the value is still IBM's own
    # completed-job metric, never the pre-run estimate.
    return seconds



def _secret(configured, *env_names):
    """Credential from the property, else the environment.

    Deliberately inlined rather than imported from batch_prep: NiFi does not put
    sibling extension modules on a *poller's* import path, and a module-level
    `import batch_prep` here fails with ModuleNotFoundError and marks the
    processor invalid. The submitters can import it; the pollers cannot.

    Blank is not an error. NiFi does not evaluate Expression Language on a
    sensitive property, so a canvas that stores `${IQM_TOKEN}` -- which is how a
    credential is kept out of flow.json.gz -- hands this an empty string, and
    the real value is in the process environment.
    """
    value = (configured or "").strip()
    if value:
        return value
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""

class QuantumIBMBatchPoller(FlowFileTransform):
    """Poll an IBM Runtime job without blocking and emit calibrated-oracle entries."""

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = "Polls an asynchronous IBM Runtime Sampler batch and emits all raw counts."
        tags = ["quantum", "ibm", "hardware", "batch", "polling"]
        dependencies = ["qiskit>=2.2,<2.5", "qiskit-ibm-runtime>=0.40,<1"]

    TERMINAL = {"DONE", "ERROR", "CANCELLED"}

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()
        self.token = PropertyDescriptor(name="API Token", description="IBM Quantum token.",
            required=True, default_value="",
            sensitive=True, expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES)
        self.instance = PropertyDescriptor(name="Instance", description="IBM service instance/plan.",
            required=False, default_value="test",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES)
        self.job_id = PropertyDescriptor(name="Job ID", description="IBM Runtime job identifier.",
            required=True,
            default_value="${batch.job_id}", validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES)
        self.poll_timeout = PropertyDescriptor(name="Poll Timeout Seconds",
            description="Maximum polling time for one invocation.", required=True,
            default_value="30", validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR])
        self.poll_interval = PropertyDescriptor(name="Poll Interval Seconds",
            description="Delay between status checks.", required=True,
            default_value="5", validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR])
        self.descriptors = [self.token, self.instance, self.job_id,
                            self.poll_timeout, self.poll_interval]

    def getPropertyDescriptors(self):
        return self.descriptors

    def getRelationships(self):
        return [Relationship(name="success", description="Counts fetched.",
                             auto_terminated=False),
                Relationship(name="pending", description="Job is not terminal.",
                             auto_terminated=False),
                Relationship(name="failure", description="Job or result failed.",
                             auto_terminated=False)]

    def service(self, token, instance):
        from qiskit_ibm_runtime import QiskitRuntimeService
        kwargs = {"channel": "ibm_quantum_platform", "token": token}
        if instance:
            kwargs["instance"] = instance
        return QiskitRuntimeService(**kwargs)

    def transform(self, context, flowFile):
        def get(prop):
            return context.getProperty(prop).evaluateAttributeExpressions(flowFile).getValue()
        raw = bytes(flowFile.getContentsAsBytes())
        try:
            manifest = json.loads(raw.decode())
            entries = manifest["entries"]
            job_id = (get(self.job_id) or manifest["job_id"]).strip()
        except Exception as exc:
            return FlowFileTransformResult(relationship="failure", contents=raw,
                attributes={"batch.error": "bad IBM batch manifest: {}".format(exc)})
        try:
            job = self.service(
                _secret(get(self.token), "IBM_QUANTUM_TOKEN"),
                _secret(get(self.instance), "IBM_QUANTUM_INSTANCE"),
            ).job(job_id)
            deadline = time.time() + int(get(self.poll_timeout))
            status = ""
            while True:
                value = job.status()
                status = str(getattr(value, "name", value)).upper()
                if status in self.TERMINAL or time.time() + int(get(self.poll_interval)) >= deadline:
                    break
                time.sleep(int(get(self.poll_interval)))
        except Exception as exc:
            return FlowFileTransformResult(relationship="pending", contents=raw,
                attributes={"batch.status": "unreachable", "batch.error": str(exc),
                            "batch.job_id": job_id})
        if status not in self.TERMINAL:
            return FlowFileTransformResult(relationship="pending", contents=raw,
                attributes={"batch.status": status.lower(), "batch.job_id": job_id})
        if status != "DONE":
            return FlowFileTransformResult(relationship="failure", contents=raw,
                attributes={"batch.status": status.lower(),
                            "batch.error": "IBM job {} ended {}".format(job_id, status)})
        try:
            merged = extract_counts(job.result(), entries)
        except Exception as exc:
            return FlowFileTransformResult(relationship="failure", contents=raw,
                attributes={"batch.error": "could not extract IBM counts: {}".format(exc)})
        actual_usage = _usage_seconds(
            job, attempts=USAGE_SETTLE_ATTEMPTS,
            interval=USAGE_SETTLE_INTERVAL_SECONDS)
        completed_at = datetime.now(timezone.utc).isoformat()
        out = {"entries": merged, "job_id": job_id, "device": manifest.get("device", ""),
               "layout": manifest.get("layout"),
               "padded_width": manifest.get("padded_width"),
               # Qiskit returns counts MSB-first (qubit 0 rightmost). The repo's
               # canonical order is q0_left. Recorded rather than converted here
               # so the existing GHZ consumers see byte-identical counts; the
               # expander does the conversion for anything that needs it.
               "bit_order": "q0_right",
               "estimated_usage_seconds": manifest.get("estimated_usage_seconds"),
               "actual_usage_seconds": actual_usage,
               "submitted_at": manifest.get("submitted_at"),
               "completed_at": completed_at}
        shots = sum(merged[0]["counts"].values()) if merged else 0
        return FlowFileTransformResult(relationship="success",
            contents=json.dumps(out, indent=2).encode(),
            attributes={"batch.status": "completed", "batch.job_id": job_id,
                        "batch.size": str(len(merged)),
                        "batch.device": manifest.get("device", ""),
                        "batch.shots": str(shots),
                        "batch.bit_order": "q0_right",
                        "batch.actual_usage_seconds": actual_usage,
                        "batch.submitted_at": str(manifest.get("submitted_at") or ""),
                        "batch.completed_at": completed_at,
                        "batch.estimated_usage_seconds":
                            str(manifest.get("estimated_usage_seconds", ""))})
