import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators, ExpressionLanguageScope
from nifiapi.__jvm__ import JvmHolder


class QrispQCCSDAnsatz(FlowFileTransform):
    """
    Declares a Qubit Coupled Cluster Single Double (QCCSD) chemistry ansatz for QrispVQE.

    Unlike generic heuristic ansätze (e.g. RealAmplitudes, TwoLocal), QCCSD uses
    parameterized single and double excitation unitaries that preserve particle number
    and spin symmetries:
        U(theta) |Psi_HF>
    where |Psi_HF> is the Hartree-Fock ground state |1_0 ... 1_N 0_{N+1} ... 0_M>.

    Two modes:
      Chain mode - an upstream Hamiltonian (e.g. from MoleculeHamiltonian or QrispHamiltonian)
        is present on the FlowFile: passes Hamiltonian content through untouched and derives
        spin orbitals / qubits from hamiltonian.num_qubits if set to 0.
      Standalone mode - emits the ansatz spec as JSON content.
    """

    class Java:
        implements = ['org.apache.nifi.python.processor.FlowFileTransform']

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Declares a QCCSD chemistry ansatz for QrispVQE. Carries the chemistry spec in "
            "ansatz.* attributes and passes through incoming Hamiltonian content in chain mode."
        )
        tags = ["quantum", "qrisp", "vqe", "chemistry", "qccsd", "ansatz"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get('jvm')
        super().__init__()

        self.num_spin_orbitals = PropertyDescriptor(
            name="Num Spin Orbitals",
            description="Total number of active spin orbitals M (qubit count). 0 = derive from upstream Hamiltonian.",
            required=True,
            default_value="4",
            validators=[StandardValidators.NON_NEGATIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.num_electrons = PropertyDescriptor(
            name="Num Electrons",
            description="Number of active electrons N in the molecule.",
            required=True,
            default_value="2",
            validators=[StandardValidators.POSITIVE_INTEGER_VALIDATOR],
            expression_language_scope=ExpressionLanguageScope.FLOWFILE_ATTRIBUTES,
        )
        self.descriptors = [self.num_spin_orbitals, self.num_electrons]

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        get = lambda prop: (
            context.getProperty(prop)
            .evaluateAttributeExpressions(flowFile)
            .getValue()
        )

        try:
            m = int(get(self.num_spin_orbitals))
            nelec = int(get(self.num_electrons))
        except (TypeError, ValueError) as exc:
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"ansatz.error": f"Invalid numeric property: {exc}"},
            )

        h_fmt = flowFile.getAttribute("hamiltonian.format")
        h_qubits = flowFile.getAttribute("hamiltonian.num_qubits")
        raw = bytes(flowFile.getContentsAsBytes() or b"")

        is_chain = bool(h_fmt == "sparse_pauli_op_json" and raw)

        if m == 0:
            if is_chain and h_qubits:
                m = int(h_qubits)
            else:
                m = 4

        if nelec > m:
            return FlowFileTransformResult(
                relationship="failure",
                attributes={"ansatz.error": f"Num Electrons ({nelec}) cannot exceed Num Spin Orbitals ({m})"},
            )

        # Calculate number of single and double excitation parameters
        import math
        spin_down_occ = len([i for i in range(nelec) if i % 2 == 0])
        spin_down_virt = len([i for i in range(nelec, m) if i % 2 == 0])
        spin_up_occ = len([i for i in range(nelec) if i % 2 == 1])
        spin_up_virt = len([i for i in range(nelec, m) if i % 2 == 1])

        num_singles = spin_down_occ * spin_down_virt + spin_up_occ * spin_up_virt
        num_doubles = (
            spin_down_occ * spin_up_occ * spin_down_virt * spin_up_virt
            + math.comb(spin_down_occ, 2) * math.comb(spin_down_virt, 2)
            + math.comb(spin_up_occ, 2) * math.comb(spin_up_virt, 2)
        )
        total_params = num_singles + num_doubles

        attrs = {
            "ansatz.format": "qrisp_spec",
            "ansatz.type": "qccsd",
            "ansatz.num_qubits": str(m),
            "ansatz.num_spin_orbitals": str(m),
            "ansatz.num_electrons": str(nelec),
            "ansatz.num_parameters": str(total_params),
            "ansatz.framework": "qrisp",
            "ansatz.description": f"QCCSD chemistry ansatz: {m} spin orbitals, {nelec} electrons, {total_params} parameters",
        }

        if is_chain:
            # Chain mode: pass through the Hamiltonian content untouched
            content = raw
            attrs["hamiltonian.format"] = h_fmt
            if h_qubits:
                attrs["hamiltonian.num_qubits"] = h_qubits
        else:
            # Standalone mode: emit JSON spec
            content = json.dumps({
                "type": "qccsd",
                "num_spin_orbitals": m,
                "num_electrons": nelec,
                "num_parameters": total_params,
            }, indent=2).encode("utf-8")

        return FlowFileTransformResult(
            relationship="success",
            contents=content,
            attributes=attrs,
        )
