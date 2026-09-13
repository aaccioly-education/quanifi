import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QrispTruthTableSynthesis(FlowFileTransform):
    """
    Reversible Logic Synthesizer using Qrisp's logic synthesis engine.

    Compiles arbitrary classical boolean truth tables into reversible quantum circuits
    using Gray-code synthesis or PPRM (Positive Polarity Reed-Muller) synthesis.

    Truth Table Input format:
      - JSON list of column bitstrings across all 2^n inputs, e.g. ["0001"] for a 2-input AND gate
        (inputs 00->0, 01->0, 10->0, 11->1).
      - Multi-output boolean functions: e.g. ["0001", "0110"] for a half-adder (carry and sum).
      - Alternatively, a raw string of bits whose length is an exact power of 2 (e.g. "0001").

    Emits:
      - FlowFile content: OpenQASM representation of the synthesized reversible circuit.
      - FlowFile attributes: circuit.format, circuit.framework, circuit.num_qubits, circuit.depth,
        circuit.gate_count, circuit.diagram, report.type=simulation.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Compiles classical boolean truth tables into reversible quantum circuits using Qrisp. "
            "Supports Gray-code and PPRM logic synthesis methods."
        )
        tags = ["quantum", "qrisp", "logic", "synthesis", "truth-table", "reversible", "circuit"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5", "qiskit-qasm3-import"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.truth_table = PropertyDescriptor(
            name="Truth Table Spec",
            description=(
                "Specification of the truth table. Can be a JSON list of bitstrings (e.g. ['0001']), "
                "or a single bitstring whose length is an integer power of 2 (e.g. '0001'). "
                "Ignored if incoming FlowFile content contains a non-empty truth table specification."
            ),
            required=True,
            default_value="[\"0001\"]",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.method = PropertyDescriptor(
            name="Synthesis Method",
            description="Synthesis algorithm to synthesize the truth table: gray, gray_pt, pprm, or pprm_pt.",
            required=True,
            default_value="gray",
            allowable_values=["gray", "gray_pt", "pprm", "pprm_pt"],
        )
        self.output_format = PropertyDescriptor(
            name="Output Format",
            description="Output circuit serialization format.",
            required=True,
            default_value="qasm2",
            allowable_values=["qasm2", "qasm3"],
        )
        self.descriptors = [self.truth_table, self.method, self.output_format]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        import contextlib
        import numpy as np
        from qrisp import QuantumVariable
        from qrisp.alg_primitives.logic_synthesis.truth_tables import TruthTable
        from qiskit import qasm2 as qiskit_qasm2
        from qiskit.compiler import transpile as qk_transpile

        def get(prop):
            return (
                context.getProperty(prop)
                .evaluateAttributeExpressions(flowFile)
                .getValue()
            )

        method = get(self.method)
        fmt = get(self.output_format)

        # Retrieve truth table from content or property
        raw_bytes = bytes(flowFile.getContentsAsBytes()) if flowFile else b""
        raw_spec = raw_bytes.decode("utf-8").strip() if raw_bytes else ""
        if not raw_spec:
            raw_spec = get(self.truth_table).strip()

        # Parse truth table specification
        spec_list = None
        try:
            parsed = json.loads(raw_spec)
            if isinstance(parsed, list):
                spec_list = [str(x) for x in parsed]
            elif isinstance(parsed, str):
                spec_list = [parsed]
        except Exception:
            if all(c in "01" for c in raw_spec):
                spec_list = [raw_spec]

        if not spec_list or not all(len(s) == len(spec_list[0]) for s in spec_list):
            msg = f"Invalid truth table specification: {raw_spec}"
            self.logger.error("QrispTruthTableSynthesis: " + msg)
            return FlowFileTransformResult(
                relationship="failure",
                contents=b"",
                attributes={"synth.error": msg},
            )

        num_rows = len(spec_list[0])
        num_outputs = len(spec_list)
        num_inputs = int(np.round(np.log2(num_rows)))
        if 2 ** num_inputs != num_rows:
            msg = f"Truth table length {num_rows} is not a power of 2"
            self.logger.error("QrispTruthTableSynthesis: " + msg)
            return FlowFileTransformResult(
                relationship="failure",
                contents=b"",
                attributes={"synth.error": msg},
            )

        try:
            tt = TruthTable(spec_list)
            in_var = QuantumVariable(num_inputs)
            out_var = QuantumVariable(num_outputs)
            tt.q_synth(in_var, out_var, method=method)
            qk = in_var.qs.compile().to_qiskit()
        except Exception as exc:
            msg = f"Synthesis failed: {exc}"
            self.logger.error("QrispTruthTableSynthesis: " + msg)
            return FlowFileTransformResult(
                relationship="failure",
                contents=b"",
                attributes={"synth.error": msg},
            )

        # Transpile for clean export
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
            self.logger.error("QrispTruthTableSynthesis: " + msg)
            return FlowFileTransformResult(
                relationship="failure",
                contents=b"",
                attributes={"synth.error": msg},
            )

        circuit_diagram = str(qk.draw("text"))
        num_qubits = in_var.qs.num_qubits()
        ops = qk_export.count_ops()
        gate_count = sum(v for k, v in ops.items() if k not in ("barrier", "measure"))
        depth = qk_export.depth()

        attrs = {
            "circuit.format": fmt,
            "circuit.framework": "qrisp",
            "circuit.num_qubits": str(num_qubits),
            "circuit.depth": str(depth),
            "circuit.gate_count": str(gate_count),
            "circuit.diagram": circuit_diagram,
            "synth.method": method,
            "synth.num_inputs": str(num_inputs),
            "synth.num_outputs": str(num_outputs),
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
