"""Reproducibility: the Random Seed property must make runs bit-identical.

One test per seeding mechanism (Aer seed_simulator, cirq.Simulator(seed),
PennyLane device seed, qiskit_algorithms algorithm_globals + sampler seed,
StatevectorSampler seed, NumPy best-effort for Qrisp), plus one check that
different seeds actually produce different samples (guards against a seed
property that is silently ignored).
"""
import json

from conftest import MockContext, MockFlowFile, result_to_flowfile

from QiskitAerSimulator import QiskitAerSimulator
from CirqSimulator import CirqSimulator
from PennylaneSimulator import PennylaneSimulator
from QrispSimulator import QrispSimulator
from QiskitBellState import QiskitBellState
from QiskitAmplitudeEstimation import QiskitAmplitudeEstimation
from CirqAmplitudeEstimation import CirqAmplitudeEstimation
from MaxCutProblem import MaxCutProblem
from QiskitQAOA import QiskitQAOA

BELL_QASM2 = (
    'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
    "qreg q[2];\ncreg c[2];\nh q[0];\ncx q[0],q[1];\n"
)


def bell_ff():
    return MockFlowFile(content=BELL_QASM2.encode(),
                        attributes={"circuit.format": "qasm2"})


def run_twice(processor_cls, props, ff_factory):
    a = processor_cls().transform(MockContext(**props), ff_factory())
    b = processor_cls().transform(MockContext(**props), ff_factory())
    assert a.relationship == "success" and b.relationship == "success"
    return a, b


class TestSimulatorSeeds:

    def test_aer_same_seed_identical_counts(self):
        props = {"Shots": "512", "Random Seed": "1234"}
        a, b = run_twice(QiskitAerSimulator, props, bell_ff)
        assert a.contents == b.contents
        assert a.attributes["run.seed"] == "1234"

    def test_aer_different_seeds_differ(self):
        a = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "512", "Random Seed": "1"}), bell_ff())
        b = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "512", "Random Seed": "2"}), bell_ff())
        assert a.contents != b.contents

    def test_cirq_same_seed_identical_counts(self):
        props = {"Shots": "512", "Random Seed": "77"}
        a, b = run_twice(CirqSimulator, props, bell_ff)
        assert a.contents == b.contents

    def test_pennylane_same_seed_identical_counts(self):
        props = {"Shots": "512", "Random Seed": "9"}
        a, b = run_twice(PennylaneSimulator, props, bell_ff)
        assert a.contents == b.contents

    def test_qrisp_same_seed_identical_counts(self):
        # best-effort global-NumPy seeding; pinned here so a Qrisp upgrade that
        # stops drawing from NumPy's global RNG is caught
        props = {"Shots": "512", "Random Seed": "5"}
        a, b = run_twice(QrispSimulator, props, bell_ff)
        assert a.contents == b.contents

    def test_perf_elapsed_emitted(self):
        a = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "128"}), bell_ff())
        assert float(a.attributes["perf.elapsed_seconds"]) >= 0.0
        assert "run.seed" not in a.attributes  # unseeded run stays unmarked


class TestAlgorithmSeeds:

    def test_bell_state_same_seed_identical(self):
        props = {"Shots": "1024", "Random Seed": "42"}
        a, b = run_twice(QiskitBellState, props, MockFlowFile)
        assert a.contents == b.contents

    def test_qiskit_ae_same_seed_identical_mle(self):
        props = {"Probability": "0.2", "Evaluation Qubits": "2",
                 "Method": "canonical", "Shots": "1024", "Random Seed": "7"}
        a, b = run_twice(QiskitAmplitudeEstimation, props, MockFlowFile)
        assert a.attributes["ae.mle"] == b.attributes["ae.mle"]
        assert a.contents == b.contents

    def test_cirq_ae_same_seed_identical(self):
        props = {"Probability": "0.2", "Evaluation Qubits": "3",
                 "Shots": "512", "Random Seed": "3"}
        a, b = run_twice(CirqAmplitudeEstimation, props, MockFlowFile)
        assert a.contents == b.contents

    def test_qaoa_same_seed_identical_end_to_end(self):
        # random init + optimizer + sampler + final Aer counts, all seeded
        def ham_ff():
            res = MaxCutProblem().transform(MockContext(), MockFlowFile())
            return result_to_flowfile(res)
        props = {"Random Seed": "11"}
        a, b = run_twice(QiskitQAOA, props, ham_ff)
        assert a.attributes["qaoa.optimal_parameters"] == b.attributes["qaoa.optimal_parameters"]
        assert a.attributes["qaoa.optimal_value"] == b.attributes["qaoa.optimal_value"]
        assert a.contents == b.contents
        assert a.attributes["run.seed"] == "11"
