"""
Unit and integration tests for CirqQFTCircuit and CirqPhaseEstimation.
"""

import json

import pytest

from CirqQFTCircuit import CirqQFTCircuit
from CirqPhaseEstimation import CirqPhaseEstimation
from CirqSimulator import CirqSimulator
from CirqPhaseOracle import CirqPhaseOracle

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# CirqQFTCircuit
# ---------------------------------------------------------------------------

class TestCirqQFTCircuit:

    def _run(self, n=3, inverse="false", do_swaps="true", barriers="false", fmt="cirq_json"):
        ctx = MockContext(**{
            "Qubit Count": str(n),
            "Inverse": inverse,
            "Do Swaps": do_swaps,
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return CirqQFTCircuit().transform(ctx, MockFlowFile())

    # --- Structural tests ---

    def test_standalone_cirq_json(self):
        r = self._run(3)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.format"] == "cirq_json"
        assert r.attributes["circuit.qft_inverse"] == "false"
        # Must be valid round-trippable Cirq JSON
        import cirq
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 3

    def test_standalone_qasm2(self):
        r = self._run(3, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_inverse_flag(self):
        r = self._run(3, inverse="true")
        assert r.attributes["circuit.qft_inverse"] == "true"

    def test_no_swaps_flag(self):
        r = self._run(3, do_swaps="false")
        assert r.attributes["circuit.qft_do_swaps"] == "false"

    def test_metrics_emitted(self):
        r = self._run(3)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) > 1

    def test_two_qubit(self):
        r = self._run(2)
        assert r.attributes["circuit.num_qubits"] == "2"

    def test_property_descriptors(self):
        names = {d.name for d in CirqQFTCircuit().getPropertyDescriptors()}
        assert names == {"Qubit Count", "Inverse", "Do Swaps", "Insert Barriers", "Output Format"}

    # --- Compose mode ---

    def test_compose_onto_existing_circuit(self):
        """QFT is appended to an existing circuit in compose mode."""
        oracle_r = CirqPhaseOracle().transform(
            MockContext(**{"Marked State": "11", "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(),
        )
        r = CirqQFTCircuit().transform(
            MockContext(**{"Qubit Count": "2", "Inverse": "false", "Do Swaps": "true",
                           "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(oracle_r),
        )
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        # Composed circuit must be deeper than the oracle alone
        oracle_depth = int(oracle_r.attributes["circuit.depth"])
        composed_depth = int(r.attributes["circuit.depth"])
        assert composed_depth > oracle_depth

    # --- Simulation tests ---

    def test_qft_pipeline(self):
        """QFT → CirqSimulator produces 2^n states for n-qubit input."""
        qft_r = self._run(3)
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(qft_r),
        )
        assert sim_r.relationship == "success"
        counts = json.loads(sim_r.contents)
        # QFT on |0⟩^n produces all basis states with equal amplitude
        assert len(counts) == 2**3

    def test_inverse_qft_undoes_forward(self):
        """Forward QFT → inverse QFT brings state back to computational basis."""
        import cirq
        # Start with a known state |101⟩, apply QFT then QFT†, expect |101⟩ back
        q = cirq.LineQubit.range(3)
        # IdentityGate on q[1] ensures all 3 qubits appear in all_qubits() when
        # compose mode calls sorted(circuit.all_qubits()) — without it only 2 qubits
        # are found and the QFT is applied to a 2-qubit circuit.
        init = cirq.Circuit([cirq.X(q[0]), cirq.I(q[1]), cirq.X(q[2])])  # prepare |101⟩
        init_json = cirq.to_json(init)

        fwd_r = CirqQFTCircuit().transform(
            MockContext(**{"Qubit Count": "3", "Inverse": "false", "Do Swaps": "true",
                           "Insert Barriers": "false", "Output Format": "cirq_json"}),
            MockFlowFile(content=init_json.encode(), attributes={"circuit.format": "cirq_json"}),
        )
        inv_r = CirqQFTCircuit().transform(
            MockContext(**{"Qubit Count": "3", "Inverse": "true", "Do Swaps": "true",
                           "Insert Barriers": "false", "Output Format": "cirq_json"}),
            result_to_flowfile(fwd_r),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(inv_r),
        )
        counts = json.loads(sim_r.contents)
        # After QFT then QFT†, original state |101⟩ should dominate
        assert max(counts, key=counts.get) == "101"


# ---------------------------------------------------------------------------
# CirqPhaseEstimation
# ---------------------------------------------------------------------------

class TestCirqPhaseEstimation:

    def _run(self, m=3, unitary="T", barriers="false", fmt="cirq_json", ff=None):
        ctx = MockContext(**{
            "Phase Register Size": str(m),
            "Builtin Unitary": unitary,
            "Insert Barriers": barriers,
            "Output Format": fmt,
        })
        return CirqPhaseEstimation().transform(ctx, ff or MockFlowFile())

    # --- Structural tests ---

    def test_standalone_t_gate_structure(self):
        r = self._run(3, "T")
        assert r.relationship == "success"
        assert r.attributes["circuit.qpe_builtin"] == "T"
        assert r.attributes["circuit.qpe_phase_register_size"] == "3"
        assert r.attributes["circuit.num_qubits"] == "4"  # 3 phase + 1 target

    def test_standalone_s_gate_structure(self):
        r = self._run(3, "S")
        assert r.attributes["circuit.qpe_builtin"] == "S"
        assert r.attributes["circuit.num_qubits"] == "4"

    def test_standalone_z_gate_structure(self):
        r = self._run(3, "Z")
        assert r.attributes["circuit.qpe_builtin"] == "Z"

    def test_qasm2_output(self):
        r = self._run(3, "T", fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_valid_cirq_json(self):
        import cirq
        r = self._run(3, "T")
        circuit = cirq.read_json(json_text=r.contents.decode())
        assert len(list(circuit.all_qubits())) == 4

    def test_metrics_emitted(self):
        r = self._run(3, "T")
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqPhaseEstimation().getPropertyDescriptors()}
        assert names == {"Phase Register Size", "Builtin Unitary", "Insert Barriers", "Output Format"}

    # --- Eigenphase correctness (simulation) ---

    def _simulate_qpe(self, unitary, m=3, shots=1024):
        r = self._run(m, unitary)
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": str(shots)}),
            result_to_flowfile(r),
        )
        counts = json.loads(sim_r.contents)
        return max(counts, key=counts.get)

    def test_t_gate_eigenphase(self):
        """T gate: eigenphase = 1/8 → phase register readout '001', target '1' → top = '0011'."""
        assert self._simulate_qpe("T") == "0011"

    def test_s_gate_eigenphase(self):
        """S gate: eigenphase = 1/4 → phase register readout '010', target '1' → top = '0101'."""
        assert self._simulate_qpe("S") == "0101"

    def test_z_gate_eigenphase(self):
        """Z gate: eigenphase = 1/2 → phase register readout '100', target '1' → top = '1001'."""
        assert self._simulate_qpe("Z") == "1001"

    def test_larger_phase_register(self):
        """4-qubit phase register gives finer precision; T gate still lands on the right state."""
        r = self._run(4, "T")
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(r),
        )
        counts = json.loads(sim_r.contents)
        top = max(counts, key=counts.get)
        # For T gate with 4-qubit phase register: 1/8 * 16 = 2 = 0010 in 4 bits → '00101'
        assert top == "00101"

    # --- Compose mode ---

    def test_compose_mode_t_circuit(self):
        """A 1-qubit T-gate circuit fed as compose input gives the same result as standalone T."""
        import cirq
        t_unitary = cirq.Circuit(cirq.T(cirq.LineQubit(0)))
        t_json = cirq.to_json(t_unitary)
        ff = MockFlowFile(
            content=t_json.encode(),
            attributes={"circuit.format": "cirq_json"},
        )
        r = self._run(3, "T", ff=ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.qpe_builtin"] == "compose"

        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}),
            result_to_flowfile(r),
        )
        top = max(json.loads(sim_r.contents), key=json.loads(sim_r.contents).get)
        assert top == "0011"

    def test_compose_mode_qpe_builtin_attr(self):
        """Compose mode sets circuit.qpe_builtin = 'compose'."""
        import cirq
        s_circuit = cirq.Circuit(cirq.S(cirq.LineQubit(0)))
        ff = MockFlowFile(
            content=cirq.to_json(s_circuit).encode(),
            attributes={"circuit.format": "cirq_json"},
        )
        r = self._run(3, "T", ff=ff)
        assert r.attributes["circuit.qpe_builtin"] == "compose"

    # --- Decode hint (mirrors QiskitPhaseEstimation) ---

    def test_emits_phase_decode_hint(self):
        """Builder emits the declarative decode hint for QuanifiReport.

        Cirq is MSB-first: the phase register is LineQubit 0..m-1 (sorted first
        by CirqSimulator) and the without_reverse inverse QFT puts qubit 0 at the
        readout MSB, so the phase bits occupy string positions 0..m-1 already in
        MSB→LSB order. For m=3 → positions "0,1,2".
        """
        r = self._run(3, "T")
        assert r.attributes["result.decode"] == "phase"
        assert r.attributes["result.bit_positions"] == "0,1,2"
        assert "phase" in r.attributes["result.label"].lower()

    def test_decode_hint_scales_with_register_size(self):
        """bit_positions covers exactly the m phase qubits (0..m-1)."""
        r = self._run(4, "T")
        assert r.attributes["result.bit_positions"] == "0,1,2,3"

    def test_decodes_to_correct_phase_end_to_end(self):
        """builder → CirqSimulator → decoded phase matches the gate eigenphase."""
        from fractions import Fraction
        for gate, expected in [("T", "1/8"), ("S", "1/4"), ("Z", "1/2")]:
            cr = self._run(3, gate)
            sim_r = CirqSimulator().transform(
                MockContext(**{"Shots": "1024"}), result_to_flowfile(cr),
            )
            counts = json.loads(sim_r.contents)
            top = max(counts, key=counts.get)
            positions = [int(p) for p in cr.attributes["result.bit_positions"].split(",")]
            bits = "".join(top[p] for p in positions)
            frac = Fraction(int(bits, 2), 2 ** len(positions))
            assert f"{frac.numerator}/{frac.denominator}" == expected, gate
