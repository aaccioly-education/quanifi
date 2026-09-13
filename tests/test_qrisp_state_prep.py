"""
Tests for the Qrisp Phase-1 parity processors:
  QrispHadamardTransform, QrispStatePreparation.

Cross-framework mirrors of the Qiskit/Cirq equivalents — same property names,
LSB-first basis convention, qasm2 wire format. Bit-ordering and amplitude claims
are verified by running the emitted qasm2 through QiskitAerSimulator (Qrisp's own
QrispSimulatorBackend re-orders/drops idle qubits when re-parsing sparse qasm2, so the
Qiskit simulator is the cross-framework validator — same choice QrispQFTCircuit's
pipeline test makes).
"""

import json

import pytest

from QrispHadamardTransform import QrispHadamardTransform
from QrispStatePreparation import QrispStatePreparation
from QiskitAerSimulator import QiskitAerSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _aer_top(prep_result, shots=512):
    sim = QiskitAerSimulator().transform(
        MockContext(**{"Shots": str(shots)}), result_to_flowfile(prep_result)
    )
    return sim


# ---------------------------------------------------------------------------
# QrispHadamardTransform
# ---------------------------------------------------------------------------

class TestQrispHadamardTransform:

    def _run(self, n=2):
        ctx = MockContext(**{"Qubit Count": str(n)})
        return QrispHadamardTransform().transform(ctx, MockFlowFile())

    def test_standalone_qasm2(self):
        r = self._run(3)
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.framework"] == "qrisp"
        assert "OPENQASM 2" in r.contents.decode()

    def test_metrics_emitted(self):
        r = self._run(2)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes
        assert int(r.attributes["circuit.gate_count"]) >= 2  # one H per qubit

    def test_property_descriptors(self):
        names = {d.name for d in QrispHadamardTransform().getPropertyDescriptors()}
        assert names == {"Qubit Count"}

    def test_uniform_distribution(self):
        """H⊗3 |0> → 8 equal-probability states (via Aer)."""
        r = self._run(3)
        sim = _aer_top(r, shots=2048)
        counts = json.loads(sim.contents)
        assert len(counts) == 8

    def test_compose_appends_h(self):
        """Compose mode appends H to an incoming qasm2 circuit."""
        base = self._run(2)
        ff = result_to_flowfile(base)
        r = QrispHadamardTransform().transform(MockContext(**{"Qubit Count": "2"}), ff)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        # H∘H = I → composed circuit returns to |00> with certainty
        sim = _aer_top(r, shots=512)
        assert sim.attributes["sim.top_result"] == "00"
        assert float(sim.attributes["sim.top_probability"]) > 0.99

    def test_compose_rejects_non_qasm2(self):
        ff = MockFlowFile(content=b"OPENQASM 3;", attributes={"circuit.format": "qasm3"})
        r = QrispHadamardTransform().transform(MockContext(**{"Qubit Count": "2"}), ff)
        assert r.relationship == "failure"
        assert "circuit.error" in r.attributes


# ---------------------------------------------------------------------------
# QrispStatePreparation
# ---------------------------------------------------------------------------

class TestQrispStatePreparation:

    def _run(self, state_type, n=2, amplitudes="", basis_state="0"):
        ctx = MockContext(**{
            "State Type": state_type,
            "Qubit Count": str(n),
            "Amplitudes": amplitudes,
            "Target Basis State": basis_state,
        })
        return QrispStatePreparation().transform(ctx, MockFlowFile())

    # --- Happy paths ---

    def test_uniform(self):
        r = self._run("uniform", n=2)
        assert r.relationship == "success"
        assert r.attributes["circuit.state_type"] == "uniform"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.framework"] == "qrisp"

    def test_ghz(self):
        r = self._run("ghz", n=3)
        assert r.relationship == "success"
        assert r.attributes["circuit.state_type"] == "ghz"

    def test_basis_attribute(self):
        r = self._run("basis", n=2, basis_state="3")
        assert r.attributes["state_prep.basis_state"] == "3"

    def test_custom_marks_amplitudes(self):
        r = self._run("custom", amplitudes="[0,0,0,1]")
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert "state_prep.amplitudes" in r.attributes

    def test_metrics_emitted(self):
        r = self._run("ghz", n=3)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in QrispStatePreparation().getPropertyDescriptors()}
        assert names == {"State Type", "Qubit Count", "Target Basis State", "Amplitudes"}

    # --- Error paths ---

    def test_custom_without_amplitudes_raises(self):
        with pytest.raises(ValueError, match="Amplitudes must be provided"):
            self._run("custom", amplitudes="")

    def test_custom_non_power_of_two_raises(self):
        with pytest.raises(ValueError, match="power of 2"):
            self._run("custom", amplitudes="[1,1,1]")

    def test_basis_out_of_range_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            self._run("basis", n=2, basis_state="9")

    def test_unknown_state_type_raises(self):
        with pytest.raises(ValueError, match="Unknown State Type"):
            self._run("bogus")

    # --- Bit-ordering / amplitude correctness via QiskitAerSimulator ---

    def test_uniform_distribution(self):
        r = self._run("uniform", n=3)
        counts = json.loads(_aer_top(r, shots=2048).contents)
        assert len(counts) == 8

    @pytest.mark.parametrize("k,n", [(1, 2), (2, 2), (3, 2), (5, 3)])
    def test_basis_readout_matches_format(self, k, n):
        r = self._run("basis", n=n, basis_state=str(k))
        sim = _aer_top(r, shots=256)
        assert sim.attributes["sim.top_result"] == format(k, f"0{n}b")

    def test_ghz_two_states(self):
        r = self._run("ghz", n=3)
        counts = json.loads(_aer_top(r, shots=2048).contents)
        assert set(counts.keys()) == {"000", "111"}

    def test_custom_amplitude_index_maps_to_basis_string(self):
        amps = [0] * 8
        amps[5] = 1
        r = self._run("custom", amplitudes=json.dumps(amps))
        sim = _aer_top(r, shots=256)
        assert sim.attributes["sim.top_result"] == "101"
