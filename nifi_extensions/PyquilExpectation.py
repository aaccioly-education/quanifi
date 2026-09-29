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

"""Evaluate a Hermitian Pauli-sum expectation exactly using pyQuil local wavefunction simulation. Reads a qasm2/qasm3 circuit and an indexed Pauli expression or neutral Hamiltonian JSON property. Outputs expectation JSON, not shot counts."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilExpectation(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Evaluate a Hermitian Pauli-sum expectation exactly using pyQuil local wavefunction simulation. Reads a qasm2/qasm3 circuit and an indexed Pauli expression or neutral Hamiltonian JSON property. Outputs expectation JSON, not shot counts."
        tags = ["quantum", "pyquil", "rigetti", "expectation", "observable", "vqe"]
        dependencies = [
            "pyquil>=4.18",
            "numpy>=1.26",
            "qiskit>=2.0.0,<2.5",
            "qiskit-qasm3-import>=0.6",
        ]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                (
                    "Hamiltonian",
                    "${hamiltonian.json:replaceEmpty('Z0')}",
                    "Indexed Pauli expression (e.g. Z0 + 0.5 X0 X1) or sparse_pauli_op_json. By default use upstream hamiltonian.json, otherwise Z0.",
                    None,
                )
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "expectation")

    def _run(self, props, flowfile):
        import json
        from pyquil_components import expectation, hamiltonian
        from pyquil_processor import read_circuit, success
        from pauli_dsl import parse_pauli_sum, terms_to_wire

        program = read_circuit(flowfile)
        spec = props["Hamiltonian"].strip()
        data = json.loads(spec) if spec.startswith("{") else None
        if (
            isinstance(data, dict)
            and "num_qubits" in data
            and data.get("terms")
            and isinstance(data["terms"][0], list)
        ):
            terms, n = hamiltonian(spec)
        else:
            terms, n = parse_pauli_sum(spec, program.num_qubits)
            if any(
                len(set(indices)) != len(indices)
                or any(i < 0 or i >= n for i in indices)
                for _, indices, _ in terms
            ):
                raise ValueError(
                    "Hamiltonian term has duplicate or out-of-range qubits"
                )
            terms, n = hamiltonian(terms_to_wire(terms, n))
        if n != program.num_qubits:
            raise ValueError("Hamiltonian and circuit widths must match")
        value = expectation(program, terms)
        payload = {
            "expectation": value,
            "num_qubits": n,
            "method": "exact_statevector",
            "framework": "pyquil",
        }
        return success(
            json.dumps(payload).encode(),
            {
                "expectation.value": repr(value),
                "expectation.method": "exact_statevector",
                "expectation.framework": "pyquil",
                "expectation.error": "",
                "report.type": "",
                "mime.type": "application/json",
            },
        )
