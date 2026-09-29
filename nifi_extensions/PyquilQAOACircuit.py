"""Build a bound native pyQuil QAOA circuit from a diagonal I/Z cost
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
import pyquil_processor


class PyquilQAOACircuit(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.2.0"
        description = (
            "Builds the bound native pyQuil QAOA circuit (pyquil_components.qaoa) "
            "for a diagonal I/Z cost Hamiltonian and explicit Betas/Gammas (beta "
            "then gamma) and emits portable OpenQASM 2.0 plus hamiltonian.json, so "
            "any counts engine can run it and QuantumQAOAEvaluator can score it. "
            "Convention: H on all qubits, then per layer exp(-i*gamma*H) and "
            "RX(2*beta)."
        )
        tags = ["quantum", "pyquil", "rigetti", "qaoa", "circuit", "qasm2", "builder"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26"]

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

        source, diagram = pyquil_processor.qaoa_export(terms, n, betas, gammas)

        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(
                source, profile, "pyquil", "PyquilQAOACircuit", diagram
            ),
            **qc.builder_attributes(raw, n, layers, betas, gammas),
        }
        return qc.success(source.encode(), attrs)
