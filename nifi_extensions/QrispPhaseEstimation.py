import json

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QrispPhaseEstimation(FlowFileTransform):

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Quantum Phase Estimation (QPE) using Qrisp. Estimates the eigenphase φ of a "
            "unitary operator U such that U|ψ⟩ = exp(2πiφ)|ψ⟩. "
            "Uses Qrisp's QPE primitive with iter_spec=True for efficient "
            "controlled-U^(2^k) application. "
            "Built-in unitaries: T (φ=1/8), S (φ=1/4), Z (φ=1/2). "
            "The target register is prepared in the |1⟩ eigenstate. "
            "Outputs a probability distribution over estimated phase values (as floats 0–1). "
            "Sets qpe.top_phase to the most likely phase; also exports the circuit as qasm2."
        )
        tags = ["quantum", "qrisp", "qpe", "phase-estimation", "circuit"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.phase_register_size = PropertyDescriptor(
            name="Phase Register Size",
            description=(
                "Number of qubits in the phase register. Precision = 1 / 2^m. "
                "3 qubits gives 1/8 precision; 4 qubits gives 1/16."
            ),
            required=True,
            default_value="3",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.builtin_unitary = PropertyDescriptor(
            name="Builtin Unitary",
            description=(
                "Gate to use as the unitary. "
                "T: eigenphase = 1/8 (0.125).  "
                "S: eigenphase = 1/4 (0.25).  "
                "Z: eigenphase = 1/2 (0.5)."
            ),
            required=True,
            default_value="T",
            allowable_values=["T", "S", "Z"],
        )
        self.shots = PropertyDescriptor(
            name="Shots",
            description="Number of measurement shots.",
            required=True,
            default_value="1024",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.output_mode = PropertyDescriptor(
            name="Output Mode",
            description="Execution mode: simulate (returns measurement JSON) or circuit (emits QPE circuit as QASM).",
            required=True,
            default_value="simulate",
            allowable_values=["simulate", "circuit"],
        )
        self.descriptors = [self.phase_register_size, self.builtin_unitary, self.shots, self.output_mode]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        import contextlib
        import io
        import numpy as np
        from qrisp import QuantumVariable, QuantumCircuit, QPE, x, p
        from qiskit import qasm2 as qiskit_qasm2
        from qiskit.compiler import transpile as qk_transpile

        get = lambda prop: (
            context.getProperty(prop)
            .evaluateAttributeExpressions(flowFile)
            .getValue()
        )

        try:
            m = int(get(self.phase_register_size))
        except (TypeError, ValueError) as exc:
            msg = "bad numeric property value: {}".format(exc)
            self.logger.error("QrispPhaseEstimation: " + msg)
            return FlowFileTransformResult(
                relationship="failure", contents=b"",
                attributes={"qpe.error": msg},
            )
        builtin = get(self.builtin_unitary)
        try:
            shots = int(get(self.shots))
        except (TypeError, ValueError) as exc:
            msg = "bad numeric property value: {}".format(exc)
            self.logger.error("QrispPhaseEstimation: " + msg)
            return FlowFileTransformResult(
                relationship="failure", contents=b"",
                attributes={"qpe.error": msg},
            )
        out_mode = get(self.output_mode)

        incoming_fmt = flowFile.getAttribute("circuit.format") if flowFile else None
        raw = bytes(flowFile.getContentsAsBytes()) if flowFile else b""

        if incoming_fmt and raw:
            # --- Compose mode: consume upstream circuit representation --------
            try:
                raw_str = raw.decode("utf-8")
                if incoming_fmt == "qasm2":
                    qk_u = qiskit_qasm2.loads(
                        raw_str,
                        custom_instructions=qiskit_qasm2.LEGACY_CUSTOM_INSTRUCTIONS,
                    )
                elif incoming_fmt == "qasm3":
                    from qiskit import qasm3 as qiskit_qasm3
                    qk_u = qiskit_qasm3.loads(raw_str)
                elif incoming_fmt == "qpy":
                    from qiskit import qpy
                    qk_u = qpy.load(io.BytesIO(raw))[0]
                else:
                    raise ValueError(f"Unsupported incoming circuit format: {incoming_fmt}")

                qrisp_u = QuantumCircuit.from_qiskit(qk_u)
                u_gate = qrisp_u.to_gate()
                num_target = qrisp_u.num_qubits()

                def U(qv, iter=1):
                    for _ in range(iter):
                        qv.qs.append(u_gate, list(qv))

                target = QuantumVariable(num_target)
                x(target)
                res = QPE(target, U, precision=m, iter_spec=True)
                mode_label = "compose"
            except Exception as exc:
                msg = f"Compose mode failed to load upstream unitary: {exc}"
                self.logger.error("QrispPhaseEstimation: " + msg)
                return FlowFileTransformResult(
                    relationship="failure", contents=b"",
                    attributes={"qpe.error": msg},
                )
        else:
            # --- Standalone mode: demo QPE for builtin gate -------------------
            gate_phases = {"T": np.pi / 4, "S": np.pi / 2, "Z": np.pi}
            phase_angle = gate_phases.get(builtin, np.pi / 4)

            def U(qv, iter=1):
                p(phase_angle * iter, qv[0])

            target = QuantumVariable(1)
            x(target)  # Prepare |1⟩ eigenstate
            res = QPE(target, U, precision=m, iter_spec=True)
            mode_label = f"standalone({builtin})"

        # Export circuit as qasm2
        qasm2_str = ""
        diagram = ""
        try:
            qk = res.qs.compile().to_qiskit()
            qk_export = qk_transpile(
                qk,
                basis_gates=['h', 'cx', 'rz', 'x', 'swap', 's', 't', 'sdg', 'tdg'],
                optimization_level=0,
            )
            qasm2_str = qiskit_qasm2.dumps(qk_export)
            diagram = str(qk.draw('text'))
        except Exception as exc:
            self.logger.warn("QPE QASM2 export skipped: {}".format(exc))

        if out_mode == "circuit":
            # Decomposed circuit output mode for downstream simulator consumption
            ops = qk_export.count_ops()
            gate_count = sum(v for k, v in ops.items() if k not in ("barrier", "measure"))
            depth = qk_export.depth()
            attrs = {
                "circuit.format": "qasm2",
                "circuit.qasm2": qasm2_str,
                "circuit.framework": "qrisp",
                "circuit.num_qubits": str(res.qs.num_qubits()),
                "circuit.depth": str(depth),
                "circuit.gate_count": str(gate_count),
                "circuit.qpe_phase_register_size": str(m),
                "qpe.precision": str(m),
                "qpe.mode": mode_label,
                "qpe.framework": "qrisp",
                "report.type": "simulation",
            }
            if diagram:
                attrs["circuit.diagram"] = diagram
            return FlowFileTransformResult(
                relationship="success",
                contents=qasm2_str.encode("utf-8"),
                attributes=attrs,
            )

        # Simulation output mode
        with contextlib.redirect_stdout(io.StringIO()):
            measurement = res.get_measurement(shots=shots)

        sorted_m = dict(sorted(measurement.items(), key=lambda kv: kv[1], reverse=True))
        top_phase, top_prob = next(iter(sorted_m.items()))
        result_json = {str(k): v for k, v in sorted_m.items()}

        attrs = {
            "qpe.top_phase": str(top_phase),
            "qpe.top_probability": f"{top_prob:.4f}",
            "qpe.precision": str(m),
            "qpe.builtin": builtin if not incoming_fmt else "custom",
            "qpe.mode": mode_label,
            "qpe.framework": "qrisp",
            "qpe.shots": str(shots),
            "report.type": "simulation",
        }
        if qasm2_str:
            attrs["circuit.format"] = "qasm2"
            attrs["circuit.qasm2"] = qasm2_str
        if diagram:
            attrs["circuit.diagram"] = diagram

        return FlowFileTransformResult(
            relationship="success",
            contents=json.dumps(result_json, indent=2).encode("utf-8"),
            attributes=attrs,
        )
