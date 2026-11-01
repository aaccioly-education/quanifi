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

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


_BUILTIN_PHASES = {"T": (1, 8), "S": (1, 4), "Z": (1, 2)}


def _parse_phase(text):
    """Parse a phase in turns (phi, where U|1> = exp(2*pi*i*phi)|1>).

    Accepts a fraction ('3/8') or a decimal ('0.375'). Returned as an exact
    ``Fraction`` reduced modulo 1, so exact phases stay exact.
    """
    from fractions import Fraction
    phi = Fraction(str(text).strip())
    return phi - (phi.numerator // phi.denominator)


def _analytic_distribution(m, phi):
    """Ideal QPE readout distribution over m estimation qubits for phase phi.

    P(y) = | 2^-m * sum_k exp(2*pi*i*k*(phi - y/2^m)) |^2, y read MSB-first.
    """
    import numpy as np
    dim = 2 ** m
    k = np.arange(dim)
    out = np.empty(dim)
    for y in range(dim):
        amp = np.exp(2j * np.pi * k * (float(phi) - y / dim)).sum() / dim
        out[y] = abs(amp) ** 2
    return out


class PennylanePhaseEstimation(FlowFileTransform):
    """Build-only PennyLane Quantum Phase Estimation builder.

    Interchangeable with QiskitPhaseEstimation, CirqPhaseEstimation and
    QrispPhaseEstimation: same properties where they make sense, same
    ``circuit.*`` / ``result.*`` attribute contract, OpenQASM 2.0 output with
    no measurement (the downstream counts engine measures).

    Layout: estimation (phase) register = qubits 0..m-1, target qubit = m,
    prepared in |1>. Qubit 0 is the readout MSB, so the phase bits sit at
    string positions 0..m-1 of the canonical q0-left keys the simulators emit,
    exactly as for the Qiskit and Cirq builders.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Builds a Quantum Phase Estimation (QPE) circuit with PennyLane's native "
            "qml.QuantumPhaseEstimation template and emits it as unmeasured OpenQASM "
            "2.0 so any Quanifi counts engine can run it. The unitary is a "
            "single-qubit phase shift: a builtin T (1/8), S (1/4) or Z (1/2) gate, or "
            "an arbitrary phase given in turns (e.g. 3/8). The target qubit is "
            "prepared in |1>. The phase register is qubits 0..m-1 with qubit 0 as "
            "MSB. The builder validates the circuit against an analytic QPE "
            "distribution before emitting it."
        )
        tags = ["quantum", "pennylane", "qpe", "phase-estimation", "circuit"]
        # qiskit is needed for the OpenQASM 2.0 parse/validation and metrics,
        # not for building the circuit. NiFi installs ONLY what is declared here.
        dependencies = ["pennylane>=0.40", "qiskit>=2.0.0,<2.5", "numpy"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.phase_register_size = PropertyDescriptor(
            name="Phase Register Size",
            description=(
                "Number of qubits in the phase register. Precision of the phase "
                "estimate is 1/2^m where m is this value. "
                "3 qubits -> 1/8 precision; 4 -> 1/16; 5 -> 1/32."
            ),
            required=True,
            default_value="3",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.builtin_unitary = PropertyDescriptor(
            name="Builtin Unitary",
            description=(
                "Single-qubit unitary used when 'Phase' is empty. "
                "T gate: eigenphase = 1/8. S gate: eigenphase = 1/4. "
                "Z gate: eigenphase = 1/2."
            ),
            required=True,
            default_value="T",
            allowable_values=["T", "S", "Z"],
        )
        self.phase = PropertyDescriptor(
            name="Phase",
            description=(
                "Optional eigenphase in turns, phi in [0,1), for the unitary "
                "PhaseShift(2*pi*phi) acting on |1>. A fraction ('3/8') or a decimal "
                "('0.375'). When set it overrides 'Builtin Unitary'."
            ),
            required=False,
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.output_format = PropertyDescriptor(
            name="Output Format",
            description=(
                "Only 'qasm2' is supported: it is the format every counts engine in "
                "the library accepts, and what makes this builder interchangeable "
                "with the Qiskit, Cirq and Qrisp builders."
            ),
            required=True,
            default_value="qasm2",
            allowable_values=["qasm2"],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [
            self.phase_register_size,
            self.builtin_unitary,
            self.phase,
            self.output_format,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def _fail(self, msg, error_type=None):
        self.logger.error("PennylanePhaseEstimation: " + msg)
        attrs = {"qpe.error": msg}
        if error_type:
            attrs["qpe.error_type"] = error_type
        return FlowFileTransformResult(relationship="failure", contents=b"", attributes=attrs)

    def transform(self, context, flowFile):
        import math
        import numpy as np
        import pennylane as qml

        def get(prop):
            return (
                context.getProperty(prop)
                .evaluateAttributeExpressions(flowFile)
                .getValue()
            )

        try:
            m = int(get(self.phase_register_size))
            if m < 1:
                raise ValueError("Phase Register Size must be >= 1")
        except (TypeError, ValueError) as exc:
            return self._fail("bad numeric property value: {}".format(exc))

        builtin = get(self.builtin_unitary)
        phase_text = get(self.phase)
        fmt = get(self.output_format)
        try:
            if phase_text and phase_text.strip():
                phi = _parse_phase(phase_text)
                qpe_builtin = "phase({})".format(phase_text.strip())
            else:
                if builtin not in _BUILTIN_PHASES:
                    raise ValueError("unknown Builtin Unitary {!r}".format(builtin))
                from fractions import Fraction
                phi = Fraction(*_BUILTIN_PHASES[builtin])
                qpe_builtin = builtin
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            return self._fail("bad phase: {}".format(exc))

        # Compose mode (an incoming circuit as the unitary) is not supported:
        # PennyLane's OpenQASM export cannot represent an arbitrary matrix
        # unitary, so refuse loudly instead of silently ignoring the upstream circuit.
        if flowFile is not None and flowFile.getAttribute("circuit.format") \
                and len(bytes(flowFile.getContentsAsBytes())) > 0:
            self.logger.warn(
                "PennylanePhaseEstimation: incoming circuit ignored; compose mode "
                "is not supported by this builder (standalone only)")

        angle = 2 * math.pi * float(phi)
        phase_wires = list(range(m))
        target = m

        try:
            @qml.qnode(qml.device("default.qubit", wires=m + 1))
            def native():
                qml.PauliX(wires=target)
                qml.QuantumPhaseEstimation(
                    qml.PhaseShift(angle, wires=target), estimation_wires=phase_wires)
                return qml.probs(wires=phase_wires)

            expected = _analytic_distribution(m, phi)
            if not np.allclose(native(), expected, atol=1e-9, rtol=0):
                return self._fail(
                    "native PennyLane QPE fails independent analytic validation",
                    "validation_failed")
            raw = qml.to_openqasm(native, wires=list(range(m + 1)), measure_all=False)()
            diagram = qml.draw(native)()
        except Exception as exc:
            return self._fail("PennyLane circuit construction failed: {}".format(exc))

        try:
            from qiskit import qasm2, transpile
            from qiskit.quantum_info import Statevector

            circuit = qasm2.loads(
                raw, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
            measuring = False
            for inst in circuit.data:
                if inst.operation.name == "measure":
                    measuring = True
                elif measuring and inst.operation.name != "barrier":
                    raise ValueError("unexpected mid-circuit measurement in PennyLane export")
            circuit = circuit.remove_final_measurements(inplace=False)
            if circuit.num_clbits:
                raise ValueError("unexpected unused classical register in PennyLane export")
            exported = transpile(circuit, basis_gates=["h", "x", "cx", "rz"],
                                 optimization_level=0, seed_transpiler=19)
            qasm = qasm2.dumps(exported)

            # Validate what is actually emitted, in the canonical q0-left order.
            check = qasm2.loads(qasm)
            actual = np.zeros(2 ** m)
            for key, value in Statevector(check).probabilities_dict().items():
                actual[int(key[::-1][:m], 2)] += float(value)
            if check.num_qubits != m + 1 or not np.allclose(actual, expected, atol=1e-9, rtol=0):
                raise ValueError("exported QASM fails analytic validation")
        except Exception as exc:
            return self._fail(
                "emitted OpenQASM 2.0 failed export checks: {}".format(exc),
                "unrepresentable_primitive")

        ops = exported.count_ops()
        gate_count = sum(v for k, v in ops.items() if k not in ("barrier", "measure"))
        self.logger.warn("PennylanePhaseEstimation (m={}, unitary={}, qubits={}):\n{}".format(
            m, qpe_builtin, m + 1, diagram))

        attrs = {
            "circuit.format":                  fmt,
            "circuit.svg":                     "",   # blank stale Cirq SVG
            "circuit.qasm3":                   "",   # never emitted here
            "circuit.qasm2":                   qasm,
            "circuit.num_qubits":              str(m + 1),
            "circuit.phase_register_size":     str(m),
            "circuit.qpe_phase_register_size": str(m),
            "circuit.unitary_num_qubits":      "1",
            "circuit.qpe_target_qubits":       "1",
            "circuit.qpe_builtin":             qpe_builtin,
            "circuit.qpe_phase":               "{}/{}".format(phi.numerator, phi.denominator),
            "circuit.bit_order":               "canonical",
            "circuit.diagram":                 diagram,
            "circuit.depth":                   str(exported.depth()),
            "circuit.gate_count":              str(gate_count),
            "circuit.nonlocal_gates":          str(exported.num_nonlocal_gates()),
            "circuit.t_count":                 str(ops.get("t", 0) + ops.get("tdg", 0)),
            # Same declarative decode hint as the Qiskit/Cirq builders: phase
            # register = qubits 0..m-1, qubit 0 = MSB.
            "result.decode":                   "phase",
            "result.bit_positions":            ",".join(str(j) for j in range(m)),
            "result.label":                    "Estimated phase (φ)",
            "builder.component":               "PennylanePhaseEstimation",
            "builder.framework":               "pennylane",
        }
        return FlowFileTransformResult(
            relationship="success",
            contents=qasm.encode("utf-8"),
            attributes=attrs,
        )
