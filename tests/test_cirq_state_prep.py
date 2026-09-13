"""
Tests for the Cirq Phase-1 parity processors:
  CirqStatePreparation, CirqStatevectorSimulator.

Cross-framework mirrors of QiskitStatePreparation / QiskitStatevectorSimulator —
same property names, same MSB-first measured-string convention (qubit 0 leftmost),
so they are swappable on the canvas. Bit-ordering claims are verified empirically
by running the prepared circuit through CirqSimulator / CirqStatevectorSimulator.
"""

import json

import pytest

from CirqStatePreparation import CirqStatePreparation
from CirqStatevectorSimulator import CirqStatevectorSimulator
from CirqSimulator import CirqSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# CirqStatePreparation
# ---------------------------------------------------------------------------

class TestCirqStatePreparation:

    def _run(self, state_type, n=2, amplitudes="", basis_state="0", fmt="cirq_json"):
        ctx = MockContext(**{
            "State Type": state_type,
            "Qubit Count": str(n),
            "Amplitudes": amplitudes,
            "Target Basis State": basis_state,
            "Output Format": fmt,
        })
        return CirqStatePreparation().transform(ctx, MockFlowFile())

    # --- Happy paths ---

    def test_uniform(self):
        r = self._run("uniform", n=2)
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert r.attributes["circuit.state_type"] == "uniform"
        assert r.attributes["circuit.format"] == "cirq_json"

    def test_ghz(self):
        r = self._run("ghz", n=3)
        assert r.relationship == "success"
        assert r.attributes["circuit.state_type"] == "ghz"
        assert int(r.attributes["circuit.nonlocal_gates"]) >= 2  # CNOT chain

    def test_basis_attribute(self):
        r = self._run("basis", n=2, basis_state="3")
        assert r.attributes["state_prep.basis_state"] == "3"

    def test_custom_marks_amplitudes(self):
        r = self._run("custom", amplitudes="[0,0,0,1]")
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "2"
        assert "state_prep.amplitudes" in r.attributes

    def test_custom_unnormalised_accepted(self):
        r = self._run("custom", amplitudes="[1,1,1,1]")
        assert r.relationship == "success"

    def test_qasm2_output(self):
        r = self._run("uniform", n=2, fmt="qasm2")
        assert r.attributes["circuit.format"] == "qasm2"
        assert "OPENQASM 2" in r.contents.decode()

    def test_metrics_emitted(self):
        r = self._run("ghz", n=3)
        for key in ("circuit.depth", "circuit.gate_count", "circuit.nonlocal_gates", "circuit.t_count"):
            assert key in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqStatePreparation().getPropertyDescriptors()}
        assert names == {"State Type", "Qubit Count", "Target Basis State", "Amplitudes", "Output Format"}

    # --- Error paths ---

    def test_custom_without_amplitudes_raises(self):
        with pytest.raises(ValueError, match="Amplitudes must be provided"):
            self._run("custom", amplitudes="")

    def test_custom_non_power_of_two_raises(self):
        with pytest.raises(ValueError, match="power of 2"):
            self._run("custom", amplitudes="[1,1,1]")

    def test_custom_zero_norm_raises(self):
        with pytest.raises(ValueError, match="zero norm"):
            self._run("custom", amplitudes="[0,0,0,0]")

    def test_basis_out_of_range_raises(self):
        with pytest.raises(ValueError, match="out of range"):
            self._run("basis", n=2, basis_state="9")

    def test_unknown_state_type_raises(self):
        with pytest.raises(ValueError, match="Unknown State Type"):
            self._run("bogus")

    # --- Bit-ordering correctness via CirqSimulator (MSB-first) ---

    @pytest.mark.parametrize("k,n", [(1, 2), (2, 2), (3, 2), (5, 3)])
    def test_basis_readout_matches_format(self, k, n):
        prep = self._run("basis", n=n, basis_state=str(k))
        sim = CirqSimulator().transform(
            MockContext(**{"Shots": "256"}), result_to_flowfile(prep)
        )
        assert sim.attributes["sim.top_result"] == format(k, f"0{n}b")

    def test_custom_amplitude_index_maps_to_basis_string(self):
        # amps[5] = 1 on 3 qubits → should read |101>
        amps = [0] * 8
        amps[5] = 1
        prep = self._run("custom", amplitudes=json.dumps(amps))
        sim = CirqSimulator().transform(
            MockContext(**{"Shots": "256"}), result_to_flowfile(prep)
        )
        assert sim.attributes["sim.top_result"] == "101"


