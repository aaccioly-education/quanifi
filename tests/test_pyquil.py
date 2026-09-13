"""
Tests for PyquilSimulator + PyquilGroverCircuit -- the pyquil (Rigetti) PyQVM
counts engine and its from-scratch Grover builder.

Modeled on test_braket.py and test_qsharp.py. Covers: the simulator contract
(sim.* attributes, counts JSON), seed reproducibility (noiseless AND with a
noise model active), a noise model visibly degrading the Grover peak, a
differential check against QiskitAerSimulator on non-palindromic marked
states (the bit-order regression pin -- never emit raw
framework-ordered keys" rule), an idle-qubit regression (a declared-but-
untouched qubit must not silently vanish from the readout, mirroring the
Braket ensure_full_register_measure fix), and cross-engine interop of the
PyquilGroverCircuit -> qasm2 output against every other counts engine in
this repo.

PyQVM is a pure-Python state-vector/density-matrix simulator, so runtime
scales with (gate count) x (shots) x (2**num_qubits); tests keep shot counts
modest and mark the 3- and 4-qubit cases `slow` accordingly.
"""
import json

import pytest

from PyquilGroverCircuit import PyquilGroverCircuit
from PyquilSimulator import PyquilSimulator
from QiskitAerSimulator import QiskitAerSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


def _grover_build(target, iterations):
    """Build a Grover circuit natively in pyquil and return its
    FlowFileTransformResult (qasm2 content + circuit.*/builder.* attrs)."""
    r = PyquilGroverCircuit().transform(
        MockContext(**{"Marked State": target, "Num Iterations": str(iterations)}),
        MockFlowFile(),
    )
    assert r.relationship == "success", r.attributes
    return r


def _grover_ff(target, iterations):
    return result_to_flowfile(_grover_build(target, iterations))


# ---------------------------------------------------------------------------
# Builder: attribute contract, no simulation involved.
# ---------------------------------------------------------------------------

class TestPyquilGroverCircuitBuilder:

    def test_qasm2_contract_and_metrics(self):
        r = _grover_build("110", 2)
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.marked_state"] == "110"
        assert r.attributes["circuit.num_iterations"] == "2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.bit_order"] == "canonical"
        assert r.attributes["builder.framework"] == "pyquil"
        assert r.attributes["builder.component"] == "PyquilGrover"
        # Stale presentation attrs from other frameworks must be blanked.
        assert r.attributes["circuit.qasm3"] == ""
        assert r.attributes["circuit.svg"] == ""
        assert int(r.attributes["circuit.gate_count"]) > 0
        assert int(r.attributes["circuit.depth"]) > 0
        qasm2 = r.contents.decode("utf-8")
        assert qasm2 == r.attributes["circuit.qasm2"]
        assert 'include "qelib1.inc"' in qasm2
        assert "qreg q[3]" in qasm2
        # No inline gate redefinitions (the stdgates.inc lesson braket_qasm.py
        # documents) -- only standard-include gate names appear as calls.
        assert "gate ccx" not in qasm2

    def test_invalid_marked_state_fails(self):
        r = PyquilGroverCircuit().transform(
            MockContext(**{"Marked State": "10x", "Num Iterations": "1"}),
            MockFlowFile(),
        )
        assert r.relationship == "failure"
        assert "grover.error" in r.attributes


# ---------------------------------------------------------------------------
# Simulator: counts contract.
# ---------------------------------------------------------------------------

