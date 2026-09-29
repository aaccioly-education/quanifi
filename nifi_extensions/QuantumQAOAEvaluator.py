"""Framework-neutral QAOA scoring: <any>Hamiltonian/builder/solver -> <any
counts engine> -> QuantumQAOAEvaluator -> QuanifiReport.

Every solver used to compute its own sample-dependent results (best
measurement, approximation ratio, ...) against its own sampling. That tied
those numbers to one specific engine. This processor computes every
sample-dependent QAOA result from ANY engine's q0-left counts plus the cost
Hamiltonian, so the same metric definitions apply uniformly across the whole
NxM matrix (5 builders/solvers x 7 counts engines) and every cell stamps its
own row/column (``qaoa.builder``/``qaoa.engine``).

See ``qaoa_contract.py`` for the shared parsing/validation/energy machinery
this processor is built on; this file owns only the evaluator's own
validation order and metric computation.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc


class QuantumQAOAEvaluator(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Framework-neutral QAOA scoring for any counts engine's output. Reads "
            "q0_left measurement counts (sim.bit_order=q0_left) plus the cost "
            "Hamiltonian (hamiltonian.json from any upstream QAOA builder/solver, or "
            "the Hamiltonian property) and emits qaoa.best_measurement, best_value, "
            "sampled_expectation, exact_minimum/maximum, approximation_ratio, "
            "expectation_ratio, optimal_probability, optimal_states and the "
            "builder/engine that produced this cell. Counts pass through unchanged "
            "so QuanifiReport can still render them."
        )
        tags = ["quantum", "qaoa", "evaluation", "optimization", "framework-agnostic"]
        dependencies = ["numpy>=1.26"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        from nifiapi.properties import (
            ExpressionLanguageScope,
            PropertyDescriptor,
            StandardValidators,
        )

        self.descriptors = [
            PropertyDescriptor(
                name="Hamiltonian",
                description=(
                    "The cost Hamiltonian to score against: the neutral wire JSON "
                    "(strictly validated) or the indexed Pauli DSL (e.g. "
                    "'Z0 Z1 - 0.5 Z2 + 1'), sized from the counts width. Defaults to "
                    "the hamiltonian.json attribute carried by an upstream QAOA "
                    "builder or solver."
                ),
                required=True,
                default_value="${hamiltonian.json}",
                validators=[StandardValidators.NON_EMPTY_VALIDATOR],
                expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
            )
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        # Not qc.run_guarded: on failure the evaluator must also blank every
        # EVALUATOR_KEYS entry (a stale success from an earlier run must not
        # survive a failed re-evaluation), which run_guarded's plain
        # {"qaoa.error": message} does not do.
        try:
            return self._run(context, flowFile)
        except Exception as exc:
            return qc.failure(
                self, flowFile, str(exc), extra={k: "" for k in qc.EVALUATOR_KEYS}
            )

    def _parse_counts(self, flowfile):
        import json

        raw = bytes(flowfile.getContentsAsBytes() or b"").decode("utf-8")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "counts content must be valid JSON: {}".format(exc)
            ) from exc
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("counts content must be a non-empty JSON object")

        widths = {len(k) for k in parsed}
        if len(widths) != 1 or 0 in widths:
            raise ValueError(
                "counts keys must be non-empty binary strings of equal width"
            )
        width = next(iter(widths))

        counts = {}
        for key, value in parsed.items():
            if set(key) - {"0", "1"}:
                raise ValueError("counts key {!r} is not a binary string".format(key))
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    "counts value for {!r} must be a finite non-negative number".format(
                        key
                    )
                )
            fv = float(value)
            if fv != fv or fv in (float("inf"), float("-inf")) or fv < 0:
                raise ValueError(
                    "counts value for {!r} must be a finite non-negative number".format(
                        key
                    )
                )
            counts[key] = fv

        total = float(sum(counts.values()))
        if total <= 0:
            raise ValueError("counts must have a positive total")
        return counts, width, total

    def _run(self, context, flowfile):
        import json

        # 1. bit order.
        if flowfile.getAttribute("sim.bit_order") != "q0_left":
            raise ValueError(
                "sim.bit_order must be 'q0_left' (got {!r}); connect a Quanifi "
                "counts engine upstream of the evaluator".format(
                    flowfile.getAttribute("sim.bit_order")
                )
            )

        # 2. counts.
        counts, width, total = self._parse_counts(flowfile)

        # 3. Hamiltonian.
        spec = (
            context.getProperty(self.descriptors[0])
            .evaluateAttributeExpressions(flowfile)
            .getValue()
        )
        terms, n = qc.parse_hamiltonian_spec(spec, width)

        # 4. diagonal (identity-only IS allowed here, unlike require_qaoa_cost).
        from pauli_dsl import is_diagonal

        if not is_diagonal(terms):
            raise ValueError(
                "cost Hamiltonian must be diagonal (I/Z terms only) for QAOA; "
                "got X/Y terms"
            )

        # 5. width match.
        if width != n:
            raise ValueError(
                "counts width {} does not match the Hamiltonian's {} qubits".format(
                    width, n
                )
            )

        # 6. circuit.num_qubits cross-check, if present.
        circuit_n = (flowfile.getAttribute("circuit.num_qubits") or "").strip()
        if circuit_n and int(circuit_n) != n:
            raise ValueError(
                "circuit.num_qubits={} does not match the Hamiltonian's {} "
                "qubits".format(circuit_n, n)
            )

        E = qc.energy_vector(terms, n)
        energy = {k: float(E[int(k, 2)]) for k in counts}
        best = min(counts, key=lambda k: (round(energy[k], qc.TIE_DECIMALS), k))
        emin, emax = float(E.min()), float(E.max())

        def opt(e):
            return round(e, qc.TIE_DECIMALS) == round(emin, qc.TIE_DECIMALS)

        sampled = sum(counts[k] * energy[k] for k in counts) / total
        p_opt = sum(counts[k] for k in counts if opt(energy[k])) / total
        optimal = sorted(
            format(i, "0{}b".format(n)) for i in range(2**n) if opt(float(E[i]))
        )

        degenerate = emax - emin <= 1e-12
        approximation_ratio = (
            1.0 if degenerate else (emax - energy[best]) / (emax - emin)
        )
        expectation_ratio = 1.0 if degenerate else (emax - sampled) / (emax - emin)

        integral = all(float(v).is_integer() for v in counts.values())
        shots = (
            str(int(total)) if integral else (flowfile.getAttribute("sim.shots") or "")
        )

        attrs = {
            "qaoa.best_measurement": best,
            "qaoa.best_value": repr(energy[best]),
            "qaoa.sampled_expectation": repr(float(sampled)),
            "qaoa.exact_minimum": repr(emin),
            "qaoa.exact_maximum": repr(emax),
            "qaoa.approximation_ratio": repr(float(approximation_ratio)),
            "qaoa.expectation_ratio": repr(float(expectation_ratio)),
            "qaoa.optimal_probability": repr(float(p_opt)),
            "qaoa.optimal_states": json.dumps(optimal[: qc.MAX_LISTED_OPTIMAL_STATES]),
            "qaoa.num_optimal_states": str(len(optimal)),
            "qaoa.shots": shots,
            "qaoa.builder": flowfile.getAttribute("builder.component") or "",
            "qaoa.engine": flowfile.getAttribute("sim.component") or "",
            "qaoa.evaluator": "QuantumQAOAEvaluator",
            "qaoa.num_qubits": str(n),
            "qaoa.error": "",
            "report.type": "simulation",
            "mime.type": "application/json",
        }
        content = bytes(flowfile.getContentsAsBytes() or b"")
        return qc.success(content, attrs)
