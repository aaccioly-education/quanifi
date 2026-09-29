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

"""Shared, non-circuit QAOA machinery for all 11 QAOA processors (not a
processor itself).

Owns everything about the QAOA family that is not "build this framework's
circuit": strict wire-format validation, property parsing, the attribute
contract (with stale-key blanking), the portable-qasm2 subset used as the
NxM interchange format, q0-left energies, the shared ``PropertyDescriptor``
factories, and the uniform success/failure result helpers. Each framework's
circuit construction lives in its own helper module
(``qiskit_qaoa.py``, ``cirq_qaoa.py``, ``pennylane_qaoa.py``, ``qrisp_qaoa.py``,
``pyquil_processor.qaoa_export``); this module never builds a circuit.

Module-level imports are stdlib plus ``pauli_dsl`` only. ``numpy`` and
``nifiapi`` are imported lazily inside the functions that need them, so this
module can be imported by test helpers with no NiFi/JVM and no numpy
dependency at import time.
"""

from __future__ import annotations

import json
import math
import re

from pauli_dsl import is_diagonal, parse_pauli_sum, terms_to_wire, wire_to_terms

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_QUBITS = 16
MAX_LAYERS = 16
MAX_TERMS = 4096
MAX_ITERATIONS = 10000
MAX_SEED = 2**32 - 1
PORTABLE_GATES = frozenset({"h", "x", "rx", "ry", "rz", "cx"})
TIE_DECIMALS = 9
MAX_LISTED_OPTIMAL_STATES = 64
DEFAULT_BETAS = "0.39269908169872414"
DEFAULT_GAMMAS = "0.7853981633974483"

EVALUATOR_KEYS = (
    "qaoa.best_measurement",
    "qaoa.best_value",
    "qaoa.sampled_expectation",
    "qaoa.exact_minimum",
    "qaoa.exact_maximum",
    "qaoa.approximation_ratio",
    "qaoa.expectation_ratio",
    "qaoa.optimal_probability",
    "qaoa.optimal_states",
    "qaoa.num_optimal_states",
    "qaoa.shots",
    "qaoa.builder",
    "qaoa.engine",
    "qaoa.evaluator",
)

_REJECTED_QASM_KEYWORDS = frozenset(
    {"creg", "measure", "barrier", "reset", "gate", "opaque", "if", "gphase"}
)


# ---------------------------------------------------------------------------
# Numeric / property parsing
# ---------------------------------------------------------------------------

_INT_RE = re.compile(r"^[+-]?\d+$")


def parse_int(value, name, minimum, maximum):
    """Parse ``value`` as an integer in ``[minimum, maximum]``.

    Rejects bools, blanks and non-integral text (e.g. "1.5"); accepts only
    strings matching ``[+-]?\\d+`` once stripped.
    """
    if isinstance(value, bool):
        raise ValueError("{} must be an integer, got {!r}".format(name, value))
    s = "" if value is None else str(value).strip()
    if not _INT_RE.match(s):
        raise ValueError("{} must be an integer, got {!r}".format(name, value))
    n = int(s)
    if not minimum <= n <= maximum:
        raise ValueError("{} must be between {} and {}".format(name, minimum, maximum))
    return n


def parse_layers(value):
    return parse_int(value, "Layers", 1, MAX_LAYERS)


def parse_max_iterations(value):
    return parse_int(value, "Max Iterations", 1, MAX_ITERATIONS)


