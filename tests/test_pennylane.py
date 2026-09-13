"""
Tests for the PennyLane (QML) processors:
  PennylaneDatasetLoader, PennylaneFeatureEmbedding,
  PennylaneVariationalAnsatz, PennylaneExpectation.

Also covers cross-framework interop: a PennyLane-built qasm2 circuit must run on
the existing Qiskit/Cirq simulators (Tier-1 interchange).
"""

import json

import pytest

from PennylaneDatasetLoader import PennylaneDatasetLoader
from PennylaneFeatureEmbedding import PennylaneFeatureEmbedding
from PennylaneVariationalAnsatz import PennylaneVariationalAnsatz
from PennylaneExpectation import PennylaneExpectation
from PennylaneSimulator import PennylaneSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile


# ---------------------------------------------------------------------------
# PennylaneDatasetLoader
# ---------------------------------------------------------------------------

class TestPennylaneDatasetLoader:

    def test_synthetic_record_count_and_shape(self):
        ctx = MockContext(**{"Mode": "synthetic", "Num Samples": "12",
                             "Num Features": "4", "Seed": "1"})
        r = PennylaneDatasetLoader().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        recs = json.loads(r.contents)
        assert len(recs) == 12
        assert all(len(rec["features"]) == 4 for rec in recs)
        assert set(rec["label"] for rec in recs) == {0, 1}
        assert r.attributes["dataset.num_features"] == "4"
        assert r.attributes["dataset.num_classes"] == "2"

    def test_synthetic_is_seeded(self):
        ctx = MockContext(**{"Mode": "synthetic", "Num Samples": "6",
                             "Num Features": "2", "Seed": "7"})
        a = PennylaneDatasetLoader().transform(ctx, MockFlowFile()).contents
        b = PennylaneDatasetLoader().transform(ctx, MockFlowFile()).contents
        assert a == b

    def test_inline_passthrough(self):
        data = [{"features": [0.1, 0.2], "label": 0},
                {"features": [0.9, 0.8], "label": 1}]
        ctx = MockContext(**{"Mode": "inline", "Records": json.dumps(data)})
        r = PennylaneDatasetLoader().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert json.loads(r.contents) == data

    def test_inline_invalid_fails(self):
        ctx = MockContext(**{"Mode": "inline", "Records": '{"not": "a list"}'})
        r = PennylaneDatasetLoader().transform(ctx, MockFlowFile())
        assert r.relationship == "failure"
        assert "dataset.error" in r.attributes


# ---------------------------------------------------------------------------
# PennylaneFeatureEmbedding
# ---------------------------------------------------------------------------

class TestPennylaneFeatureEmbedding:

    def test_angle_from_property(self):
        ctx = MockContext(**{"Embedding": "angle", "Features": "[0.1, 0.5, 0.9]"})
        r = PennylaneFeatureEmbedding().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.embedding"] == "angle"
        assert r.attributes["circuit.framework"] == "pennylane"
        assert "OPENQASM 2.0" in r.contents.decode()

    def test_features_from_content_take_precedence(self):
        ctx = MockContext(**{"Embedding": "angle", "Features": "[0.0, 0.0]"})
        ff  = MockFlowFile(content=b"[0.1, 0.2, 0.3, 0.4]")
        r = PennylaneFeatureEmbedding().transform(ctx, ff)
        assert r.attributes["circuit.num_qubits"] == "4"  # from content, not property

    def test_amplitude_qubit_count(self):
        ctx = MockContext(**{"Embedding": "amplitude",
                             "Features": "[0.1, 0.2, 0.3, 0.4]"})
        r = PennylaneFeatureEmbedding().transform(ctx, MockFlowFile())
        assert r.attributes["circuit.num_qubits"] == "2"  # ceil(log2(4))

    def test_basis_embedding(self):
        ctx = MockContext(**{"Embedding": "basis", "Features": "[1, 0, 1]"})
        r = PennylaneFeatureEmbedding().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.num_qubits"] == "3"

    def test_bad_features_fail(self):
        ctx = MockContext(**{"Embedding": "angle", "Features": "not json"})
        r = PennylaneFeatureEmbedding().transform(ctx, MockFlowFile())
        assert r.relationship == "failure"
        assert "embedding.error" in r.attributes

    def test_non_numeric_features_fail(self):
        # Data-driven path: FlowFile content wins over the Features property,
        # and non-numeric entries must route to failure, not escape transform().
        ctx = MockContext(**{"Embedding": "angle", "Features": "[0.1, 0.2]"})
        r = PennylaneFeatureEmbedding().transform(
            ctx, MockFlowFile(content=b'["a", "b"]'))
        assert r.relationship == "failure"
        assert "embedding.error" in r.attributes


