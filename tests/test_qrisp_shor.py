"""Tests for QrispShor (native shors_alg wrap)."""
import json

import pytest

from conftest import MockContext, MockFlowFile

from QrispShor import QrispShor


class TestQrispShor:

    def test_factors_fifteen(self):
        res = QrispShor().transform(
            MockContext(**{"Number To Factor": "15"}), MockFlowFile())
        assert res.relationship == "success"
        a = res.attributes
        assert {a["shor.factor_1"], a["shor.factor_2"]} == {"3", "5"}
        content = json.loads(res.contents.decode("utf-8"))
        assert content == {"number": 15, "factors": [3, 5]}
        assert a["shor.framework"] == "qrisp"

    @pytest.mark.slow
    def test_factors_twenty_one(self):
        res = QrispShor().transform(
            MockContext(**{"Number To Factor": "21"}), MockFlowFile())
        assert res.relationship == "success"
        a = res.attributes
        assert {a["shor.factor_1"], a["shor.factor_2"]} == {"3", "7"}

    def test_prime_routes_to_failure(self):
        res = QrispShor().transform(
            MockContext(**{"Number To Factor": "13"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "prime" in res.attributes["shor.error"]

    def test_non_integer_routes_to_failure(self):
        res = QrispShor().transform(
            MockContext(**{"Number To Factor": "rsa2048"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "shor.error" in res.attributes
