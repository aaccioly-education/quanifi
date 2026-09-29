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

"""Fail-closed IBM quota reservation for hardware campaigns.

The IBM Open Plan reports account usage separately from a job's provider-side
usage estimate.  Immediately before a submission we need both: the live account
balance, and enough reserved time to finish every job still in the frozen
campaign.  This module performs that calculation without importing Qiskit, so
the same guard can be used by command-line runners and NiFi processors.

``quota_guard`` deliberately returns only an allow decision.  Every condition
that prevents a reliable decision raises :class:`QuotaGuardError`; callers must
not turn that exception into a warning or continue with submission.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from numbers import Real
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


QUOTA_SNAPSHOT_SCHEMA = "quanifi-ibm-quota-guard-v1"
QUOTA_SAFETY_FACTOR = 1.25
EMERGENCY_RESERVE_SECONDS = 10.0
FUTURE_CLOCK_SKEW_SECONDS = 5.0

# Largest observed cost for each job class in the historical IBM campaign.
# Each occurrence in the remaining ledger receives its own 25% reserve and is
# rounded up independently.  That intentionally gives 22 + 7 + 19 + 9*20 =
# 228 seconds for a complete fresh v6 campaign.
DEFAULT_CLASS_MAXIMA = MappingProxyType(
    {
        "screen": 17.0,
        "qualification": 5.0,
        "calibration": 15.0,
        "validation": 16.0,
    }
)

_SUBMISSION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ESTIMATE_KEYS = ("quantum_seconds", "usage_seconds", "seconds")


class QuotaGuardError(RuntimeError):
    """The IBM quota state is unsafe or cannot be verified."""


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _snapshot_digest(snapshot):
    body = dict(snapshot)
    body.pop("snapshot_sha256", None)
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _mapping(value, where):
    if not isinstance(value, Mapping):
        raise QuotaGuardError("%s is unavailable or is not an object" % where)
    return value


def _finite_number(value, where, *, positive=False):
    # bool is an int in Python but never a meaningful number of QPU seconds.
    if isinstance(value, bool) or not isinstance(value, Real):
        raise QuotaGuardError("%s must be a finite number" % where)
    result = float(value)
    if not math.isfinite(result):
        raise QuotaGuardError("%s must be a finite number" % where)
    if result < 0 or (positive and result <= 0):
        adjective = "positive" if positive else "non-negative"
        raise QuotaGuardError("%s must be %s" % (where, adjective))
    return result


def _datetime(value, where):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise QuotaGuardError("%s is not an ISO-8601 timestamp" % where) from exc
    else:
        raise QuotaGuardError("%s is unavailable" % where)
    if result.tzinfo is None or result.utcoffset() is None:
        raise QuotaGuardError("%s must include a timezone" % where)
    return result.astimezone(timezone.utc)


def _timestamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_usage(raw, checked_at, usage_max_age_seconds):
    usage = _mapping(raw, "IBM usage response")
    flat_fields = {
        "usage_consumed_seconds", "usage_limit_seconds",
        "usage_remaining_seconds", "usage_limit_reached",
    }
    has_flat = any(name in usage for name in flat_fields)
    has_nested = "usage" in usage or "usage_limit" in usage
    if has_flat and has_nested:
        raise QuotaGuardError("IBM usage response mixes flat and nested schemas")

    if has_flat:
        missing = sorted(flat_fields - set(usage))
        if missing:
            raise QuotaGuardError(
                "IBM usage response is missing flat field(s): %s" %
                ", ".join(missing))
        consumed = _finite_number(
            usage["usage_consumed_seconds"],
            "IBM usage.usage_consumed_seconds")
        limit = _finite_number(
            usage["usage_limit_seconds"],
            "IBM usage.usage_limit_seconds", positive=True)
        provider_remaining = _finite_number(
            usage["usage_remaining_seconds"],
            "IBM usage.usage_remaining_seconds")
        limit_reached = usage["usage_limit_reached"]
        if not isinstance(limit_reached, bool):
            raise QuotaGuardError("IBM usage.usage_limit_reached must be boolean")
        calculated_remaining = max(limit - consumed, 0.0)
        if not math.isclose(provider_remaining, calculated_remaining,
                            rel_tol=0.0, abs_tol=1e-6):
            raise QuotaGuardError(
                "IBM usage remaining seconds disagree with consumed and limit")
        if limit_reached != (calculated_remaining == 0.0):
            raise QuotaGuardError(
                "IBM usage limit flag disagrees with remaining seconds")
    else:
        consumed_group = _mapping(usage.get("usage"), "IBM usage.usage")
        limit_group = _mapping(usage.get("usage_limit"),
                               "IBM usage.usage_limit")
        if "seconds" not in consumed_group:
            raise QuotaGuardError("IBM usage.usage.seconds is unavailable")
        if "seconds" not in limit_group:
            raise QuotaGuardError("IBM usage.usage_limit.seconds is unavailable")
        consumed = _finite_number(consumed_group["seconds"],
                                  "IBM usage.usage.seconds")
        limit = _finite_number(limit_group["seconds"],
                               "IBM usage.usage_limit.seconds", positive=True)

    period = _mapping(usage.get("usage_period"), "IBM usage.usage_period")
    if "start_time" not in period:
        raise QuotaGuardError("IBM usage.usage_period.start_time is unavailable")
    if "end_time" not in period:
        raise QuotaGuardError("IBM usage.usage_period.end_time is unavailable")
    period_start = _datetime(period["start_time"],
                             "IBM usage.usage_period.start_time")
    period_end = _datetime(period["end_time"],
                           "IBM usage.usage_period.end_time")
    if period_start > period_end:
        raise QuotaGuardError("IBM usage period starts after it ends")

    age = (checked_at - period_end).total_seconds()
    if age < -FUTURE_CLOCK_SKEW_SECONDS:
        raise QuotaGuardError(
            "IBM usage timestamp is %.3f seconds in the future" % (-age))
    if age > usage_max_age_seconds:
        raise QuotaGuardError(
            "IBM usage is stale (%.3f seconds old; maximum %.3f)" %
            (age, usage_max_age_seconds))

    available = max(limit - consumed, 0.0)
    return {
        "consumed_seconds": consumed,
        "limit_seconds": limit,
        "available_seconds": available,
        "period_start_time": _timestamp(period_start),
        "period_end_time": _timestamp(period_end),
        # A small negative value is retained when it is within the explicit
        # provider/local-clock tolerance instead of pretending it was zero.
        "age_seconds": round(age, 6),
    }


def _normalize_classes(remaining_job_classes):
    if isinstance(remaining_job_classes, (str, bytes)):
        raise QuotaGuardError("remaining_job_classes must be an ordered iterable")
    try:
        values = tuple(remaining_job_classes)
    except TypeError as exc:
        raise QuotaGuardError(
            "remaining_job_classes must be an ordered iterable") from exc
    for index, value in enumerate(values):
        if not isinstance(value, str) or value not in DEFAULT_CLASS_MAXIMA:
            raise QuotaGuardError(
                "remaining_job_classes[%d] is not a known job class" % index)
    return values


def _provider_estimate(value, index):
    job = _mapping(value, "unresolved_jobs[%d]" % index)
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise QuotaGuardError("unresolved_jobs[%d].job_id is required" % index)
    job_id = job_id.strip()
    provider = job.get("provider", "ibm")
    if provider != "ibm":
        raise QuotaGuardError("unresolved job %s is not an IBM job" % job_id)

    direct_present = "estimated_usage_seconds" in job
    nested_present = "usage_estimation" in job
    if direct_present == nested_present:
        raise QuotaGuardError(
            "unresolved job %s must have exactly one provider usage estimate" %
            job_id)
    if direct_present:
        estimate = job["estimated_usage_seconds"]
    else:
        nested = _mapping(job["usage_estimation"],
                          "unresolved job %s usage_estimation" % job_id)
        keys = [name for name in _ESTIMATE_KEYS if name in nested]
        if len(keys) != 1:
            raise QuotaGuardError(
                "unresolved job %s must have exactly one provider usage estimate" %
                job_id)
        estimate = nested[keys[0]]
    seconds = _finite_number(
        estimate, "unresolved job %s provider usage estimate" % job_id)
    return {
        "job_id": job_id,
        "provider": "ibm",
        "estimated_usage_seconds": seconds,
        "reserved_seconds": int(math.ceil(seconds)),
    }


def _normalize_unresolved(unresolved_jobs):
    if unresolved_jobs is None or isinstance(unresolved_jobs, (str, bytes)):
        raise QuotaGuardError("unresolved_jobs must be an ordered iterable")
    try:
        values = tuple(unresolved_jobs)
    except TypeError as exc:
        raise QuotaGuardError("unresolved_jobs must be an ordered iterable") from exc
    normalized = tuple(_provider_estimate(value, index)
                       for index, value in enumerate(values))
    ids = tuple(value["job_id"] for value in normalized)
    if len(ids) != len(set(ids)):
        raise QuotaGuardError("unresolved_jobs contains duplicate job IDs")
    return normalized


def quota_guard(service, remaining_job_classes, unresolved_jobs=(),
                minimum_remaining_seconds=0, *, usage_max_age_seconds=300,
                now=None):
    """Verify and reserve sufficient live IBM quota for one submission.

    Args:
        service: Authenticated ``QiskitRuntimeService``-compatible object. Its
            ``usage()`` method is called exactly once.
        remaining_job_classes: Ordered job-class name for every campaign job
            that has not completed, including the job about to be submitted.
        unresolved_jobs: Ordered mappings for unresolved IBM jobs. Each mapping
            must contain ``job_id`` and either ``estimated_usage_seconds`` or a
            provider ``usage_estimation`` mapping with one recognized seconds
            field.
        minimum_remaining_seconds: An externally calculated fail-closed floor.
            It is combined with the campaign reservation using ``max`` and is
            therefore never accidentally counted twice.
        usage_max_age_seconds: Maximum age of ``usage_period.end_time``.
        now: Optional aware datetime or ISO timestamp used by deterministic
            tests. Current UTC time is used otherwise.

    Returns:
        A JSON-serializable, digest-bound allow-decision snapshot.

    Raises:
        QuotaGuardError: If usage or estimates are unavailable, malformed,
            stale, or insufficient. A caller must abort the submission.
    """
    max_age = _finite_number(usage_max_age_seconds,
                             "usage_max_age_seconds", positive=True)
    minimum = _finite_number(minimum_remaining_seconds,
                             "minimum_remaining_seconds")
    classes = _normalize_classes(remaining_job_classes)
    unresolved = _normalize_unresolved(unresolved_jobs)
    checked_at = (_datetime(now, "now") if now is not None
                  else datetime.now(timezone.utc))

    usage_method = getattr(service, "usage", None)
    if not callable(usage_method):
        raise QuotaGuardError("IBM service.usage() is unavailable")
    try:
        raw_usage = usage_method()
    except Exception as exc:  # noqa: BLE001 - provider failures must fail closed
        raise QuotaGuardError("IBM service.usage() failed: %s" % exc) from exc
    normalized_usage = _normalize_usage(raw_usage, checked_at, max_age)

    job_reservations = []
    for sequence, job_class in enumerate(classes, 1):
        reference = DEFAULT_CLASS_MAXIMA[job_class]
        reserved = int(math.ceil(QUOTA_SAFETY_FACTOR * reference))
        job_reservations.append({
            "remaining_sequence": sequence,
            "job_class": job_class,
            "reference_seconds": reference,
            "safety_factor": QUOTA_SAFETY_FACTOR,
            "reserved_seconds": reserved,
        })
    jobs_seconds = sum(value["reserved_seconds"] for value in job_reservations)
    unresolved_seconds = sum(value["reserved_seconds"] for value in unresolved)
    calculated = jobs_seconds + unresolved_seconds + EMERGENCY_RESERVE_SECONDS
    required = max(calculated, minimum)
    available = normalized_usage["available_seconds"]
    headroom = available - required
    if headroom < 0:
        raise QuotaGuardError(
            "insufficient IBM quota: %.3f seconds available, %.3f required "
            "(short by %.3f)" % (available, required, -headroom))

    snapshot = {
        "schema": QUOTA_SNAPSHOT_SCHEMA,
        "decision": "allow",
        "checked_at": _timestamp(checked_at),
        "usage_max_age_seconds": max_age,
        "usage": normalized_usage,
        "remaining_job_classes": list(classes),
        "job_reservations": job_reservations,
        "unresolved_jobs": list(unresolved),
        "reservation": {
            "remaining_jobs_seconds": jobs_seconds,
            "unresolved_jobs_seconds": unresolved_seconds,
            "emergency_reserve_seconds": EMERGENCY_RESERVE_SECONDS,
            "calculated_required_seconds": calculated,
            "minimum_remaining_seconds": minimum,
            "required_seconds": required,
            "available_seconds": available,
            "headroom_seconds": headroom,
        },
    }
    snapshot["snapshot_sha256"] = _snapshot_digest(snapshot)
    return snapshot


def archive_quota_snapshot(directory, snapshot, submission_key):
    """Create one immutable quota-evidence file and return its path.

    Existing evidence is never replaced, even when its contents are identical.
    The file is created with mode ``0600`` because provider job identifiers and
    account usage are operational evidence even though no credential is stored.
    """
    if not isinstance(submission_key, str) or not _SUBMISSION_KEY.fullmatch(
            submission_key):
        raise QuotaGuardError("submission_key is not a safe archive identifier")
    value = _mapping(snapshot, "quota snapshot")
    if value.get("schema") != QUOTA_SNAPSHOT_SCHEMA or value.get("decision") != "allow":
        raise QuotaGuardError("only a quota-guard allow snapshot can be archived")
    digest = value.get("snapshot_sha256")
    if not isinstance(digest, str) or digest != _snapshot_digest(value):
        raise QuotaGuardError("quota snapshot digest mismatch")
    try:
        encoded = (json.dumps(value, indent=2, sort_keys=True,
                              ensure_ascii=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise QuotaGuardError("quota snapshot is not JSON serializable") from exc

    archive_dir = Path(directory)
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        path = archive_dir / (submission_key + "-ibm-quota.json")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise QuotaGuardError("quota snapshot already exists: %s" % path) from exc
    except OSError as exc:
        raise QuotaGuardError("cannot archive quota snapshot: %s" % exc) from exc
    return path


__all__ = [
    "DEFAULT_CLASS_MAXIMA",
    "EMERGENCY_RESERVE_SECONDS",
    "QUOTA_SAFETY_FACTOR",
    "QUOTA_SNAPSHOT_SCHEMA",
    "QuotaGuardError",
    "archive_quota_snapshot",
    "quota_guard",
]
