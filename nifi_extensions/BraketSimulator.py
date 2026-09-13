import time
import contextlib
import io
import json
import os
import re
import sys

# NiFi runs each processor in its own module context where the extensions
# directory is not on sys.path, so the sibling-module import (braket_qasm)
# fails unless we add this file's directory explicitly. (Tests pass without
# it only because conftest puts the directory on sys.path.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class BraketSimulator(FlowFileTransform):
    """Amazon Braket local simulator. A fifth independently-implemented
    counts engine for cross-framework differential testing, alongside
    QiskitAerSimulator, CirqSimulator, QrispSimulator and PennylaneSimulator.
    Both qasm3 and qasm2 inputs are re-based onto Braket-native gates via a
    Qiskit parse-and-re-emit step (translation only — the simulation itself
    is pure Braket, flagged with sim.translation for traceability; see
    braket_qasm.py for why inlined gate definitions are unsound).

    Noise Model = none (the default) samples the translated circuit on the
    ideal state-vector simulator (braket_sv) exactly as before. Any other
    Noise Model switches to the density-matrix simulator (braket_dm) and
    injects a `#pragma braket noise <channel>(<p>) q[i]` line after each
    translated 1-qubit gate (h, x, rz — see braket_qasm.BRAKET_SAFE_BASIS),
    reachable directly from OpenQASM without needing any Braket-native gate
    definitions. braket_dm's memory cost is 4^n (vs 2^n for braket_sv), so
    noisy runs are only practical for narrower circuits and are slower per
    shot; 2-qubit-gate noise is not modeled."""

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Reads a quantum circuit from the FlowFile content (OpenQASM 3 or "
            "OpenQASM 2.0, determined by the 'circuit.format' attribute; both "
            "are re-based onto Braket-native gates via Qiskit's parser), "
            "samples it on the Amazon Braket LocalSimulator (no AWS account or "
            "network access required), and writes shot counts as JSON. Bit "
            "order is MSB-first (qubit 0 leftmost, the Cirq convention). "
            "Noise Model = none (default) runs the ideal state-vector "
            "simulator (braket_sv), unchanged from prior versions. Any other "
            "Noise Model runs the density-matrix simulator (braket_dm) with "
            "the selected single-qubit noise channel injected via Braket "
            "QASM pragmas after every 1-qubit gate; braket_dm's memory cost "
            "is 4^n (vs 2^n for braket_sv), so noisy runs are narrower and "
            "slower than noiseless ones."
        )
        tags = ["quantum", "braket", "aws", "simulation", "measurement", "noise"]
        dependencies = ["amazon-braket-sdk", "qiskit>=2.0.0,<2.5", "qiskit-qasm3-import"]

    # Single-qubit noise channels exposed via the Noise Model property, named
    # to match braket.default_simulator.noise_operations. Class-level: these
    # are plain constants, not PropertyDescriptors (which must stay inside
    # __init__ per this directory's AGENTS.md).
    _NOISE_CHANNELS = frozenset(
        ["depolarizing", "bit_flip", "phase_flip", "amplitude_damping", "phase_damping"]
    )
    # Matches a translated Braket-native gate line (see
    # braket_qasm.BRAKET_SAFE_BASIS: h, cx/cnot, rz, x) so a noise pragma can
    # be appended after it. Declarations, OPENQASM version pragmas, and
    # measurement lines never match this.
    _GATE_LINE_RE = re.compile(
        r'^(?P<gate>h|x|rz|cnot)\b(?:\([^)]*\))?\s+(?P<qargs>[^;]+);\s*$'
    )

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.shots = PropertyDescriptor(
            name="Shots",
            description="Number of times to sample the circuit.",
            required=True,
            default_value="1024",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.noise_model = PropertyDescriptor(
            name="Noise Model",
            description=(
                "Single-qubit noise channel injected via Braket QASM pragmas "
                "after each translated 1-qubit gate. 'none' (default) samples "
                "the ideal state-vector simulator (braket_sv), unchanged. Any "
                "other value switches to the density-matrix simulator "
                "(braket_dm), whose memory cost is 4^n."
            ),
            required=True,
            default_value="none",
            allowable_values=[
                "none",
                "depolarizing",
                "bit_flip",
                "phase_flip",
                "amplitude_damping",
                "phase_damping",
            ],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.error_probability = PropertyDescriptor(
            name="Error Probability",
            description=(
                "Error probability for the selected Noise Model's single-qubit "
                "channel. Ignored when Noise Model is none."
            ),
            required=False,
            default_value="0.01",
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [self.shots, self.noise_model, self.error_probability]

    def getPropertyDescriptors(self):
        return self.descriptors

    @classmethod
    def _inject_noise_pragmas(cls, qasm3_src, channel, error_probability):
        """Insert a `#pragma braket noise <channel>(<p>) q[i]` line after
        every 1-qubit gate in Braket-native QASM3 (h, x, rz — see
        braket_qasm.BRAKET_SAFE_BASIS), before ensure_full_register_measure
        appends the measurement block. These pragmas are only resolved by
        braket_dm, which the caller selects whenever Noise Model != none.
        2-qubit gates (cnot) are left alone — this processor does not model
        2-qubit-gate noise.
        """
        out_lines = []
        for line in qasm3_src.splitlines():
            out_lines.append(line)
            match = cls._GATE_LINE_RE.match(line.strip())
            if not match or match.group("gate") == "cnot":
                continue
            qubit = match.group("qargs").strip()
            out_lines.append(
                "#pragma braket noise {0}({1}) {2}".format(
                    channel, error_probability, qubit)
            )
        return "\n".join(out_lines)

    def transform(self, context, flowFile):
        # Translation only (see braket_qasm.py for why the circuit is
        # re-based onto Braket-native gates): the simulation below is pure
        # Braket; sim.translation flags the shared Qiskit front-end so
        # differential runs can account for it.
        from braket_qasm import ensure_full_register_measure, to_braket_qasm3

        shots = int(
            context.getProperty(self.shots)
            .evaluateAttributeExpressions(flowFile)
            .getValue()
        )
        noise_kind = (
            context.getProperty(self.noise_model)
            .evaluateAttributeExpressions(flowFile)
            .getValue() or "none"
        ).strip()

        fmt = flowFile.getAttribute("circuit.format")
        raw = bytes(flowFile.getContentsAsBytes())

        try:
            qasm3_src, note = to_braket_qasm3(fmt, raw.decode("utf-8"))
        except Exception as exc:
            self.logger.error("BraketSimulator: {}".format(exc))
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"sim.error": str(exc)},
            )
        extra = {"sim.translation": note}

        try:
            if noise_kind == "none":
                noise_attrs = {"sim.noise_model": "none"}
                backend = "braket_sv"
            elif noise_kind in self._NOISE_CHANNELS:
                error_probability = float(
                    context.getProperty(self.error_probability)
                    .evaluateAttributeExpressions(flowFile)
                    .getValue()
                )
                if not 0.0 <= error_probability <= 1.0:
                    raise ValueError("Error Probability must be between 0 and 1")
                qasm3_src = self._inject_noise_pragmas(
                    qasm3_src, noise_kind, error_probability)
                noise_attrs = {
                    "sim.noise_model": noise_kind,
                    "sim.noise_params": json.dumps(
                        {"probability": error_probability}, sort_keys=True),
                }
                backend = "braket_dm"
            else:
                raise ValueError("unsupported Noise Model '{}'".format(noise_kind))
        except Exception as exc:
            self.logger.error("BraketSimulator: {}".format(exc))
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"sim.error": str(exc)},
            )

        from braket.devices import LocalSimulator
        from braket.ir.openqasm import Program

        try:
            program = Program(source=ensure_full_register_measure(qasm3_src))

            # Suppress the "may not be supported on QPUs" advisory print so it
            # doesn't pollute NiFi logs.
            t0 = time.time()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                # Noise Model = none keeps the exact prior call
                # (LocalSimulator() -> braket_sv) so noiseless results don't
                # shift; noisy runs need the density-matrix backend, which is
                # the only local device that resolves noise pragmas.
                device = (
                    LocalSimulator("braket_dm")
                    if backend == "braket_dm"
                    else LocalSimulator()
                )
                result = device.run(program, shots=shots).result()
            elapsed = time.time() - t0
        except Exception as exc:
            self.logger.error("BraketSimulator: {}".format(exc))
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"sim.error": (
                    "Braket simulation failed: {}".format(exc))},
            )
        counts = result.measurement_counts

        sorted_counts = dict(
            sorted(((k, int(v)) for k, v in counts.items()),
                   key=lambda x: x[1], reverse=True))
        top_state, top_count = next(iter(sorted_counts.items()))

        return FlowFileTransformResult(
            relationship="success",
            contents=json.dumps(sorted_counts, indent=2).encode("utf-8"),
            attributes={
                "sim.shots":           str(shots),
                "sim.top_result":      top_state,
                "sim.top_probability": "{:.4f}".format(top_count / shots),
                "sim.framework":       "braket",
                "sim.component":       "BraketSimulator",
                "sim.backend":         backend,
                "sim.bit_order":       "q0_left",
                "report.type":         "simulation",
                "perf.elapsed_seconds": "{:.4f}".format(elapsed),
                **extra,
                **noise_attrs,
            },
        )
