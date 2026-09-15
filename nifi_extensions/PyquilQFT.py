"""Build or append the forward/inverse quantum Fourier transform in native pyQuil. Qubit 0 is the most significant bit in the documented Fourier matrix; optional final swaps reverse the output register. Emits bound qasm2."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilQFT(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Build or append the forward/inverse quantum Fourier transform in native pyQuil. Qubit 0 is the most significant bit in the documented Fourier matrix; optional final swaps reverse the output register. Emits bound qasm2."
        tags = ["quantum", "pyquil", "rigetti", "qft", "circuit", "composition"]
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
                    "Input Mode",
                    "new",
                    "new builds just QFT; append applies it after an incoming qasm2/qasm3 circuit.",
                    ["new", "append"],
                ),
                (
                    "Num Qubits",
                    "2",
                    "Width for new mode, 1..16; append uses the incoming circuit width.",
                    None,
                ),
                (
                    "Inverse",
                    "false",
                    "Use the inverse of the chosen forward circuit.",
                    ["false", "true"],
                ),
                (
                    "Include Swaps",
                    "true",
                    "Include final bit-reversal swaps in forward QFT (initial swaps in its inverse).",
                    ["false", "true"],
                ),
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "qft")

    def _run(self, props, flowfile):
        from pyquil_components import qft
        from pyquil_processor import circuit_result, read_circuit

        if props["Input Mode"] not in ("new", "append"):
            raise ValueError("Input Mode must be new or append")
        if props["Inverse"] not in ("true", "false") or props["Include Swaps"] not in (
            "true",
            "false",
        ):
            raise ValueError("Inverse and Include Swaps must be true or false")
        upstream = read_circuit(flowfile) if props["Input Mode"] == "append" else None
        n = upstream.num_qubits if upstream is not None else props["Num Qubits"]
        program = qft(n, props["Inverse"] == "true", props["Include Swaps"] == "true")
        if upstream is not None:
            width = program.num_qubits
            program = upstream + program
            program.num_qubits = width
        return circuit_result(program, "PyquilQFT", "qft", {"qft.error": ""})
