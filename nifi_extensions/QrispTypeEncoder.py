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

import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QrispTypeEncoder(FlowFileTransform):
    """
    High-level Typed Quantum Data Structure Encoder for Qrisp.

    Encodes classical floating-point numbers or numeric arrays into typed quantum registers
    (QuantumFloat or QuantumArray) with automatic scaling and bit-width inference, then
    compiles the state initialization circuit into OpenQASM.

    Supports:
      - QuantumFloat: encodes a single scalar (e.g. 3.5).
      - QuantumArray: encodes an array of scalars into a 1D quantum tensor.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Encodes classical numbers or arrays into typed quantum registers (QuantumFloat/QuantumArray) "
            "using Qrisp, generating the corresponding quantum initialization circuit."
        )
        tags = ["quantum", "qrisp", "quantum-float", "quantum-array", "typed", "circuit"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5", "qiskit-qasm3-import"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.data_type = PropertyDescriptor(
            name="Data Type",
            description="Type of quantum variable to allocate: QuantumFloat or QuantumArray.",
            required=True,
            default_value="QuantumFloat",
            allowable_values=["QuantumFloat", "QuantumArray"],
        )
        self.value = PropertyDescriptor(
            name="Value",
            description=(
                "Value to encode: float/int for QuantumFloat (e.g. 3.5), or JSON array for QuantumArray "
                "(e.g. [1.0, 2.0, 3.5]). Ignored if incoming FlowFile content contains non-empty text."
            ),
            required=True,
            default_value="3.5",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.bit_width = PropertyDescriptor(
            name="Bit Width",
            description="Number of qubits per scalar element (0 = auto-infer from value).",
            required=True,
            default_value="4",
            validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.exponent = PropertyDescriptor(
            name="Exponent",
            description="Power of 2 for the least significant bit (e.g. -1 for step size 0.5; 0 for integers).",
            required=True,
            default_value="-1",
            validators=[StandardValidators.INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.signed = PropertyDescriptor(
            name="Signed",
            description="Whether the quantum float uses two's complement signed representation.",
            required=True,
            default_value="false",
            allowable_values=["true", "false"],
        )
        self.output_format = PropertyDescriptor(
            name="Output Format",
            description="Output circuit serialization format.",
            required=True,
            default_value="qasm2",
            allowable_values=["qasm2", "qasm3"],
        )
        self.descriptors = [
            self.data_type,
            self.value,
            self.bit_width,
            self.exponent,
            self.signed,
            self.output_format,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        import numpy as np
        from qrisp import QuantumFloat, QuantumArray
        from qiskit import qasm2 as qiskit_qasm2
        from qiskit.compiler import transpile as qk_transpile

        def get(prop):
            return (
                context.getProperty(prop)
                .evaluateAttributeExpressions(flowFile)
                .getValue()
            )

        dtype = get(self.data_type)
        fmt = get(self.output_format)
        is_signed = get(self.signed).lower() == "true"

        try:
            m = int(get(self.bit_width))
            exp = int(get(self.exponent))
        except (TypeError, ValueError) as exc:
            msg = f"Invalid numeric parameters: {exc}"
            self.logger.error("QrispTypeEncoder: " + msg)
            return FlowFileTransformResult(
                relationship="failure", contents=b"", attributes={"encode.error": msg}
            )

        raw_bytes = bytes(flowFile.getContentsAsBytes()) if flowFile else b""
        raw_val = raw_bytes.decode("utf-8").strip() if raw_bytes else ""
        if not raw_val:
            raw_val = get(self.value).strip()

        # Parse value
        try:
            if dtype == "QuantumFloat":
                val = float(raw_val)
                # Auto-inference if m == 0
                if m == 0:
                    int_part = abs(int(val))
                    m_int = max(1, math.ceil(math.log2(int_part + 1))) if int_part > 0 else 1
                    frac_part = abs(val - int(val))
                    m_frac = max(0, -exp) if frac_part > 0 else 0
                    m = m_int + m_frac + (1 if is_signed else 0)
                qf = QuantumFloat(m, exp, signed=is_signed)
                qf[:] = val
                qs = qf.qs
                encoded_desc = str(val)
                num_elements = 1
            else:
                arr = json.loads(raw_val) if (raw_val.startswith("[") and raw_val.endswith("]")) else [float(x.strip()) for x in raw_val.split(",")]
                arr = [float(x) for x in arr]
                if m == 0:
                    max_val = max(abs(x) for x in arr) if arr else 1.0
                    m_int = max(1, math.ceil(math.log2(int(max_val) + 1)))
                    m = m_int + max(0, -exp) + (1 if is_signed else 0)
                qtype = QuantumFloat(m, exp, signed=is_signed)
                qa = QuantumArray(qtype=qtype, shape=(len(arr),))
                qa[:] = arr
                qs = qa.qs
                encoded_desc = json.dumps(arr)
                num_elements = len(arr)

            qk = qs.compile().to_qiskit()
        except Exception as exc:
            msg = f"Encoding failed: {exc}"
            self.logger.error("QrispTypeEncoder: " + msg)
            return FlowFileTransformResult(
                relationship="failure", contents=b"", attributes={"encode.error": msg}
            )

        qk_export = qk_transpile(
            qk,
            basis_gates=['h', 'cx', 'rz', 'x', 'swap', 's', 't', 'sdg', 'tdg'],
            optimization_level=1,
        )

        qasm2_str = ""
        qasm3_str = ""
        try:
            if fmt == "qasm2":
                qasm2_str = qiskit_qasm2.dumps(qk_export)
                contents = qasm2_str.encode("utf-8")
            else:
                from qiskit import qasm3 as qiskit_qasm3
                qasm3_str = qiskit_qasm3.dumps(qk_export)
                contents = qasm3_str.encode("utf-8")
        except Exception as exc:
            msg = f"Serialization failed: {exc}"
            self.logger.error("QrispTypeEncoder: " + msg)
            return FlowFileTransformResult(
                relationship="failure", contents=b"", attributes={"encode.error": msg}
            )

        diagram = str(qk.draw("text"))
        num_qubits = qs.num_qubits()
        ops = qk_export.count_ops()
        gate_count = sum(v for k, v in ops.items() if k not in ("barrier", "measure"))
        depth = qk_export.depth()

        attrs = {
            "circuit.format": fmt,
            "circuit.framework": "qrisp",
            "circuit.num_qubits": str(num_qubits),
            "circuit.depth": str(depth),
            "circuit.gate_count": str(gate_count),
            "circuit.diagram": diagram,
            "qtype.type": dtype,
            "qtype.value": encoded_desc,
            "qtype.bit_width": str(m),
            "qtype.exponent": str(exp),
            "qtype.num_elements": str(num_elements),
            "report.type": "simulation",
        }
        if qasm2_str:
            attrs["circuit.qasm2"] = qasm2_str
        if qasm3_str:
            attrs["circuit.qasm3"] = qasm3_str

        return FlowFileTransformResult(
            relationship="success",
            contents=contents,
            attributes=attrs,
        )
