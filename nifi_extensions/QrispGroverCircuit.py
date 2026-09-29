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

"""Qrisp's tag_state reads a binary string little-endian (its rightmost
character is qv[0]), while the Grover contract types Marked State q0-left.
The builder tags the reversed string, so every counts engine reads the marked
state exactly as typed; without the reversal all three simulators return the
bit-reversed state.
"""

import contextlib
import io

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import (
    PropertyDescriptor,
    StandardValidators,
    ExpressionLanguageScope,
)
from nifiapi.__jvm__ import JvmHolder

MAX_QUBITS = 8
BASIS_GATES = ["h", "cx", "rz", "x"]


class QrispGroverCircuit(FlowFileTransform):

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        tags = ["quantum", "qrisp", "grover", "circuit", "qasm2", "builder"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5"]
        description = (
            "Builds a Grover search circuit for a target bitstring with Qrisp "
            "(tag_state phase oracle + grovers_alg diffuser, fixed Num Iterations) "
            "and outputs the unmeasured circuit as portable OpenQASM 2.0 (one qreg, "
            "gates h/x/cx/rz), so any counts engine can run it. Marked State is "
            "q0-left (qubit 0 = leftmost character), like the Qiskit, Cirq, "
            "PennyLane and pyQuil Grover builders. 1-8 qubits."
        )

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()

        self.marked_state = PropertyDescriptor(
            name="Marked State",
            description=(
                "Target bitstring Grover will search for, e.g. '110'. "
                "Length sets the number of qubits. Bit order is left-to-right "
                "(qubit 0 = leftmost character)."
            ),
            required=True,
            default_value="11",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.num_iterations = PropertyDescriptor(
            name="Num Iterations",
            description=(
                "Number of Grover operator applications. "
                "Optimal is roughly floor(pi/4 * sqrt(2^n)) for one marked state."
            ),
            required=True,
            default_value="1",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.output_format = PropertyDescriptor(
            name="Output Format",
            description=(
                "Format used to serialize the circuit into the FlowFile content. "
                "Only 'qasm2' is supported: it is the format every counts engine in "
                "the library accepts, and it is what makes this builder "
                "interchangeable with the Qiskit, Cirq and Qrisp builders."
            ),
            required=True,
            default_value="qasm2",
            allowable_values=["qasm2"],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [
            self.marked_state,
            self.num_iterations,
            self.output_format,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def _failure(self, flowFile, msg):
        self.logger.error("QrispGroverCircuit: " + msg)
        return FlowFileTransformResult(
            relationship="failure",
            contents=bytes(flowFile.getContentsAsBytes() or b""),
            attributes={"grover.error": msg},
        )

    def transform(self, context, flowFile):
        def get(prop):
            return (
                context.getProperty(prop)
                .evaluateAttributeExpressions(flowFile)
                .getValue()
            )

        target = (get(self.marked_state) or "").strip()
        try:
            num_iterations = int(get(self.num_iterations))
        except (TypeError, ValueError) as exc:
            return self._failure(flowFile, "bad numeric property value: {}".format(exc))
        if num_iterations < 1:
            return self._failure(
                flowFile,
                "Num Iterations must be >= 1, got {}".format(num_iterations),
            )
        if not target or set(target) - {"0", "1"}:
            return self._failure(
                flowFile,
                "Marked State must be a bitstring, got {!r}".format(target),
            )
        n = len(target)
        if n > MAX_QUBITS:
            return self._failure(
                flowFile,
                "Marked State must have 1..8 bits, got {}".format(n),
            )
        fmt = get(self.output_format)
        if fmt != "qasm2":
            return self._failure(
                flowFile,
                "Output Format must be qasm2, got {!r}".format(fmt),
            )

        try:
            from qiskit import QuantumCircuit, qasm2, transpile
            from qrisp import QuantumVariable
            from qrisp.grover import grovers_alg, tag_state

            # Suppress Qrisp's tqdm output: NiFi's py4j bridge uses stdout.
            with contextlib.redirect_stdout(io.StringIO()):
                qv = QuantumVariable(n)

                def oracle(variable):
                    tag_state({variable: target[::-1]}, binary_values=True)

                grovers_alg(qv, oracle, iterations=num_iterations)
                compiled = qv.qs.compile().to_qiskit()
            if compiled.num_qubits != n:
                raise ValueError(
                    "Qrisp compiled circuit has {} qubits, expected {}".format(
                        compiled.num_qubits, n
                    )
                )
            single = QuantumCircuit(n)
            single.compose(compiled, qubits=list(range(n)), inplace=True)
            emitted = transpile(single, basis_gates=BASIS_GATES, optimization_level=0)
            source = qasm2.dumps(emitted)
            # Foreign-parser self-check.
            parsed = qasm2.loads(
                source, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS
            )
            diagram = str(single.draw("text"))
        except Exception as exc:
            return self._failure(
                flowFile, "Qrisp circuit construction failed: {}".format(exc)
            )

        ops = parsed.count_ops()
        attrs = {
            "circuit.format": "qasm2",
            "circuit.qasm2": source,
            "circuit.qasm3": "",
            "circuit.cirq_json": "",
            "circuit.svg": "",
            "circuit.num_qubits": str(n),
            "circuit.marked_state": target,
            "circuit.num_iterations": str(num_iterations),
            "circuit.bit_order": "q0_left",
            "circuit.diagram": diagram,
            "circuit.depth": str(parsed.depth()),
            "circuit.gate_count": str(
                sum(v for k, v in ops.items() if k not in ("barrier", "measure"))
            ),
            "circuit.nonlocal_gates": str(parsed.num_nonlocal_gates()),
            "circuit.t_count": str(ops.get("t", 0) + ops.get("tdg", 0)),
            "builder.component": "QrispGrover",
            "builder.framework": "qrisp",
            "grover.error": "",
        }

        return FlowFileTransformResult(
            relationship="success",
            contents=source.encode("utf-8"),
            attributes=attrs,
        )
