"""Read a phase oracle as qasm2/qasm3, prepare a uniform superposition, and repeat oracle plus native pyQuil diffuser. Output qasm2 for any simulator. Input must be an oracle, not a complete Grover circuit."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilGroverOperator(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Read a phase oracle as qasm2/qasm3, prepare a uniform superposition, and repeat oracle plus native pyQuil diffuser. Output qasm2 for any simulator. Input must be an oracle, not a complete Grover circuit."
        tags = ["quantum", "pyquil", "rigetti", "grover", "circuit", "composition"]
        dependencies = [
            "pyquil>=4.18",
            "qiskit>=2.0.0,<2.5",
            "qiskit-qasm3-import>=0.6",
        ]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                (
                    "Num Iterations",
                    "1",
                    "Oracle/diffuser repetitions, 0..100. Zero emits just the uniform state. At most 8 qubits.",
                    None,
                )
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "grover")

    def _run(self, props, flowfile):
        from pyquil_components import grover
        from pyquil_processor import circuit_result, read_circuit

        oracle = read_circuit(flowfile)
        return circuit_result(
            grover(oracle, props["Num Iterations"]),
            "PyquilGroverOperator",
            "grover",
            {
                "circuit.marked_state": flowfile.getAttribute("circuit.marked_state")
                or "",
                "circuit.num_iterations": props["Num Iterations"],
                "grover.error": "",
            },
        )
