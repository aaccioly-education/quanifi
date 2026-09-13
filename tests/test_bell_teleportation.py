"""Tests for the entanglement learner demos: QiskitBellState and
QiskitTeleportation (dynamic-circuit protocol, self-verifying)."""
import json
import math

import pytest

from conftest import MockContext, MockFlowFile

from QiskitBellState import QiskitBellState
from QiskitTeleportation import QiskitTeleportation


class TestQiskitBellState:

    @pytest.mark.parametrize("state,pair", [
        ("phi_plus", {"00", "11"}),
        ("phi_minus", {"00", "11"}),
        ("psi_plus", {"01", "10"}),
        ("psi_minus", {"01", "10"}),
    ])
    def test_all_four_states_fully_correlated(self, state, pair):
        res = QiskitBellState().transform(
            MockContext(**{"Bell State": state, "Shots": "2048"}), MockFlowFile())
        assert res.relationship == "success"
        a = res.attributes
        assert a["bell.state"] == state
        assert float(a["bell.correlation"]) == 1.0  # ideal simulator
        assert a["report.type"] == "simulation"
        counts = json.loads(res.contents.decode("utf-8"))
        assert set(counts) <= pair
        # roughly balanced superposition
        assert all(v > 700 for v in counts.values())

    def test_unknown_state_routes_to_failure(self):
        res = QiskitBellState().transform(
            MockContext(**{"Bell State": "epr"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "bell.error" in res.attributes


class TestQiskitTeleportation:

    @pytest.mark.parametrize("theta,phi", [
        (math.pi / 3, math.pi / 4),
        (0.3, 2.1),
        (math.pi / 2, 0.0),
    ])
    def test_fidelity_is_one_on_ideal_simulator(self, theta, phi):
        res = QiskitTeleportation().transform(
            MockContext(**{"Theta": str(theta), "Phi": str(phi), "Shots": "1024"}),
            MockFlowFile())
        assert res.relationship == "success"
        a = res.attributes
        assert float(a["teleport.fidelity"]) == 1.0
        assert "report.type" not in a  # default all-attributes card by design
        counts = json.loads(res.contents.decode("utf-8"))
        # verification bit (position 2, q0-left keys) must always be 0
        assert all(k[2] == "0" for k in counts)
        # the two Bell-measurement bits stay random: all four branches occur
        assert len(counts) == 4

    def test_bad_angle_routes_to_failure(self):
        res = QiskitTeleportation().transform(
            MockContext(**{"Theta": "north"}), MockFlowFile())
        assert res.relationship == "failure"
        assert "teleport.error" in res.attributes
