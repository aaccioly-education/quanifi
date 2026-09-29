# Quanifi — quantum-computing components for Apache NiFi
# Copyright (C) 2026 Neilson Ramalho
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Affero General Public License, version 3, as published by
# the Free Software Foundation. This program is distributed WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the license along with this program; if not,
# see <https://www.gnu.org/licenses/>. Commercial licensing is also available:
# see COMMERCIAL.md at the repository root.

"""Minimize a Hermitian Hamiltonian using a recipe from PyquilAnsatz (attach mode), native pyQuil exact local expectations and SciPy. Reads neutral Hamiltonian content; emits final counts, vqe.* results and the optimized qasm2 in circuit.qasm2. No Forest server or hardware calls."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nifiapi.flowfiletransform import FlowFileTransform
from nifiapi.__jvm__ import JvmHolder
from pyquil_processor import descriptors, guarded
from pyquil_processor import SOLVER_SPECS


class PyquilVQE(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = "Minimize a Hermitian Hamiltonian using a recipe from PyquilAnsatz (attach mode), native pyQuil exact local expectations and SciPy. Reads neutral Hamiltonian content; emits final counts, vqe.* results and the optimized qasm2 in circuit.qasm2. No Forest server or hardware calls."
        tags = ["quantum", "pyquil", "rigetti", "vqe", "variational", "solver"]
        dependencies = ["pyquil>=4.18", "numpy>=1.26", "scipy>=1.14"]

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.descriptors = descriptors(SOLVER_SPECS)

    def getPropertyDescriptors(self):
        return self.descriptors

    def transform(self, context, flowFile):
        return guarded(self, context, flowFile, self._run, "vqe")

    def _run(self, props, flowfile):
        from pyquil_components import read_recipe, ansatz, parameter_count
        from pyquil_processor import read_hamiltonian, solve

        terms, n, _ = read_hamiltonian(flowfile)
        raw = flowfile.getAttribute("ansatz.pyquil_json")
        if flowfile.getAttribute("ansatz.format") != "pyquil_recipe_json" or not raw:
            raise ValueError(
                "Expected PyquilAnsatz in attach mode (ansatz.pyquil_json)"
            )
        recipe = read_recipe(raw)
        if recipe["num_qubits"] != n:
            raise ValueError("Ansatz and Hamiltonian widths must match")
        return solve(
            props,
            flowfile,
            "vqe",
            lambda point: ansatz(recipe, point),
            parameter_count(recipe),
            terms,
            n,
            {"vqe.ansatz_type": recipe["rotations"]},
        )
