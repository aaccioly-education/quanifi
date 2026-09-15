import math
import os
import sys

# NiFi runs each processor in its own module context where the extensions
# directory is not on sys.path, so the sibling-module import (quil_qasm)
# fails unless we add this file's directory explicitly. (Tests pass without
# it only because conftest puts the directory on sys.path.)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.properties import (
    PropertyDescriptor,
    StandardValidators,
    ExpressionLanguageScope,
)
from nifiapi.__jvm__ import JvmHolder


class PyquilGroverCircuit(FlowFileTransform):

    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Builds a Grover search circuit using pyquil (Rigetti) for a "
            "given target bitstring and outputs the unmeasured circuit as "
            "OpenQASM 2.0, the interchange format every engine in this repo "
            "accepts. pyquil ships no Grover/amplitude-amplification helper, "
            "so the oracle and diffuser -- including the multi-controlled-Z, "
            "which pyquil has no native gate for beyond 2 controls -- are "
            "built here directly from pyquil's own H/X/CNOT/RZ primitives "
            "(see pyquil_components.mcz_ops), not translated from "
            "another framework's circuit. Connect to PyquilSimulator or any "
            "other engine in this repo to run the simulation."
        )
        tags = ["quantum", "pyquil", "rigetti", "grover", "circuit"]
        dependencies = ["pyquil>=4.18"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()

        self.marked_state = PropertyDescriptor(
            name="Marked State",
            description=(
                "Target bitstring Grover will search for, e.g. '110'. "
                "Length sets the number of qubits. Bit order is left-to-right "
                "(qubit 0 = leftmost character)."
            ),
            required=True,
            default_value="11",
            validators=[StandardValidators.NON_EMPTY_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.num_iterations = PropertyDescriptor(
            name="Num Iterations",
            description=(
                "Number of Grover operator applications (0..100; at most 8 qubits). "
                "Optimal is roughly floor(pi/4 * sqrt(2^n)) for one marked state."
            ),
            required=True,
            default_value="1",
            validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.output_format = PropertyDescriptor(
            name="Output Format",
            description=(
                "Format used to serialize the circuit into the FlowFile "
                "content. 'qasm2' (the only supported value) is OpenQASM 2.0 "
                "text emitted via quil_qasm.to_qasm2, the interchange format "
                "every other engine in this repo accepts."
            ),
            required=True,
            default_value="qasm2",
            allowable_values=["qasm2"],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [
            self.marked_state,
            self.num_iterations,
            self.output_format,
        ]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        from pyquil_components import phase_oracle, grover
        from pyquil_processor import guarded, circuit_result

        def build(props, ff):
            if props["Output Format"] != "qasm2":
                raise ValueError("Output Format must be qasm2")
            target = props["Marked State"]
            program = grover(phase_oracle(target), props["Num Iterations"])
            result = circuit_result(
                program,
                "PyquilGrover",
                "grover",
                {
                    "circuit.marked_state": target,
                    "circuit.num_iterations": props["Num Iterations"],
                    "circuit.bit_order": "canonical",  # legacy alias of q0_left
                    "grover.error": "",
                },
            )
            result.attributes["circuit.t_count"] = str(
                sum(
                    1
                    for instr in program.instructions
                    if instr.name == "RZ"
                    and abs(abs(float(instr.params[0].real)) - math.pi / 4) < 1e-9
                )
            )
            return result

        return guarded(self, context, flowFile, build, "grover")
