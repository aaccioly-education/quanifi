"""Tests for PennylaneVariationalClassifier: the QML lane's end-to-end story
(DatasetLoader -> classifier -> training report)."""
import json

from conftest import MockContext, MockFlowFile, result_to_flowfile

from PennylaneDatasetLoader import PennylaneDatasetLoader
from PennylaneVariationalClassifier import PennylaneVariationalClassifier


def synthetic_dataset_flowfile(num_samples="24", num_features="2"):
    res = PennylaneDatasetLoader().transform(
        MockContext(**{"Mode": "synthetic", "Num Samples": num_samples,
                       "Num Features": num_features, "Seed": "7"}),
        MockFlowFile())
    assert res.relationship == "success"
    return result_to_flowfile(res)


class TestPennylaneVariationalClassifier:

    def test_trains_on_loader_output(self):
        ff = synthetic_dataset_flowfile()
        res = PennylaneVariationalClassifier().transform(
            MockContext(**{"Epochs": "25", "Learning Rate": "0.2", "Seed": "42"}),
            ff)
        assert res.relationship == "success"
        a = res.attributes
        assert a["train.framework"] == "pennylane"
        assert a["report.type"] == "training"
        assert a["train.num_qubits"] == "2"
        # separable blobs: the model must actually learn
        assert float(a["train.train_accuracy"]) >= 0.8
        assert float(a["train.val_accuracy"]) >= 0.6
        content = json.loads(res.contents.decode("utf-8"))
        loss = content["loss_history"]
        assert len(loss) == 25
        assert loss[-1] < loss[0]  # training reduced the loss
        assert content["trained_weights"]

    def test_iqp_basic_entangler_variant_learns(self):
        ff = synthetic_dataset_flowfile()
        res = PennylaneVariationalClassifier().transform(
            MockContext(**{"Embedding": "iqp", "Ansatz": "basic_entangler",
                           "Epochs": "25", "Learning Rate": "0.2", "Seed": "42"}),
            ff)
        assert res.relationship == "success"
        a = res.attributes
        assert a["train.embedding"] == "iqp"
        assert a["train.ansatz"] == "basic_entangler"
        assert float(a["train.train_accuracy"]) >= 0.7
        loss = json.loads(res.contents.decode("utf-8"))["loss_history"]
        assert loss[-1] < loss[0]

    def test_amplitude_embedding_packs_features_into_fewer_qubits(self):
        ff = synthetic_dataset_flowfile(num_samples="16", num_features="4")
        res = PennylaneVariationalClassifier().transform(
            MockContext(**{"Embedding": "amplitude", "Epochs": "10",
                           "Learning Rate": "0.2", "Seed": "42"}),
            ff)
        assert res.relationship == "success"
        a = res.attributes
        assert a["train.num_features"] == "4"
        assert a["train.num_qubits"] == "2"  # ceil(log2(4))
        assert a["train.embedding"] == "amplitude"

    def test_bad_content_routes_to_failure(self):
        ff = MockFlowFile(content=b"not json at all")
        res = PennylaneVariationalClassifier().transform(MockContext(), ff)
        assert res.relationship == "failure"
        assert "train.error" in res.attributes

    def test_single_class_routes_to_failure(self):
        records = [{"features": [0.1, 0.2], "label": 1} for _ in range(6)]
        ff = MockFlowFile(content=json.dumps(records).encode())
        res = PennylaneVariationalClassifier().transform(MockContext(), ff)
        assert res.relationship == "failure"
        assert "single class" in res.attributes["train.error"]

    def test_ragged_features_route_to_failure(self):
        records = [{"features": [0.1, 0.2], "label": 0},
                   {"features": [0.1], "label": 1},
                   {"features": [0.3, 0.4], "label": 0},
                   {"features": [0.5, 0.6], "label": 1}]
        ff = MockFlowFile(content=json.dumps(records).encode())
        res = PennylaneVariationalClassifier().transform(MockContext(), ff)
        assert res.relationship == "failure"
        assert "train.error" in res.attributes