# ---------------------------------------------------------------------------
# CirqStatevectorSimulator
# ---------------------------------------------------------------------------

class TestCirqStatevectorSimulator:

    def _sv(self, prep_result, reports_dir, **overrides):
        props = {
            "Reports Directory": str(reports_dir),
            "Flow Name": "test",
            "Probability Threshold": "0.0",
            "Max States": "32",
        }
        props.update(overrides)
        return CirqStatevectorSimulator().transform(
            MockContext(**props), result_to_flowfile(prep_result)
        )

    def _prep(self, state_type, n=2, amplitudes="", basis_state="0"):
        ctx = MockContext(**{
            "State Type": state_type, "Qubit Count": str(n),
            "Amplitudes": amplitudes, "Target Basis State": basis_state,
            "Output Format": "cirq_json",
        })
        return CirqStatePreparation().transform(ctx, MockFlowFile())

    def test_uniform_equal_amplitudes(self, tmp_path):
        prep = self._prep("uniform", n=2)
        r = self._sv(prep, tmp_path)
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "cirq"
        assert r.attributes["sim.simulator"] == "statevector"
        probs = json.loads(r.contents)
        assert len(probs) == 4
        for p in probs.values():
            assert abs(p - 0.25) < 1e-6

    def test_statevector_data_emitted(self, tmp_path):
        prep = self._prep("uniform", n=2)
        r = self._sv(prep, tmp_path)
        sv = json.loads(r.attributes["sim.statevector_data"])
        assert len(sv) == 4  # 2^2 complex [real, imag] pairs
        assert all(len(pair) == 2 for pair in sv)

    def test_total_states_attribute(self, tmp_path):
        prep = self._prep("ghz", n=3)
        r = self._sv(prep, tmp_path)
        assert r.attributes["sim.total_states"] == "8"

    @pytest.mark.parametrize("k,n", [(1, 2), (3, 2), (5, 3)])
    def test_basis_top_result(self, k, n, tmp_path):
        prep = self._prep("basis", n=n, basis_state=str(k))
        r = self._sv(prep, tmp_path)
        assert r.attributes["sim.top_result"] == format(k, f"0{n}b")
        assert float(r.attributes["sim.top_probability"]) > 0.99

    def test_ghz_two_nonzero_states(self, tmp_path):
        prep = self._prep("ghz", n=3)
        # A real threshold excludes the zero-amplitude states (0.0 would keep all 2^n).
        r = self._sv(prep, tmp_path, **{"Probability Threshold": "0.001"})
        assert r.attributes["sim.num_nonzero_states"] == "2"

    def test_report_file_written(self, tmp_path):
        prep = self._prep("uniform", n=2)
        self._sv(prep, tmp_path, **{"Report File Name": "svtest"})
        assert (tmp_path / "svtest.html").exists()

    def test_unsupported_format_fails(self, tmp_path):
        ff = MockFlowFile(content=b"junk", attributes={"circuit.format": "qpy"})
        r = CirqStatevectorSimulator().transform(
            MockContext(**{
                "Reports Directory": str(tmp_path), "Flow Name": "t",
                "Probability Threshold": "0.0", "Max States": "32",
            }), ff,
        )
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_property_descriptors(self):
        names = {d.name for d in CirqStatevectorSimulator().getPropertyDescriptors()}
        assert names == {"Reports Directory", "Flow Name", "Report File Name",
                         "Probability Threshold", "Max States"}
