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

"""Build a bound native PennyLane QAOA circuit from a diagonal I/Z cost
Hamiltonian and explicit beta/gamma angles. Emits portable qasm2 plus
hamiltonian.json for any counts engine and QuantumQAOAEvaluator. One of the
five interchangeable NxM builder rows; see docs/guides/QAOA_COMPONENTS.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import pennylane_qaoa


class PennylaneQAOACircuit(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Builds the bound PennyLane QAOA circuit (qml.qaoa cost_layer/"
            "mixer_layer) for a diagonal I/Z cost Hamiltonian and explicit "
            "Betas/Gammas (beta then gamma) and emits portable OpenQASM 2.0 plus "
            "hamiltonian.json, so any counts engine can run it and "
            "QuantumQAOAEvaluator can score it. Convention: H on all qubits, then "
            "per layer exp(-i*gamma*H) and RX(2*beta). PennyLane emits the RX "
            "mixer as h.rz.h and no gphase; circuit.global_phase_dropped records "
            "whether any were stripped."
        )
        tags = ["quantum", "pennylane", "qaoa", "circuit", "qasm2", "builder"]
        dependencies = ["pennylane>=0.40"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.builder_descriptors()

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return qc.run_guarded(self, flowFile, lambda: self._run(context, flowFile))

    def _run(self, context, flowfile):
        props = qc.read_properties(self, context, flowfile)
        terms, n, raw = qc.read_cost_hamiltonian(flowfile)
        layers = qc.parse_layers(props["Layers"])
        betas = qc.parse_angles(props["Betas"], layers, "Betas")
        gammas = qc.parse_angles(props["Gammas"], layers, "Gammas")

        source, diagram, dropped = pennylane_qaoa.export(terms, n, betas, gammas)

        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(
                source, profile, "pennylane", "PennylaneQAOACircuit", diagram
            ),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
            "circuit.global_phase_dropped": "true" if dropped else "false",
        }
        return qc.success(source.encode(), attrs)
