"""Build an unmeasured bitstring phase oracle in native pyQuil. Outputs qasm2, with qubit 0 at the left of Marked State. Connect to PyquilGroverOperator."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilPhaseOracle(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Build an unmeasured bitstring phase oracle in native pyQuil. Outputs qasm2, with qubit 0 at the left of Marked State. Connect to PyquilGroverOperator."
        tags = ["quantum", "pyquil", "rigetti", "grover", "oracle", "circuit"]
        dependencies = ["pyquil>=4.18"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                (
                    "Marked State",
                    "11",
                    "Binary target, qubit 0 leftmost; 1..8 qubits. No state preparation or measurement.",
                    None,
                )
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "grover")

    def _run(self, props, flowfile):
        from pyquil_components import phase_oracle
        from pyquil_processor import circuit_result

        target = props["Marked State"]
        return circuit_result(
            phase_oracle(target),
            "PyquilPhaseOracle",
            "phase_oracle",
            {"circuit.marked_state": target, "grover.error": ""},
        )