class TestPyquilSimulatorContract:

    def test_counts_contract_and_grover_peak(self):
        # n=2, 1 iteration is the textbook exact case (peak probability 1).
        ff = _grover_ff("10", 1)
        r = PyquilSimulator().transform(MockContext(**{"Shots": "256"}), ff)
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "pyquil"
        assert r.attributes["sim.component"] == "PyquilSimulator"
        assert r.attributes["sim.backend"] == "PyQVM"
        assert r.attributes["sim.bit_order"] == "q0_left"
        assert r.attributes["report.type"] == "simulation"
        assert r.attributes["sim.noise_model"] == "none"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 256
        vals = list(counts.values())
        assert vals == sorted(vals, reverse=True)
        assert r.attributes["sim.top_result"] == next(iter(counts))
        assert r.attributes["sim.top_result"] == "10"
        assert float(r.attributes["sim.top_probability"]) == 1.0

    def test_missing_format_fails(self):
        r = PyquilSimulator().transform(MockContext(), MockFlowFile(content=b"..."))
        assert r.relationship == "failure"
        assert "circuit.format" in r.attributes["sim.error"]

    def test_unsupported_format_fails(self):
        r = PyquilSimulator().transform(
            MockContext(),
            MockFlowFile(content=b"...", attributes={"circuit.format": "qpy"}),
        )
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes


# ---------------------------------------------------------------------------
# Seed reproducibility -- noiseless AND with a noise model active.
# ---------------------------------------------------------------------------

class TestPyquilSeedReproducibility:

    def _run(self, ff, **props):
        return PyquilSimulator().transform(MockContext(**props), ff)

    def test_same_seed_identical_noiseless(self):
        ff = _grover_ff("11", 1)
        r1 = self._run(ff, **{"Shots": "128", "Random Seed": "42"})
        r2 = self._run(ff, **{"Shots": "128", "Random Seed": "42"})
        assert json.loads(r1.contents) == json.loads(r2.contents)
        assert r1.attributes["run.seed"] == "42"

    def test_different_seed_diverges_noiseless(self):
        # "11" at 1 iteration is n=2's exact case (peak probability 1.0, see
        # TestPyquilSimulatorContract): every shot lands on the marked state
        # regardless of seed, so there is no randomness left for a seed to
        # affect. "110" at 2 iterations has a sub-1.0 peak (~0.93, see
        # TestPyquilNoiseDegradesGroverPeak), leaving real shot-to-shot
        # variance for different seeds to diverge on.
        ff = _grover_ff("110", 2)
        r1 = self._run(ff, **{"Shots": "128", "Random Seed": "1"})
        r2 = self._run(ff, **{"Shots": "128", "Random Seed": "2"})
        assert json.loads(r1.contents) != json.loads(r2.contents)

    def test_same_seed_identical_with_noise(self):
        ff = _grover_ff("11", 1)
        props = {"Shots": "128", "Random Seed": "42",
                  "Noise Model": "bit_flip", "Error Probability": "0.15"}
        r1 = self._run(ff, **props)
        r2 = self._run(ff, **props)
        assert r1.relationship == "success" and r2.relationship == "success"
        assert json.loads(r1.contents) == json.loads(r2.contents)

    def test_different_seed_diverges_with_noise(self):
        ff = _grover_ff("11", 1)
        base = {"Shots": "128", "Noise Model": "bit_flip", "Error Probability": "0.15"}
        r1 = self._run(ff, **{**base, "Random Seed": "1"})
        r2 = self._run(ff, **{**base, "Random Seed": "2"})
        assert json.loads(r1.contents) != json.loads(r2.contents)


# ---------------------------------------------------------------------------
# Noise visibly degrades the Grover peak.
# ---------------------------------------------------------------------------

class TestPyquilNoiseDegradesGroverPeak:

    @pytest.mark.slow
    def test_depolarizing_noise_lowers_marked_state_probability(self):
        ff = _grover_ff("110", 2)
        shots = 512
        clean = PyquilSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "5"}), ff)
        noisy = PyquilSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "5",
                           "Noise Model": "depolarizing", "Error Probability": "0.25"}),
            ff)
        assert clean.relationship == "success"
        assert noisy.relationship == "success"
        assert noisy.attributes["sim.noise_model"] == "depolarizing"
        assert json.loads(noisy.attributes["sim.noise_params"]) == {"probability": 0.25}

        clean_counts = json.loads(clean.contents)
        noisy_counts = json.loads(noisy.contents)
        clean_peak = clean_counts.get("110", 0) / shots
        noisy_peak = noisy_counts.get("110", 0) / shots
        assert clean.attributes["sim.top_result"] == "110"
        assert noisy_peak < clean_peak

    def test_all_six_noise_channels_accepted(self):
        ff = _grover_ff("11", 1)
        for channel in ("relaxation", "dephasing", "depolarizing",
                        "phase_flip", "bit_flip", "bitphase_flip"):
            r = PyquilSimulator().transform(
                MockContext(**{"Shots": "32", "Noise Model": channel,
                               "Error Probability": "0.05"}),
                ff)
            assert r.relationship == "success", (channel, r.attributes)
            assert r.attributes["sim.noise_model"] == channel


