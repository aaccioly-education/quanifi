"""
Unit tests for MoleculeHamiltonian — the chemistry problem-definition stage
that replicates the front-end of Classiq's molecule_eigensolver application:

    MoleculeHamiltonian -> <Framework>Ansatz -> <Framework>VQE -> QuanifiReport

The processor is framework-agnostic: it emits the same sparse_pauli_op_json
wire format as the per-framework *Hamiltonian processors, so the chain test
drives it straight into the existing Qiskit VQE pipeline.
No running NiFi / JVM — conftest.py stubs nifiapi.*.
"""

import json

import numpy as np
import pytest

from MoleculeHamiltonian import MoleculeHamiltonian, _parse_geometry, _formula

from conftest import MockContext, MockFlowFile, result_to_flowfile


H2_FCI_STO3G = -1.1373060358  # exact (FCI) H2 energy at 0.735 A, sto-3g


class TestGeometryParsing:

    def test_semicolon_and_newline_separators(self):
        atoms = _parse_geometry("H 0 0 0\nH 0, 0, 0.735")
        assert atoms == [("H", (0.0, 0.0, 0.0)), ("H", (0.0, 0.0, 0.735))]

    def test_formula_groups_repeated_symbols(self):
        assert _formula([("H", (0, 0, 0)), ("H", (0, 0, 1))]) == "H2"
        assert _formula([("H", (0, 0, 0)), ("Li", (0, 0, 1))]) == "HLi"

    def test_rejects_wrong_arity(self):
        with pytest.raises(ValueError):
            _parse_geometry("H 0 0")


class TestMoleculeHamiltonian:

    def test_h2_default_contract_and_reference_energies(self):
        proc = MoleculeHamiltonian()
        res = proc.transform(MockContext(), MockFlowFile())
        assert res.relationship == "success"
        assert res.attributes["hamiltonian.format"] == "sparse_pauli_op_json"
        assert res.attributes["hamiltonian.num_qubits"] == "4"
        assert res.attributes["hamiltonian.molecule"] == "H2"
        assert res.attributes["hamiltonian.bond_distance"] == "0.735000"
        assert float(res.attributes["hamiltonian.hf_energy"]) < 0
        assert abs(float(res.attributes["hamiltonian.fci_energy"]) - H2_FCI_STO3G) < 1e-6

        payload = json.loads(res.contents.decode("utf-8"))
        assert payload["num_qubits"] == 4
        assert len(payload["terms"]) == int(res.attributes["hamiltonian.num_terms"])

    def test_qubit_operator_ground_state_matches_fci(self):
        # The smallest eigenvalue of the emitted qubit operator must equal the
        # FCI energy — this pins the whole pyscf -> fermion -> Jordan-Wigner ->
        # wire-format path, including the nuclear-repulsion constant term.
        from qiskit.quantum_info import SparsePauliOp
        res = MoleculeHamiltonian().transform(MockContext(), MockFlowFile())
        payload = json.loads(res.contents.decode("utf-8"))
        op = SparsePauliOp.from_list(
            [(t[0], complex(t[1], t[2])) for t in payload["terms"]],
            num_qubits=payload["num_qubits"],
        )
        ground = float(np.min(np.linalg.eigvalsh(op.to_matrix())))
        assert abs(ground - float(res.attributes["hamiltonian.fci_energy"])) < 1e-8

    def test_expression_language_drives_bond_distance(self):
        proc = MoleculeHamiltonian()
        ctx = MockContext(**{"Molecule Geometry": "H 0 0 0; H 0 0 ${bond.distance}"})
        ff = MockFlowFile(attributes={"bond.distance": "0.9"})
        res = proc.transform(ctx, ff)
        assert res.relationship == "success"
        assert res.attributes["hamiltonian.bond_distance"] == "0.900000"
        # Stretched bond -> higher ground-state energy than equilibrium.
        assert float(res.attributes["hamiltonian.fci_energy"]) > H2_FCI_STO3G

    def test_reference_energies_can_be_disabled(self):
        ctx = MockContext(**{"Compute Reference Energies": "false"})
        res = MoleculeHamiltonian().transform(ctx, MockFlowFile())
        assert res.relationship == "success"
        assert "hamiltonian.fci_energy" not in res.attributes

    def test_bad_geometry_routes_to_failure(self):
        ctx = MockContext(**{"Molecule Geometry": "H 0 0"})
        res = MoleculeHamiltonian().transform(ctx, MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes

    def test_unknown_element_routes_to_failure(self):
        ctx = MockContext(**{"Molecule Geometry": "Zz 0 0 0; H 0 0 1"})
        res = MoleculeHamiltonian().transform(ctx, MockFlowFile())
        assert res.relationship == "failure"
        assert "hamiltonian.error" in res.attributes


class TestMoleculeVQEChain:

    def test_h2_chain_reaches_near_fci(self):
        from QiskitAnsatz import QiskitAnsatz
        from QiskitVQE import QiskitVQE

        ham = MoleculeHamiltonian().transform(MockContext(), MockFlowFile())
        fci = float(ham.attributes["hamiltonian.fci_energy"])

        ans = QiskitAnsatz().transform(
            MockContext(**{"Ansatz Type": "efficient_su2", "Reps": "2",
                           "Entanglement": "linear"}),
            result_to_flowfile(ham))
        assert ans.relationship == "success"
        assert ans.attributes["ansatz.num_qubits"] == "4"

        vqe = QiskitVQE().transform(
            MockContext(**{"Optimizer": "COBYLA", "Max Iterations": "250"}),
            result_to_flowfile(ans))
        assert vqe.relationship == "success"
        energy = float(vqe.attributes["vqe.optimal_value"])
        # Variational bound: never below the exact ground state (numeric slack),
        # and the optimizer must get within chemical-demo accuracy of it.
        assert energy >= fci - 1e-6
        assert energy <= fci + 0.05
