"""
Canonical bit order across every counts engine.

Contract: counts keys and sim.top_result use qubit 0 = LEFTMOST character
(Cirq convention), regardless of the producing framework, and every engine
emits sim.bit_order = "q0_left". Pinned with non-palindromic states so an
endianness regression cannot hide (palindromes read the same both ways).
"""

import json

import pytest

from conftest import MockContext, MockFlowFile, result_to_flowfile, result_to_flowfile_merged
import qaoa_reference as qref

from QiskitPhaseOracle import QiskitPhaseOracle
from QiskitGroverOperator import QiskitGroverOperator
from QiskitGroverSearch import QiskitGroverSearch
from QiskitAmplitudeAmplification import QiskitAmplitudeAmplification
from QiskitAerSimulator import QiskitAerSimulator
from CirqPhaseOracle import CirqPhaseOracle
from CirqGroverOperator import CirqGroverOperator
from CirqSimulator import CirqSimulator


# X on qubit 0 of a 2-qubit register -> |q0=1, q1=0>, canonical key '10'.
# q[1] is deliberately idle: it also pins the idle-qubit padding (Cirq's qasm
# importer drops untouched qubits, which would shrink the key to '1').
QASM_X_Q0 = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\nx q[0];\n'


def _x_q0_flowfile():
    return MockFlowFile(content=QASM_X_Q0.encode(),
                        attributes={"circuit.format": "qasm2"})


# ---------------------------------------------------------------------------
# The five counts engines agree on |q0=1, q1=0>
# ---------------------------------------------------------------------------

class TestCountsEnginesCanonicalOrder:

    @pytest.mark.parametrize("name", [
        "QiskitAerSimulator",
        "CirqSimulator",
        "QrispSimulator",
        "PennylaneSimulator",
        "BraketSimulator",
        "PyquilSimulator",
    ])
    def test_x_on_qubit0_reads_10(self, name):
        proc = getattr(__import__(name), name)()
        r = proc.transform(MockContext(**{"Shots": "128"}), _x_q0_flowfile())
        assert r.relationship == "success"
        assert r.attributes["sim.top_result"] == "10"
        assert r.attributes["sim.bit_order"] == "q0_left"
        counts = json.loads(r.contents)
        assert max(counts, key=counts.get) == "10"

    def test_cirq_json_idle_qubit_padded_from_attribute(self):
        """cirq_json input: idle qubits are recovered from circuit.num_qubits."""
        import cirq
        q = cirq.LineQubit.range(2)
        circ = cirq.Circuit([cirq.X(q[0])])  # q1 idle -> not in the circuit
        ff = MockFlowFile(
            content=cirq.to_json(circ).encode(),
            attributes={"circuit.format": "cirq_json", "circuit.num_qubits": "2"},
        )
        r = CirqSimulator().transform(MockContext(**{"Shots": "64"}), ff)
        assert r.attributes["sim.top_result"] == "10"


# ---------------------------------------------------------------------------
# Statevector lane agrees too
# ---------------------------------------------------------------------------

class TestStatevectorCanonicalOrder:

    def test_qiskit_statevector_labels(self, tmp_path):
        from QiskitStatevectorSimulator import QiskitStatevectorSimulator
        from qiskit import QuantumCircuit, qasm3, transpile

        qc = QuantumCircuit(2)
        qc.x(0)
        tc = transpile(qc, basis_gates=["h", "cx", "rz", "x"], optimization_level=0)
        ff = MockFlowFile(content=qasm3.dumps(tc).encode(),
                          attributes={"circuit.format": "qasm3"})
        r = QiskitStatevectorSimulator().transform(
            MockContext(**{
                "Reports Directory": str(tmp_path),
                "Flow Name": "test",
                "Probability Threshold": "0.0",
                "Max States": "8",
            }),
            ff,
        )
        assert r.attributes["sim.top_result"] == "10"
        assert r.attributes["sim.bit_order"] == "q0_left"
        probs = json.loads(r.contents)
        assert max(probs, key=probs.get) == "10"

    def test_cirq_statevector_labels(self, tmp_path):
        from CirqStatevectorSimulator import CirqStatevectorSimulator
        import cirq

        q = cirq.LineQubit.range(2)
        circ = cirq.Circuit([cirq.X(q[0]), cirq.I(q[1])])
        ff = MockFlowFile(content=cirq.to_json(circ).encode(),
                          attributes={"circuit.format": "cirq_json"})
        r = CirqStatevectorSimulator().transform(
            MockContext(**{
                "Reports Directory": str(tmp_path),
                "Flow Name": "test",
                "Probability Threshold": "0.0",
                "Max States": "8",
            }),
            ff,
        )
        assert r.attributes["sim.top_result"] == "10"
        assert r.attributes["sim.bit_order"] == "q0_left"


