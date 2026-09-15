"""Build a bound native pyQuil QAOA circuit from a diagonal I/Z cost Hamiltonian and explicit beta/gamma angles. Emits qasm2 plus hamiltonian.json for PyquilExpectation, or connects directly to a simulator. Minimization convention: exp(-i gamma H), then RX(2 beta)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilQAOACircuit(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Build a bound native pyQuil QAOA circuit from a diagonal I/Z cost Hamiltonian and explicit beta/gamma angles. Emits qasm2 plus hamiltonian.json for PyquilExpectation, or connects directly to a simulator. Minimization convention: exp(-i gamma H), then RX(2 beta)."
        tags = ["quantum", "pyquil", "rigetti", "qaoa", "circuit", "composition"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                ("Layers", "1", "Number of cost/mixer layers, 1..16.", None),
                (
                    "Betas",
                    "0.39269908169872414",
                    "JSON/comma-separated mixer angles, one per layer.",
                    None,
                ),
                (
                    "Gammas",
                    "0.7853981633974483",
                    "JSON/comma-separated cost angles, one per layer.",
                    None,
                ),
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "qaoa")

    def _run(self, props, flowfile):
        from pyquil_components import qaoa, angles, integer
        from pyquil_processor import read_hamiltonian, circuit_result

        terms, n, raw = read_hamiltonian(flowfile)
        layers = integer(props["Layers"], "Layers")
        beta = angles(props["Betas"], layers, "Betas")
        gamma = angles(props["Gammas"], layers, "Gammas")
        return circuit_result(
            qaoa(terms, n, beta, gamma),
            "PyquilQAOACircuit",
            "qaoa",
            {"hamiltonian.json": raw, "qaoa.layers": str(layers), "qaoa.error": ""},
        )