# ---------------------------------------------------------------------------
# Differential agreement with QiskitAerSimulator -- non-palindromic marked
# states, the bit-order regression pin (see tests/test_bit_order.py).
# ---------------------------------------------------------------------------

class TestPyquilDifferentialAgreementWithAer:

    @pytest.mark.parametrize("target,iterations,shots", [
        ("10", 1, 512),
        ("110", 2, 256),
    ])
    def test_top_result_agrees_with_aer(self, target, iterations, shots):
        ff = _grover_ff(target, iterations)
        pyquil_r = PyquilSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "3"}), ff)
        aer_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "3"}), ff)
        assert pyquil_r.relationship == "success"
        assert aer_r.relationship == "success"
        assert pyquil_r.attributes["sim.top_result"] == target
        assert aer_r.attributes["sim.top_result"] == target
        assert pyquil_r.attributes["sim.top_result"] == aer_r.attributes["sim.top_result"]

    @pytest.mark.slow
    def test_top_result_agrees_with_aer_4_qubit(self):
        target, iterations, shots = "0111", 2, 128
        ff = _grover_ff(target, iterations)
        pyquil_r = PyquilSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "3"}), ff)
        aer_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": str(shots), "Random Seed": "3"}), ff)
        assert pyquil_r.relationship == "success"
        assert aer_r.relationship == "success"
        assert pyquil_r.attributes["sim.top_result"] == target
        assert aer_r.attributes["sim.top_result"] == target


# ---------------------------------------------------------------------------
# Idle-qubit regression: a declared register wider than the qubits actually
# gated must still report its full width -- mirrors the Braket
# ensure_full_register_measure fix (see braket_qasm.py / test_braket.py).
# ---------------------------------------------------------------------------

class TestPyquilIdleQubitRegression:

    # width 3, only q0 and q2 are gated; q1 is declared but never touched.
    IDLE_QASM2 = (
        'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\nx q[0];\nx q[2];\n'
    )

    def test_untouched_middle_qubit_is_not_dropped(self):
        ff = MockFlowFile(content=self.IDLE_QASM2.encode(),
                          attributes={"circuit.format": "qasm2"})
        r = PyquilSimulator().transform(MockContext(**{"Shots": "64"}), ff)
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert all(len(k) == 3 for k in counts)
        assert r.attributes["sim.top_result"] == "101"


# ---------------------------------------------------------------------------
# Cross-engine interop: PyquilGroverCircuit's qasm2 output runs correctly
# (marked state is the top outcome) on every other counts engine.
# ---------------------------------------------------------------------------

class TestPyquilGroverCircuitCrossEngineInterop:

    @pytest.mark.parametrize("target,iterations", [
        ("10", 1), ("110", 2), ("0111", 2), ("0110", 2),
    ])
    @pytest.mark.parametrize("engine_name", [
        "QiskitAerSimulator", "CirqSimulator", "QrispSimulator", "PennylaneSimulator",
    ])
    def test_marked_state_is_top_outcome(self, engine_name, target, iterations):
        ff = _grover_ff(target, iterations)
        engine_cls = getattr(__import__(engine_name), engine_name)
        r = engine_cls().transform(MockContext(**{"Shots": "256"}), ff)
        assert r.relationship == "success", r.attributes
        assert r.attributes["sim.top_result"] == target
