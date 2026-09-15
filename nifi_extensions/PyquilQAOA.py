"""Minimize a diagonal I/Z cost using the same native circuit builder as PyquilQAOACircuit, exact pyQuil local expectations and SciPy. Initial Parameters are all betas followed by all gammas. Final sampled counts and qaoa.* results are separate from the optimized circuit.qasm2."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded
from pyquil_processor import SOLVER_SPECS


class PyquilQAOA(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Minimize a diagonal I/Z cost using the same native circuit builder as PyquilQAOACircuit, exact pyQuil local expectations and SciPy. Initial Parameters are all betas followed by all gammas. Final sampled counts and qaoa.* results are separate from the optimized circuit.qasm2."
        tags = ["quantum", "pyquil", "rigetti", "qaoa", "optimization", "solver"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26", "scipy>=1.14"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(
            [
                (
                    "Layers",
                    "1",
                    "Number of cost/mixer layers, 1..16; parameter order is betas then gammas.",
                    None,
                )
            ]
            + SOLVER_SPECS
        )

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "qaoa")

    def _run(self, props, flowfile):
        from pyquil_components import qaoa, integer
        from pyquil_processor import read_hamiltonian, solve
        from pauli_dsl import is_diagonal

        terms, n, raw = read_hamiltonian(flowfile)
        if not is_diagonal(terms):
            raise ValueError("QAOA cost must be diagonal (I/Z terms only)")
        layers = integer(props["Layers"], "Layers")
        return solve(
            props,
            flowfile,
            "qaoa",
            lambda point: qaoa(terms, n, point[:layers], point[layers:]),
            2 * layers,
            terms,
            n,
            {"hamiltonian.json": raw, "qaoa.layers": str(layers)},
        )