# ---------------------------------------------------------------------------
# State preparation: |k⟩ is the same physical state in all three frameworks
# and reads back as format(k, '0nb') everywhere
# ---------------------------------------------------------------------------

class TestStatePrepCanonicalOrder:

    def _readout(self, prep_result, simulator, shots="256"):
        sim_r = simulator.transform(
            MockContext(**{"Shots": shots}), result_to_flowfile(prep_result))
        return sim_r.attributes["sim.top_result"]

    def test_qiskit_basis_nonpalindrome(self):
        from QiskitStatePreparation import QiskitStatePreparation
        r = QiskitStatePreparation().transform(
            MockContext(**{"State Type": "basis", "Qubit Count": "2",
                           "Target Basis State": "1", "Amplitudes": "",
                           "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        assert self._readout(r, QiskitAerSimulator()) == "01"

    def test_qiskit_custom_nonpalindrome_index(self):
        """amps[1] = 1 on 2 qubits must read |01⟩ (canonical, like Cirq)."""
        from QiskitStatePreparation import QiskitStatePreparation
        r = QiskitStatePreparation().transform(
            MockContext(**{"State Type": "custom", "Qubit Count": "2",
                           "Target Basis State": "0", "Amplitudes": "[0,1,0,0]",
                           "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        assert self._readout(r, QiskitAerSimulator()) == "01"

    def test_qiskit_and_cirq_basis_agree_physically(self):
        """The same |k⟩ from both preps reads identically on the same simulator."""
        from QiskitStatePreparation import QiskitStatePreparation
        from CirqStatePreparation import CirqStatePreparation
        qiskit_r = QiskitStatePreparation().transform(
            MockContext(**{"State Type": "basis", "Qubit Count": "2",
                           "Target Basis State": "2", "Amplitudes": "",
                           "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        cirq_r = CirqStatePreparation().transform(
            MockContext(**{"State Type": "basis", "Qubit Count": "2",
                           "Target Basis State": "2", "Amplitudes": "",
                           "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        # Both through the same engine (Aer) — physical agreement, not just labels
        assert self._readout(qiskit_r, QiskitAerSimulator()) == "10"
        assert self._readout(cirq_r, QiskitAerSimulator()) == "10"


# ---------------------------------------------------------------------------
# QPE decode hint survives the canonical key order
# ---------------------------------------------------------------------------

class TestQPEDecodeCanonicalOrder:

    def test_qiskit_qpe_decode_positions(self):
        from QiskitPhaseEstimation import QiskitPhaseEstimation
        from fractions import Fraction
        cr = QiskitPhaseEstimation().transform(
            MockContext(**{"Phase Register Size": "3", "Builtin Unitary": "S",
                           "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        sr = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "2048"}), result_to_flowfile(cr))
        top = sr.attributes["sim.top_result"]
        positions = [int(p) for p in cr.attributes["result.bit_positions"].split(",")]
        bits = "".join(top[p] for p in positions)
        frac = Fraction(int(bits, 2), 2 ** len(positions))
        assert frac == Fraction(1, 4)  # S-gate eigenphase


# ---------------------------------------------------------------------------
# QAOA family: best_measurement / counts keys are canonical too
# ---------------------------------------------------------------------------

class TestQAOAChainCanonicalOrder:
    """H = Z0 - Z1 has the unique ground state q0=1, q1=0 → canonical '10'.

    Non-palindromic on purpose: a solver leaking its framework's native key
    order reports '01' instead. Also catches a mismatch between the sampled
    keys and the classical energy decode (the QrispQAOA mirror-training bug).
    Every solver here has moved to the train-only contract: best_measurement
    etc. come from QuantumQAOAEvaluator, downstream of the solver's own
    circuit output.
    """

    def _ham_flowfile(self):
        from QiskitHamiltonian import QiskitHamiltonian
        r = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "Z0 - Z1", "Num Qubits": "0"}),
            MockFlowFile(),
        )
        return result_to_flowfile(r)

    @pytest.mark.parametrize("name", ["QiskitQAOA", "CirqQAOA", "PennylaneQAOA", "QrispQAOA"])
    def test_ground_state_reads_10(self, name):
        proc = getattr(__import__(name), name)()
        ham_ff = self._ham_flowfile()
        r = proc.transform(
            MockContext(**{"Layers": "2", "Max Iterations": "100", "Random Seed": "7"}),
            ham_ff,
        )
        assert r.relationship == "success", r.attributes
        merged = result_to_flowfile_merged(r, ham_ff)
        _engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "7"}
        )
        assert ev.relationship == "success", ev.attributes
        a = ev.attributes
        assert a["qaoa.best_measurement"] == "10"
        assert float(a["qaoa.best_value"]) == -2.0
        assert _engine_res.attributes["sim.top_result"] == "10"
        assert float(a["qaoa.optimal_probability"]) >= 0.9
        assert _engine_res.attributes["sim.bit_order"] == "q0_left"
        counts = json.loads(_engine_res.contents)
        assert all(len(k) == 2 for k in counts)

    def test_qrisp_trains_unmirrored_cost(self):
        """Regression pin for the pre-0.2.0 QrispQAOA mirror-training bug
        (verified fact 6): H = -Z0 + 0.3 Z1 has its unique minimum at
        q0=0, q1=1 -> '01' (E=-1.3). The old code trained and reported the
        bit-reversed Hamiltonian and put P(10)=0.687 on the wrong state.

        The pin is on orientation, not optimizer quality.

        Updated for M5f (the QrispQAOA seeded-reproducibility fix). Before
        M5f, this test's own docstring recorded P('01') varying ~0.72-0.74
        depending on which tests ran earlier in the same process, and
        attributed that to "Qrisp's environment/global RNG state" leaking
        across calls. M5f's own investigation (2026-09-28) found the more
        precise mechanism: QrispQAOA's `Initial Parameters="random"` used to
        draw its initial angles from the *global* NumPy RNG inside Qrisp's
        `optimization_routine`, seeded here via `np.random.seed(seed)`; any
        code running between that seed call and Qrisp's own draw (e.g.
        another QrispQAOA FlowFile) could consume global RNG state and
        change the draw. The fix draws the initial point itself from a
        local `np.random.default_rng(seed)`, in Qrisp's own theta layout,
        and always passes it explicitly, so Qrisp's global-RNG branch is
        never reached.

        With the fix, this exact scenario (H, Layers, Max Iterations,
        Random Seed all as below, default Optimizer=COBYLA) was measured
        18 times across 3 fresh processes, both "clean" (nothing else run
        first) and "polluted" (24 unrelated QiskitQAOA/CirqQAOA/
        PennylaneQAOA/QrispQAOA runs at 6 different seeds run first in the
        same process): `qaoa.optimal_probability` was **exactly**
        0.7158203125 (733/1024) every single time, `sim.top_result` was
        '01' every time, and P(observed '10') was 0 or 1/1024 (0.0 or
        ~0.001) every time -- exactly the state the old mirror-trained
        circuit favoured (P=0.687). At that time `qaoa.optimal_value`
        itself still showed residual ~1e-5-level jitter run to run (COBYLA
        is not perfectly reentrant across separate scipy.optimize.minimize
        calls in one process even given a byte-identical initial point),
        which M5f attributed to a separate, pre-existing scipy/Qrisp
        numerical characteristic unrelated to seeding and left out of
        scope. M5g's investigation found the precise mechanism: Qrisp's
        exact-probability backend itself differs by up to 1 ulp between
        separately compiled circuits at the same theta, which COBYLA
        chaotically amplifies; rounding the classical cost function's
        returned energy to 12 decimals (`QrispQAOA.cl_cost_function`)
        removes it. Re-measured after M5g (10 runs, clean and 4x
        re-polluted): `qaoa.optimal_value` is now **exactly**
        `-1.139336393364` every time too, not just the shot-derived
        `optimal_probability`.

        The pin below keeps some margin under the measured 0.7158203125
        (rather than hard-pinning the exact fraction) so a harmless COBYLA
        micro-jitter elsewhere can never flip it, while still strictly
        discriminating the mirror bug (top/best must read '01', not '10',
        and mass on the mirror state '10' must stay near zero).
        """
        from QrispQAOA import QrispQAOA
        from QiskitHamiltonian import QiskitHamiltonian

        r = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "-1 Z0 + 0.3 Z1", "Num Qubits": "0"}),
            MockFlowFile(),
        )
        ham_ff = result_to_flowfile(r)
        res = QrispQAOA().transform(
            MockContext(**{"Layers": "2", "Max Iterations": "100", "Random Seed": "7"}),
            ham_ff,
        )
        assert res.relationship == "success", res.attributes
        merged = result_to_flowfile_merged(res, ham_ff)
        engine_res, _merged2, ev = qref.evaluate(
            merged, "QiskitAerSimulator", {"Shots": "1024", "Random Seed": "7"}
        )
        assert ev.relationship == "success", ev.attributes
        assert engine_res.attributes["sim.top_result"] == "01"
        assert ev.attributes["qaoa.best_measurement"] == "01"
        counts = json.loads(engine_res.contents)
        total = sum(counts.values())
        assert counts.get("10", 0) / total <= 0.05  # old mirror bug: P(10) = 0.687
        # Measured 0.7158203125 (733/1024), deterministically, in 18/18
        # trials after the M5f fix (see docstring); 0.7 leaves margin.
        assert float(ev.attributes["qaoa.optimal_probability"]) >= 0.7


# ---------------------------------------------------------------------------
# VQE family: the trained state's top readout is canonical too
# ---------------------------------------------------------------------------

class TestVQECanonicalOrder:
    """H = Z0 - Z1 has the unique ground state q0=1, q1=0 → canonical '10'.

    Same non-palindromic pin as the QAOA family: a solver leaking its
    framework's native key order reports '01' instead. VQE is decomposed, so
    each case runs its framework's Hamiltonian → Ansatz → VQE chain.
    """

    def _prepared_flowfile(self, framework):
        ham = getattr(__import__(framework + "Hamiltonian"),
                      framework + "Hamiltonian")().transform(
            MockContext(**{"Hamiltonian": "Z0 - Z1", "Num Qubits": "0"}),
            MockFlowFile(),
        )
        assert ham.relationship == "success"
        ans = getattr(__import__(framework + "Ansatz"),
                      framework + "Ansatz")().transform(
            MockContext(**{"Reps": "2"}), result_to_flowfile(ham))
        assert ans.relationship == "success"
        return result_to_flowfile(ans)

    @pytest.mark.parametrize("framework", ["Qiskit", "Qrisp"])
    def test_ground_state_reads_10(self, framework):
        proc = getattr(__import__(framework + "VQE"), framework + "VQE")()
        r = proc.transform(
            MockContext(**{"Max Iterations": "200", "Shots": "1024",
                           "Initial Parameters": "zeros"}),
            self._prepared_flowfile(framework),
        )
        assert r.relationship == "success"
        a = r.attributes
        assert float(a["vqe.optimal_value"]) < -1.5  # converged near -2.0
        assert a["sim.bit_order"] == "q0_left"
        assert a["sim.top_result"] == "10"
        counts = json.loads(r.contents)
        assert all(len(k) == 2 for k in counts)
        assert max(counts, key=counts.get) == "10"


# ---------------------------------------------------------------------------
# Grover end-to-end with a non-palindromic target
# ---------------------------------------------------------------------------

class TestGroverNonPalindrome:

    TARGET = "110"  # reads '011' if any stage leaks little-endian order

    def _qiskit_oracle(self, fmt="qasm3"):
        return QiskitPhaseOracle().transform(
            MockContext(**{"Marked State": self.TARGET, "Insert Barriers": "false",
                           "Output Format": fmt}),
            MockFlowFile(),
        )

    def _cirq_oracle(self, fmt="cirq_json"):
        return CirqPhaseOracle().transform(
            MockContext(**{"Marked State": self.TARGET, "Insert Barriers": "false",
                           "Output Format": fmt}),
            MockFlowFile(),
        )

    def test_qiskit_decomposed(self):
        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false",
                           "Output Format": "qasm3"}),
            result_to_flowfile(self._qiskit_oracle()),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}), result_to_flowfile(operator_r))
        assert sim_r.attributes["sim.top_result"] == self.TARGET

    def test_cirq_decomposed(self):
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false",
                           "Output Format": "cirq_json"}),
            result_to_flowfile(self._cirq_oracle()),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}), result_to_flowfile(operator_r))
        assert sim_r.attributes["sim.top_result"] == self.TARGET

    def test_cross_cirq_oracle_qiskit_rest(self):
        """CirqPhaseOracle (qasm2) → QiskitGroverOperator → QiskitAerSimulator."""
        operator_r = QiskitGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false",
                           "Output Format": "qasm3"}),
            result_to_flowfile(self._cirq_oracle(fmt="qasm2")),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}), result_to_flowfile(operator_r))
        assert sim_r.attributes["sim.top_result"] == self.TARGET

    def test_cross_qiskit_oracle_cirq_rest(self):
        """QiskitPhaseOracle (qasm2) → CirqGroverOperator → CirqSimulator."""
        operator_r = CirqGroverOperator().transform(
            MockContext(**{"Num Iterations": "2", "Insert Barriers": "false",
                           "Output Format": "cirq_json"}),
            result_to_flowfile(self._qiskit_oracle(fmt="qasm2")),
        )
        sim_r = CirqSimulator().transform(
            MockContext(**{"Shots": "1024"}), result_to_flowfile(operator_r))
        assert sim_r.attributes["sim.top_result"] == self.TARGET

    def test_qiskit_grover_search_all_in_one(self):
        r = QiskitGroverSearch().transform(
            MockContext(**{"Marked State": self.TARGET, "Num Iterations": "2",
                           "Shots": "1024", "Insert Barriers": "false"}),
            MockFlowFile(),
        )
        assert r.attributes["grover.top_result"] == self.TARGET
        counts = json.loads(r.contents)
        assert max(counts, key=counts.get) == self.TARGET

    def test_qrisp_grover_search_all_in_one(self):
        from QrispGroverSearch import QrispGroverSearch
        r = QrispGroverSearch().transform(
            MockContext(**{"Marked State": self.TARGET, "Num Iterations": "0",
                           "Shots": "1024"}),
            MockFlowFile(),
        )
        assert r.attributes["sim.top_result"] == self.TARGET

    def test_amplitude_amplification(self):
        """QiskitAmplitudeAmplification standalone → QiskitAerSimulator."""
        aa_r = QiskitAmplitudeAmplification().transform(
            MockContext(**{"Marked State": self.TARGET, "Num Iterations": "2",
                           "Insert Barriers": "false", "Output Format": "qasm3"}),
            MockFlowFile(),
        )
        sim_r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "1024"}), result_to_flowfile(aa_r))
        assert sim_r.attributes["sim.top_result"] == self.TARGET
