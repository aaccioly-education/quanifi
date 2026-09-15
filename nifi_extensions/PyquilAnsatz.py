"""Build a reusable RY or RY/RZ trial-state recipe with CNOT entanglers. Attach mode preserves an upstream Hamiltonian for PyquilVQE; circuit mode emits a bound qasm2 circuit for simulation or PyquilExpectation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded


class PyquilAnsatz(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Build a reusable RY or RY/RZ trial-state recipe with CNOT entanglers. Attach mode preserves an upstream Hamiltonian for PyquilVQE; circuit mode emits a bound qasm2 circuit for simulation or PyquilExpectation."
        tags = ["quantum", "pyquil", "rigetti", "ansatz", "vqe", "circuit"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                (
                    "Output Mode",
                    "circuit",
                    "circuit emits bound qasm2; attach preserves upstream Hamiltonian content and writes ansatz.pyquil_json for VQE.",
                    ["circuit", "attach"],
                ),
                (
                    "Num Qubits",
                    "2",
                    "Circuit-mode width, 1..16. Attach mode uses the Hamiltonian width.",
                    None,
                ),
                (
                    "Reps",
                    "1",
                    "Entangling layers, 0..16; one final rotation layer is always included.",
                    None,
                ),
                (
                    "Rotations",
                    "ry_rz",
                    "Rotations per qubit in each layer, in the listed order.",
                    ["ry", "ry_rz"],
                ),
                (
                    "Entanglement",
                    "linear",
                    "CNOT pattern applied after each nonfinal rotation layer.",
                    ["linear", "full", "none"],
                ),
                (
                    "Parameters",
                    "zeros",
                    "Circuit mode: zeros or JSON/comma-separated angles. Order: layer, qubit, RY then RZ. Ignored in attach mode.",
                    None,
                ),
            ]
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "ansatz")

    def _run(self, props, flowfile):
        import json
        from pyquil_components import ansatz_recipe, ansatz, parameter_count, angles
        from pyquil_processor import circuit_result, read_hamiltonian, success

        mode = props["Output Mode"]
        if mode not in ("attach", "circuit"):
            raise ValueError("Output Mode must be attach or circuit")
        n = read_hamiltonian(flowfile)[1] if mode == "attach" else props["Num Qubits"]
        recipe = ansatz_recipe(
            n, props["Reps"], props["Rotations"], props["Entanglement"]
        )
        count = parameter_count(recipe)
        attrs = {
            "ansatz.format": "pyquil_recipe_json",
            "ansatz.pyquil_json": json.dumps(recipe),
            "ansatz.num_parameters": str(count),
            "ansatz.num_qubits": str(recipe["num_qubits"]),
            "ansatz.framework": "pyquil",
            "ansatz.type": props["Rotations"],
            "ansatz.reps": props["Reps"],
            "ansatz.entanglement": props["Entanglement"],
            "ansatz.param_names": json.dumps([f"theta_{i}" for i in range(count)]),
            "ansatz.error": "",
        }
        if mode == "attach":
            attrs.update(
                {
                    "circuit.format": "",
                    "circuit.qasm2": "",
                    "circuit.qasm3": "",
                    "circuit.svg": "",
                    "circuit.diagram": "",
                    "report.type": "",
                    "mime.type": "application/json",
                }
            )
            return success(bytes(flowfile.getContentsAsBytes()), attrs)
        values = (
            [0.0] * count
            if props["Parameters"] == "zeros"
            else angles(props["Parameters"], count)
        )
        return circuit_result(ansatz(recipe, values), "PyquilAnsatz", "ansatz", attrs)