# ---------------------------------------------------------------------------
# PennylaneVariationalAnsatz
# ---------------------------------------------------------------------------

class TestPennylaneVariationalAnsatz:

    def test_standalone_strongly_entangling(self):
        ctx = MockContext(**{"Ansatz": "strongly_entangling", "Num Qubits": "3",
                             "Num Layers": "2", "Weight Seed": "42"})
        r = PennylaneVariationalAnsatz().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert r.attributes["circuit.format"] == "qasm2"
        assert r.attributes["circuit.num_qubits"] == "3"
        assert r.attributes["circuit.ansatz_mode"] == "standalone"
        assert int(r.attributes["circuit.num_params"]) == 2 * 3 * 3  # (L, n, 3)
        assert int(r.attributes["circuit.nonlocal_gates"]) > 0

    def test_basic_entangler(self):
        ctx = MockContext(**{"Ansatz": "basic_entangler", "Num Qubits": "4",
                             "Num Layers": "1", "Weight Seed": "0"})
        r = PennylaneVariationalAnsatz().transform(ctx, MockFlowFile())
        assert r.relationship == "success"
        assert int(r.attributes["circuit.num_params"]) == 1 * 4  # (L, n)

    def test_seeded_reproducible(self):
        ctx = MockContext(**{"Ansatz": "strongly_entangling", "Num Qubits": "2",
                             "Num Layers": "2", "Weight Seed": "5"})
        a = PennylaneVariationalAnsatz().transform(ctx, MockFlowFile()).contents
        b = PennylaneVariationalAnsatz().transform(ctx, MockFlowFile()).contents
        assert a == b

    def test_compose_onto_embedding(self):
        emb = PennylaneFeatureEmbedding().transform(
            MockContext(**{"Embedding": "angle", "Features": "[0.1, 0.2, 0.3]"}),
            MockFlowFile(),
        )
        ctx = MockContext(**{"Ansatz": "strongly_entangling", "Num Layers": "1",
                             "Weight Seed": "3"})
        r = PennylaneVariationalAnsatz().transform(ctx, result_to_flowfile(emb))
        assert r.relationship == "success"
        assert r.attributes["circuit.ansatz_mode"] == "compose"
        assert r.attributes["circuit.num_qubits"] == "3"  # inherited from embedding

    def test_compose_wrong_format_fails(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "qasm3"})
        r = PennylaneVariationalAnsatz().transform(
            MockContext(**{"Num Layers": "1"}), ff)
        assert r.relationship == "failure"
        assert "ansatz.error" in r.attributes


# ---------------------------------------------------------------------------
# PennylaneExpectation
# ---------------------------------------------------------------------------

class TestPennylaneExpectation:

    def _embedding_ff(self, features="[0.3, 1.1, 2.0]"):
        emb = PennylaneFeatureEmbedding().transform(
            MockContext(**{"Embedding": "angle", "Features": features}),
            MockFlowFile(),
        )
        return result_to_flowfile(emb)

    def test_expectation_lane(self):
        r = PennylaneExpectation().transform(MockContext(), self._embedding_ff())
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "pennylane"
        assert r.attributes["report.type"] == "simulation"
        assert r.attributes["sim.shots"] == "analytic"
        exp = json.loads(r.attributes["sim.expectations"])
        assert set(exp.keys()) == {"q0", "q1", "q2"}
        probs = json.loads(r.contents)
        assert abs(sum(probs.values()) - 1.0) < 1e-6

    def test_observable_x(self):
        ctx = MockContext(**{"Observable": "X"})
        r = PennylaneExpectation().transform(ctx, self._embedding_ff())
        assert r.attributes["sim.observable"] == "X"

    def test_finite_shots(self):
        ctx = MockContext(**{"Shots": "500"})
        r = PennylaneExpectation().transform(ctx, self._embedding_ff())
        assert r.attributes["sim.shots"] == "500"

    def test_non_qasm2_fails(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "qasm3"})
        r = PennylaneExpectation().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_malformed_qasm2_fails(self):
        ff = MockFlowFile(
            content=b'OPENQASM 2.0;\nqreg q[2];\nnotagate q[0];\n',
            attributes={"circuit.format": "qasm2"})
        r = PennylaneExpectation().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_unknown_observable_fails(self):
        # An EL-supplied Observable outside {Z, X, Y} must route to failure
        # rather than escape with a KeyError.
        ctx = MockContext(**{"Observable": "W"})
        r = PennylaneExpectation().transform(ctx, self._embedding_ff())
        assert r.relationship == "failure"
        assert "Unknown Observable" in r.attributes["sim.error"]


# ---------------------------------------------------------------------------
# PennylaneSimulator (counts lane)
# ---------------------------------------------------------------------------

