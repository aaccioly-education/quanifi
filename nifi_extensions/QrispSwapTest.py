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

import json
import os
import sys
import contextlib
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QrispSwapTest(FlowFileTransform):
    """
    Executes the SWAP test (destructive fidelity/overlap test) using Qrisp.

    Given two quantum states |a> and |b>, the SWAP test measures their overlap
    |<a|b>|^2 using an ancilla qubit and a controlled-SWAP (Fredkin) gate:
      1. Prepare ancilla in |0>, apply H -> (|0> + |1>) / sqrt(2)
      2. Prepare |a> on register A, |b> on register B
      3. Controlled-SWAP between register A and B controlled by ancilla
      4. Apply H to ancilla and measure ancilla in computational basis

    The measurement probability of ancilla being |0> is P(0) = 1/2 + 1/2 * |<a|b>|^2.
    The state fidelity / overlap is computed as |<a|b>|^2 = 2 * P(0) - 1 = 1 - 2 * P(1).
    For identical states (|a>=|b>), P(0) = 1.0 (overlap = 1.0).
    For orthogonal states (<a|b>=0), P(0) = 0.5 (overlap = 0.0).
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Executes the SWAP test using Qrisp to measure quantum state fidelity / overlap "
            "|<a|b>|^2. Emits swap_test.overlap, ancilla measurement probabilities, and "
            "canonical simulation results."
        )
        tags = ["quantum", "qrisp", "swap-test", "overlap", "fidelity", "textbook"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5", "qiskit-qasm3-import"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.state_a = PropertyDescriptor(
            name="State A",
            description="Quantum state prepared on register A: zero (|0>), one (|1>), plus (|+>), or minus (|->).",
            required=True,
            default_value="zero",
            allowable_values=["zero", "one", "plus", "minus"],
        )
        self.state_b = PropertyDescriptor(
            name="State B",
            description="Quantum state prepared on register B: zero (|0>), one (|1>), plus (|+>), or minus (|->).",
            required=True,
            default_value="one",
            allowable_values=["zero", "one", "plus", "minus"],
        )
        self.shots = PropertyDescriptor(
            name="Shots",
            description="Number of simulation shots passed to get_measurement().",
            required=True,
            default_value="1024",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [self.state_a, self.state_b, self.shots]

    def getPropertyDescriptors(self):
        return self.descriptors

    @staticmethod
    def _prep_state(qv, state_name):
        from qrisp import x, h
        if state_name == "zero":
            pass
        elif state_name == "one":
            x(qv)
        elif state_name == "plus":
            h(qv)
        elif state_name == "minus":
            x(qv)
            h(qv)
        else:
            raise ValueError(f"Unknown state '{state_name}'")

    def transform(self, context, flowFile):
        from qrisp import QuantumVariable, QuantumBool, h, control, swap
        from qiskit import qasm2 as qiskit_qasm2

        def get(prop):
            return (
                context.getProperty(prop)
                .evaluateAttributeExpressions(flowFile)
                .getValue()
            )

        state_a_str = get(self.state_a)
        state_b_str = get(self.state_b)
        try:
            shots = int(get(self.shots))
        except (TypeError, ValueError) as exc:
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"swap_test.error": f"Invalid Shots value: {exc}"},
            )

        # Allocate ancilla, register A, register B
        anc = QuantumBool()
        qa = QuantumVariable(1)
        qb = QuantumVariable(1)

        # Prepare states on registers A and B
        try:
            self._prep_state(qa, state_a_str)
            self._prep_state(qb, state_b_str)
        except Exception as exc:
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"swap_test.error": f"State preparation failed: {exc}"},
            )

        # 1. Ancilla in |+>
        h(anc)

        # 2. Controlled SWAP between qa and qb
        with control(anc):
            swap(qa, qb)

        # 3. Final Hadamard on ancilla
        h(anc)

        # Measure ancilla
        raw_counts = anc.get_measurement(shots=shots)
        total_samples = sum(raw_counts.values())
        p0 = raw_counts.get(False, 0) / total_samples if total_samples > 0 else 0.0
        p1 = raw_counts.get(True, 0) / total_samples if total_samples > 0 else 0.0

        # Overlap: |<a|b>|^2 = 2 * P(0) - 1, clamped to [0.0, 1.0]
        overlap = max(0.0, min(1.0, 2.0 * p0 - 1.0))

        counts_str = {
            "0": raw_counts.get(False, 0),
            "1": raw_counts.get(True, 0),
        }
        sorted_counts = dict(sorted(counts_str.items(), key=lambda item: item[1], reverse=True))
        top_state, top_count = next(iter(sorted_counts.items()))
        top_prob = top_count / total_samples if total_samples > 0 else 0.0

        qasm2_str = ""
        circuit_diagram = ""
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                qk = anc.qs.compile().to_qiskit()
            qasm2_str = qiskit_qasm2.dumps(qk)
            circuit_diagram = str(qk.draw("text"))
        except Exception as exc:
            self.logger.warn(f"Circuit export skipped: {exc}")

        attrs = {
            "circuit.format": "qasm2",
            "circuit.framework": "qrisp",
            "circuit.num_qubits": "3",
            "swap_test.state_a": state_a_str,
            "swap_test.state_b": state_b_str,
            "swap_test.overlap": f"{overlap:.4f}",
            "swap_test.p_zero": f"{p0:.4f}",
            "swap_test.p_one": f"{p1:.4f}",
            "sim.shots": str(shots),
            "sim.top_result": top_state,
            "sim.top_probability": f"{top_prob:.4f}",
            "sim.bit_order": "q0_left",
            "sim.framework": "qrisp",
            "report.type": "simulation",
        }
        if qasm2_str:
            attrs["circuit.qasm2"] = qasm2_str
        if circuit_diagram:
            attrs["circuit.diagram"] = circuit_diagram

        return FlowFileTransformResult(
            relationship="success",
            contents=json.dumps(sorted_counts, indent=2).encode("utf-8"),
            attributes=attrs,
        )
