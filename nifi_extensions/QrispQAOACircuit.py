"""Build a bound native Qrisp QAOA circuit from a diagonal I/Z cost
Hamiltonian and explicit beta/gamma angles. Emits portable qasm2 plus
hamiltonian.json for any counts engine and QuantumQAOAEvaluator. One of the
five interchangeable NxM builder rows; see docs/guides/QAOA_COMPONENTS.md.
"""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder

import qaoa_contract as qc
import qrisp_qaoa


class QrispQAOACircuit(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = (
            "Builds the bound Qrisp QAOA circuit (QAOAProblem's cost operator + "
            "mixer, without training) for a diagonal I/Z cost Hamiltonian and "
            "explicit Betas/Gammas (beta then gamma) and emits portable "
            "OpenQASM 2.0 plus hamiltonian.json, so any counts engine can run it "
            "and QuantumQAOAEvaluator can score it. Convention: H on all qubits, "
            "then per layer exp(-i*gamma*H) and RX(2*beta). Qrisp's per-qubit "
            "registers are normalised to one qreg; the XY mixer is rebased to "
            "{h,x,rx,ry,rz,cx}."
        )
        tags = ["quantum", "qrisp", "qaoa", "circuit", "qasm2", "builder"]
        dependencies = ["qrisp==0.9.5", "qiskit>=2.0.0,<2.5"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = qc.builder_descriptors(with_mixer=True)

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
        mixer_name = props["Mixer Type"]

        # Suppress Qrisp's tqdm progress bar: NiFi's py4j bridge uses stdout,
        # so printing there corrupts the channel ("null response" crash).
        with contextlib.redirect_stdout(io.StringIO()):
            source, diagram, _rebased = qrisp_qaoa.export(
                terms, n, betas, gammas, mixer_name
            )

        profile = qc.qasm2_profile(source)
        if profile["num_qubits"] != n:
            raise ValueError(
                "exported circuit has {} qubits, expected {}".format(
                    profile["num_qubits"], n
                )
            )

        attrs = {
            **qc.circuit_attributes(
                source, profile, "qrisp", "QrispQAOACircuit", diagram
            ),
            **qc.builder_attributes(raw, n, layers, betas, gammas, mixer=mixer_name),
        }
        return qc.success(source.encode(), attrs)