def parse_seed(value):
    """None or blank -> None (nondeterministic); else an integer 0..MAX_SEED."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return parse_int(s, "Random Seed", 0, MAX_SEED)


def parse_choice(value, choices, name):
    if value not in choices:
        raise ValueError(
            "{} must be one of {}; got {!r}".format(name, list(choices), value)
        )
    return value


def _coerce_list(value):
    """A JSON list, a comma-separated string, or an existing iterable -> a
    plain Python list, with no element-level validation yet."""
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return []
        if s.startswith("["):
            try:
                data = json.loads(s)
            except json.JSONDecodeError as exc:
                raise ValueError("could not parse {!r} as JSON".format(s)) from exc
            if not isinstance(data, list):
                raise ValueError("expected a JSON list, got {!r}".format(s))
            return data
        return [x.strip() for x in s.split(",")]
    return list(value)


def _to_finite_floats(data, name):
    out = []
    for x in data:
        if isinstance(x, bool):
            raise ValueError("{} must contain only finite numbers".format(name))
        try:
            f = float(x)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "{} must contain only finite numbers".format(name)
            ) from exc
        if not math.isfinite(f):
            raise ValueError("{} must contain only finite numbers".format(name))
        out.append(f)
    return out


def parse_angles(value, size, name):
    """A JSON list or comma-separated string -> exactly ``size`` finite floats."""
    data = _coerce_list(value)
    floats = _to_finite_floats(data, name)
    if len(floats) != size:
        raise ValueError("{} must contain exactly {} finite numbers".format(name, size))
    return floats


def parse_initial_parameters(value, layers):
    """'random' -> None, 'zeros' -> 2p zeros, else exactly 2p finite floats
    (all betas, then all gammas)."""
    v = ("" if value is None else str(value)).strip()
    if v.lower() == "random":
        return None
    if v.lower() == "zeros":
        return [0.0] * (2 * layers)
    data = _coerce_list(v)
    if len(data) != 2 * layers:
        raise ValueError(
            "Initial Parameters length {} != 2p = {}".format(len(data), 2 * layers)
        )
    return _to_finite_floats(data, "Initial Parameters")


def split_parameters(point, layers):
    """A flat 2p array/list -> (betas, gammas), the canonical order."""
    betas = [float(x) for x in list(point)[:layers]]
    gammas = [float(x) for x in list(point)[layers : 2 * layers]]
    return betas, gammas


# ---------------------------------------------------------------------------
# Wire-format (Hamiltonian) validation
# ---------------------------------------------------------------------------


def validate_wire(data):
    """Strictly validate the neutral wire dict (or its JSON text) and return
    ``(terms, num_qubits)``. Reimplements ``pyquil_components.hamiltonian``'s
    rules without importing that module (kept independent on purpose)."""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError("Hamiltonian must be valid JSON: {}".format(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("Hamiltonian must be a sparse_pauli_op_json object")
    n = parse_int(data.get("num_qubits"), "Hamiltonian qubits", 1, MAX_QUBITS)
    entries = data.get("terms")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TERMS:
        raise ValueError("Hamiltonian requires 1..{} terms".format(MAX_TERMS))
    for term in entries:
        if not isinstance(term, list) or len(term) != 3:
            raise ValueError("Each Hamiltonian term must be [label, real, imaginary]")
        label, real, imag = term
        if not isinstance(label, str) or len(label) != n or set(label) - set("IXYZ"):
            raise ValueError("Pauli label must contain I/X/Y/Z and match num_qubits")
        if not math.isfinite(float(real)) or not math.isfinite(float(imag)):
            raise ValueError("Hamiltonian coefficients must be finite")
        if float(imag) != 0:
            raise ValueError("Hamiltonian coefficients must be real (Hermitian)")
    return wire_to_terms(data)


def require_qaoa_cost(terms):
    """Raise unless ``terms`` is a nonempty diagonal (I/Z) cost with >=1 Z term."""
    if not is_diagonal(terms):
        raise ValueError(
            "cost Hamiltonian must be diagonal (I/Z terms only) for QAOA; "
            "got X/Y terms"
        )
    if all(not pauli for pauli, _idx, _c in terms):
        raise ValueError(
            "cost Hamiltonian has no Z terms (identity only); QAOA needs at "
            "least one Z term"
        )


def read_cost_hamiltonian(flowfile):
    """Resolve the cost Hamiltonian from a FlowFile, per the shared input rule:
    content (from an upstream Hamiltonian processor) or, on the rebuild path
    (a FlowFile that already carries a circuit), the ``hamiltonian.json``
    attribute. Returns ``(terms, num_qubits, raw_wire_text)``."""
    fmt = (flowfile.getAttribute("circuit.format") or "").strip()
    if fmt:
        raw = flowfile.getAttribute("hamiltonian.json") or ""
        if not raw.strip():
            raise ValueError(
                "FlowFile carries a circuit (circuit.format=%r) but no "
                "hamiltonian.json attribute" % fmt
            )
    else:
        if flowfile.getAttribute("hamiltonian.format") != "sparse_pauli_op_json":
            raise ValueError(
                "no Hamiltonian on the FlowFile (expected hamiltonian.format="
                "'sparse_pauli_op_json' from an upstream Hamiltonian processor)"
            )
        raw = bytes(flowfile.getContentsAsBytes() or b"").decode("utf-8")
    terms, n = validate_wire(raw)
    require_qaoa_cost(terms)
    return terms, n, raw


def parse_hamiltonian_spec(spec, width):
    """Evaluator-side Hamiltonian resolution: wire JSON (strict), or the
    indexed Pauli DSL sized from the counts width. Returns ``(terms, n)``."""
    s = ("" if spec is None else str(spec)).strip()
    if not s:
        raise ValueError(
            "no cost Hamiltonian: set the Hamiltonian property or provide "
            "hamiltonian.json from an upstream QAOA builder/solver"
        )
    if s.startswith("{"):
        return validate_wire(s)
    terms, n = parse_pauli_sum(s, width)
    return validate_wire(terms_to_wire(terms, n))


# ---------------------------------------------------------------------------
# Energies (q0-left)
# ---------------------------------------------------------------------------


def energy_vector(terms, n):
    """Exact energy of every computational basis state, indexed by
    ``int(q0_left_key, 2)`` (q0 = the leftmost/most-significant character).

    ``pauli_dsl.diagonal_values`` indexes with q0 = least significant bit;
    this bit-reverses that index to the canonical q0-left convention used by
    every emitter and by the evaluator.
    """
    import numpy as np

    from pauli_dsl import diagonal_values

    values = diagonal_values(terms, n)
    states = np.arange(2**n)
    order = np.zeros(2**n, dtype=np.int64)
    for i in range(n):
        order |= ((states >> (n - 1 - i)) & 1) << i
    return values[order]


# ---------------------------------------------------------------------------
# Portable qasm2 profile (the NxM interchange contract)
# ---------------------------------------------------------------------------

_QREG_RE = re.compile(r"^qreg\s+([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]$")
_ARG_RE = re.compile(r"([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]")
_LEADING_NAME_RE = re.compile(r"^([A-Za-z_]\w*)")
_OPENQASM_RE = re.compile(r"^OPENQASM\s+2\.0$")


def qasm2_profile(source):
    """Parse and validate the portable qasm2 subset by hand (no qiskit
    dependency): ``OPENQASM 2.0;``, an optional ``include "qelib1.inc";``,
    exactly one ``qreg``, and gates only from ``PORTABLE_GATES``.

    Returns ``{"num_qubits", "gate_count", "depth", "nonlocal_gates",
    "gate_counts"}``, agreeing with Qiskit's ``depth()``/``size()`` on every
    accepted circuit.
    """
    text = re.sub(r"//[^\n]*", "", source)
    statements = [s.strip() for s in text.split(";") if s.strip()]
    if not statements or not _OPENQASM_RE.match(statements[0]):
        raise ValueError("qasm2 must start with 'OPENQASM 2.0;'")

    reg_name, reg_width = None, None
    seen_gate = False
    depth = {}
    gate_count = 0
    nonlocal_gates = 0
    gate_counts = {}

    for stmt in statements[1:]:
        if stmt == 'include "qelib1.inc"':
            continue
        m = _QREG_RE.match(stmt)
        if m:
            if seen_gate:
                raise ValueError("qreg declared after a gate statement")
            if reg_name is not None:
                raise ValueError(
                    "exactly one qreg is required, found a second: {!r}".format(stmt)
                )
            reg_name, reg_width = m.group(1), int(m.group(2))
            continue

        name_m = _LEADING_NAME_RE.match(stmt)
        name = name_m.group(1) if name_m else ""
        if name in _REJECTED_QASM_KEYWORDS or name not in PORTABLE_GATES:
            raise ValueError(
                "unsupported statement in portable qasm2: {!r}".format(stmt)
            )
        if reg_name is None:
            raise ValueError("exactly one qreg is required, declared before any gate")

        seen_gate = True
        paren = stmt.rfind(")")
        arg_text = stmt[paren + 1 :] if paren != -1 else stmt[len(name) :]
        args = _ARG_RE.findall(arg_text)
        if not args:
            raise ValueError("gate statement has no qubit arguments: {!r}".format(stmt))
        qubits = []
        for reg, idx_s in args:
            if reg != reg_name:
                raise ValueError("gate references undeclared register {!r}".format(reg))
            idx = int(idx_s)
            if idx >= reg_width:
                raise ValueError(
                    "qubit index {} out of range for register width {}".format(
                        idx, reg_width
                    )
                )
            qubits.append(idx)

        level = max((depth.get(q, 0) for q in qubits), default=0) + 1
        for q in qubits:
            depth[q] = level
        gate_count += 1
        gate_counts[name] = gate_counts.get(name, 0) + 1
        if len(qubits) >= 2:
            nonlocal_gates += 1

    if reg_name is None:
        raise ValueError("exactly one qreg is required")

    return {
        "num_qubits": reg_width,
        "gate_count": gate_count,
        "depth": max(depth.values(), default=0),
        "nonlocal_gates": nonlocal_gates,
        "gate_counts": gate_counts,
    }


# ---------------------------------------------------------------------------
# Property reading (called from a processor's transform)
# ---------------------------------------------------------------------------


def read_properties(processor, context, flowfile):
    return {
        d.name: context.getProperty(d).evaluateAttributeExpressions(flowfile).getValue()
        for d in processor.descriptors
    }


# ---------------------------------------------------------------------------
# Attribute contracts
# ---------------------------------------------------------------------------


def circuit_attributes(source, profile, framework, component, diagram, svg=""):
    """The circuit.*/builder.* contract every builder and solver emits."""
    return {
        "circuit.format": "qasm2",
        "circuit.qasm2": source,
        "circuit.qasm3": "",
        "circuit.cirq_json": "",
        "circuit.svg": svg,
        "circuit.diagram": diagram,
        "circuit.num_qubits": str(profile["num_qubits"]),
        "circuit.depth": str(profile["depth"]),
        "circuit.gate_count": str(profile["gate_count"]),
        "circuit.nonlocal_gates": str(profile["nonlocal_gates"]),
        "circuit.gate_counts": json.dumps(
            profile.get("gate_counts", {}), sort_keys=True
        ),
        "circuit.t_count": "0",
        "circuit.framework": framework,
        "builder.framework": framework,
        "builder.component": component,
        "circuit.bit_order": "q0_left",
        "circuit.algorithm": "qaoa",
        "circuit.num_parameters": "0",
        "circuit.marked_state": "",
        "circuit.num_iterations": "",
        "circuit.error": "",
        "mime.type": "text/plain",
        "report.type": "circuit",
    }


def builder_attributes(raw, n, layers, betas, gammas, mixer="RX"):
    """The builder/solver-shared qaoa.* contract: blank every evaluator key
    (stale-key hygiene), then the angles that produced this circuit."""
    attrs = {k: "" for k in EVALUATOR_KEYS}
    attrs.update(
        {
            "hamiltonian.json": raw,
            "qaoa.num_qubits": str(n),
            "qaoa.layers": str(layers),
            "qaoa.betas": json.dumps([float(b) for b in betas]),
            "qaoa.gammas": json.dumps([float(g) for g in gammas]),
            "qaoa.mixer_type": mixer,
            "qaoa.error": "",
        }
    )
    return attrs


def solver_attributes(
    framework,
    betas,
    gammas,
    optimal_value,
    energy_method,
    energy_shots,
    optimizer,
    max_iterations,
    cost_function_evals,
    num_iterations,
    converged,
    optimizer_message,
    seed,
    elapsed_seconds,
):
    """The solver-only extension of the qaoa.* contract (on top of
    ``circuit_attributes`` and ``builder_attributes``)."""
    return {
        "qaoa.framework": framework,
        "qaoa.optimal_parameters": json.dumps(
            [float(b) for b in betas] + [float(g) for g in gammas]
        ),
        "qaoa.optimal_value": repr(float(optimal_value)),
        "qaoa.energy_method": energy_method,
        "qaoa.energy_shots": energy_shots,
        "qaoa.optimizer": optimizer,
        "qaoa.max_iterations": str(max_iterations),
        "qaoa.cost_function_evals": str(cost_function_evals),
        "qaoa.num_iterations": str(num_iterations),
        "qaoa.converged": "true" if converged else "false",
        "qaoa.optimizer_message": optimizer_message or "",
        "qaoa.seed": "" if seed is None else str(seed),
        "qaoa.elapsed_seconds": "{:.4f}".format(elapsed_seconds),
        "perf.elapsed_seconds": "{:.4f}".format(elapsed_seconds),
    }


# ---------------------------------------------------------------------------
# Descriptor factories (called from a processor's __init__, after JvmHolder.jvm)
# ---------------------------------------------------------------------------


def _mixer_type_descriptor():
    from nifiapi.properties import PropertyDescriptor

    return PropertyDescriptor(
        name="Mixer Type",
        description=(
            "QAOA mixer: RX (transverse field; the cross-framework convention) "
            "or XY (Qrisp's Hamming-weight-preserving ring mixer; Qrisp-only, "
            "not comparable with the other builders)."
        ),
        required=True,
        default_value="RX",
        allowable_values=["RX", "XY"],
    )


def solver_descriptors(optimizers, with_mixer=False):
    """Shared solver properties. Qrisp inserts Mixer Type at index 1."""
    from nifiapi.properties import (
        ExpressionLanguageScope,
        PropertyDescriptor,
        StandardValidators,
    )

    layers = PropertyDescriptor(
        name="Layers",
        description=(
            "Number of QAOA layers p (cost + mixer repetitions), 1..16. The "
            "circuit has 2p angles, ordered all betas then all gammas."
        ),
        required=True,
        default_value="2",
        validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    optimizer = PropertyDescriptor(
        name="Optimizer",
        description="Classical optimizer driving the angle search.",
        required=True,
        default_value="COBYLA",
        allowable_values=list(optimizers),
    )
    max_iterations = PropertyDescriptor(
        name="Max Iterations",
        description=(
            "Optimizer budget, 1..10000 (COBYLA counts function evaluations; "
            "other methods count iterations)."
        ),
        required=True,
        default_value="100",
        validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    initial_parameters = PropertyDescriptor(
        name="Initial Parameters",
        description=(
            "'random', 'zeros', or a JSON/comma-separated list of 2p finite "
            "angles: all betas, then all gammas."
        ),
        required=True,
        default_value="random",
        validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    random_seed = PropertyDescriptor(
        name="Random Seed",
        description=(
            "Seed for reproducible training (initial angles and any optimizer "
            "sampling), 0..4294967295. Unset = nondeterministic. Measurement "
            "sampling happens downstream in the simulator, which has its own seed."
        ),
        required=False,
        default_value=None,
        validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    descriptors = [layers, optimizer, max_iterations, initial_parameters, random_seed]
    if with_mixer:
        descriptors.insert(1, _mixer_type_descriptor())
    return descriptors


def builder_descriptors(with_mixer=False):
    """Shared builder properties. Qrisp appends Mixer Type last."""
    from nifiapi.properties import (
        ExpressionLanguageScope,
        PropertyDescriptor,
        StandardValidators,
    )

    layers = PropertyDescriptor(
        name="Layers",
        description=(
            "Number of QAOA layers p, 1..16. Betas and Gammas must each hold "
            "exactly p angles."
        ),
        required=True,
        default_value="1",
        validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    betas = PropertyDescriptor(
        name="Betas",
        description=(
            "Mixer angles, one per layer, as a JSON list or comma-separated "
            "values. Each layer applies RX(2*beta) to every qubit."
        ),
        required=True,
        default_value=DEFAULT_BETAS,
        validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    gammas = PropertyDescriptor(
        name="Gammas",
        description=(
            "Cost angles, one per layer, as a JSON list or comma-separated "
            "values. Each layer applies exp(-i*gamma*H)."
        ),
        required=True,
        default_value=DEFAULT_GAMMAS,
        validators=[StandardValidators.NON_EMPTY_VALIDATOR],
        expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
    )
    descriptors = [layers, betas, gammas]
    if with_mixer:
        descriptors.append(_mixer_type_descriptor())
    return descriptors


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------


def success(contents, attrs):
    from nifiapi.flowfiletransform import FlowFileTransformResult

    return FlowFileTransformResult(
        relationship="success", contents=contents, attributes=attrs
    )


def failure(processor, flowfile, message, extra=None):
    from nifiapi.flowfiletransform import FlowFileTransformResult

    processor.logger.error("{}: {}".format(type(processor).__name__, message))
    return FlowFileTransformResult(
        relationship="failure",
        contents=bytes(flowfile.getContentsAsBytes() or b""),
        attributes={"qaoa.error": message, **(extra or {})},
    )


def run_guarded(processor, flowfile, fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - route every failure uniformly
        return failure(processor, flowfile, str(exc))