class TestPennylaneSimulator:

    def _embedding_ff(self, embedding="angle", features="[0.3, 1.1, 2.0]"):
        emb = PennylaneFeatureEmbedding().transform(
            MockContext(**{"Embedding": embedding, "Features": features}),
            MockFlowFile(),
        )
        return result_to_flowfile(emb)

    def test_counts_contract(self):
        r = PennylaneSimulator().transform(
            MockContext(**{"Shots": "256"}), self._embedding_ff())
        assert r.relationship == "success"
        assert r.attributes["sim.framework"] == "pennylane"
        assert r.attributes["report.type"] == "simulation"
        assert r.attributes["sim.shots"] == "256"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 256
        assert all(isinstance(v, int) for v in counts.values())
        # sorted descending, top result mirrored into attributes
        vals = list(counts.values())
        assert vals == sorted(vals, reverse=True)
        assert r.attributes["sim.top_result"] == next(iter(counts))
        assert 0.0 < float(r.attributes["sim.top_probability"]) <= 1.0

    def test_basis_state_is_deterministic_msb_first(self):
        # Basis embedding [1,0,1] prepares |101>; PennyLane is MSB-first
        # (wire 0 = leftmost bit), so every shot must read "101".
        r = PennylaneSimulator().transform(
            MockContext(**{"Shots": "64"}),
            self._embedding_ff(embedding="basis", features="[1, 0, 1]"))
        assert r.relationship == "success"
        assert json.loads(r.contents) == {"101": 64}
        assert r.attributes["sim.top_result"] == "101"
        assert r.attributes["sim.top_probability"] == "1.0000"

    def test_runs_qiskit_built_circuit(self):
        from QiskitHadamardTransform import QiskitHadamardTransform
        h = QiskitHadamardTransform().transform(
            MockContext(**{"Qubit Count": "2", "Output Format": "qasm2"}),
            MockFlowFile(),
        )
        r = PennylaneSimulator().transform(
            MockContext(**{"Shots": "512"}), result_to_flowfile(h))
        assert r.relationship == "success"
        counts = json.loads(r.contents)
        assert sum(counts.values()) == 512
        assert set(counts.keys()) <= {"00", "01", "10", "11"}

    def test_non_qasm2_fails(self):
        ff = MockFlowFile(content=b"...", attributes={"circuit.format": "qasm3"})
        r = PennylaneSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_unparseable_qubit_count_fails(self):
        ff = MockFlowFile(content=b"OPENQASM 2.0;",
                          attributes={"circuit.format": "qasm2"})
        r = PennylaneSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes

    def test_malformed_qasm2_fails(self):
        # Has a qreg (passes the width check) but an unknown gate — the load/
        # execution failure must route to failure, not escape transform().
        ff = MockFlowFile(
            content=b'OPENQASM 2.0;\nqreg q[2];\nnotagate q[0];\n',
            attributes={"circuit.format": "qasm2"})
        r = PennylaneSimulator().transform(MockContext(), ff)
        assert r.relationship == "failure"
        assert "sim.error" in r.attributes


# ---------------------------------------------------------------------------
# Cross-framework interop (Tier 1) + full QML pipeline
# ---------------------------------------------------------------------------

class TestPennylaneInterop:

    def _embedding_ff(self):
        emb = PennylaneFeatureEmbedding().transform(
            MockContext(**{"Embedding": "angle", "Features": "[0.4, 1.2, 2.1]"}),
            MockFlowFile(),
        )
        return result_to_flowfile(emb)

    def test_runs_on_qiskit_aer(self):
        from QiskitAerSimulator import QiskitAerSimulator
        r = QiskitAerSimulator().transform(
            MockContext(**{"Shots": "256"}), self._embedding_ff())
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 256

    def test_runs_on_cirq(self):
        from CirqSimulator import CirqSimulator
        r = CirqSimulator().transform(
            MockContext(**{"Shots": "128"}), self._embedding_ff())
        assert r.relationship == "success"
        assert sum(json.loads(r.contents).values()) == 128

    def test_full_pipeline_embed_ansatz_expectation(self):
        emb = PennylaneFeatureEmbedding().transform(
            MockContext(**{"Embedding": "angle", "Features": "[0.5, 1.0, 1.5]"}),
            MockFlowFile(),
        )
        ans = PennylaneVariationalAnsatz().transform(
            MockContext(**{"Ansatz": "strongly_entangling", "Num Layers": "1",
                           "Weight Seed": "9"}),
            result_to_flowfile(emb),
        )
        assert ans.attributes["circuit.ansatz_mode"] == "compose"
        exp = PennylaneExpectation().transform(MockContext(), result_to_flowfile(ans))
        assert exp.relationship == "success"
        assert exp.attributes["sim.framework"] == "pennylane"
        assert abs(sum(json.loads(exp.contents).values()) - 1.0) < 1e-6
