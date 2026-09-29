"""Unit tests for QrispGroverCircuit: the Qrisp row of the Grover builder
matrix. Portable (no JVM, no Docker): runs in the host suite and in the
Docker test profile.
"""

import pytest

from QrispGroverCircuit import QrispGroverCircuit
from QiskitGroverCircuit import QiskitGroverCircuit
from QiskitAerSimulator import QiskitAerSimulator
from CirqSimulator import CirqSimulator
from QrispSimulator import QrispSimulator

from conftest import MockContext, MockFlowFile, result_to_flowfile_merged


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _probabilities(qasm):
    """q0-left probability dict from a qasm2 source string."""
    from qiskit import qasm2
    from qiskit.quantum_info import Statevector

    circuit = qasm2.loads(qasm, custom_instructions=qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
    probs = Statevector(circuit).probabilities_dict()
    return {state[::-1]: p for state, p in probs.items()}


# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------


class TestDescriptors:

    def test_descriptors(self):
        proc = QrispGroverCircuit()
        names = [d.name for d in proc.getPropertyDescriptors()]
        assert names == ["Marked State", "Num Iterations", "Output Format"]

        by_name = {d.name: d for d in proc.getPropertyDescriptors()}
        assert by_name["Marked State"].default_value == "11"
        assert by_name["Num Iterations"].default_value == "1"
        assert by_name["Output Format"].default_value == "qasm2"
        assert by_name["Output Format"].allowable_values == ["qasm2"]
        for name in ("Marked State", "Num Iterations", "Output Format"):
            assert by_name[name].expression_language_scope == "FLOWFILE_ATTRIBUTES"


# ---------------------------------------------------------------------------
# Portable single-register output
# ---------------------------------------------------------------------------


class TestPortableOutput:

    @pytest.mark.parametrize("target", ["10", "01", "110", "011", "0110", "10110"])
    def test_portable_single_register(self, target):
        ctx = MockContext(
            **{
                "Marked State": target,
                "Num Iterations": "2",
                "Output Format": "qasm2",
            }
        )
        result = QrispGroverCircuit().transform(ctx, MockFlowFile())

        assert result.relationship == "success"
        content = bytes(result.contents).decode("utf-8")
        assert content == result.attributes["circuit.qasm2"]

        n = len(target)
        qreg_lines = [ln for ln in content.splitlines() if "qreg" in ln]
        assert len(qreg_lines) == 1
        assert "qreg q[{}];".format(n) in qreg_lines[0]
        assert not any(
            ln.strip().startswith("gate ") or ln.strip().startswith("creg")
            for ln in content.splitlines()
        )

        used_gates = set()
        for ln in content.splitlines():
            stripped = ln.strip()
            if not stripped or stripped.startswith(
                ("OPENQASM", "include", "qreg", "//")
            ):
                continue
            token = stripped.split("(")[0].split()[0].rstrip(";")
            if token:
                used_gates.add(token)
        assert used_gates <= {"h", "x", "cx", "rz"}

        assert result.attributes["circuit.num_qubits"] == str(n)
        assert result.attributes["circuit.bit_order"] == "q0_left"
        assert result.attributes["builder.component"] == "QrispGrover"
        assert result.attributes["builder.framework"] == "qrisp"
        assert result.attributes["grover.error"] == ""


# ---------------------------------------------------------------------------
# Marked-state orientation (regression guard for the reversal)
# ---------------------------------------------------------------------------


class TestMarkedStateOrientation:

    @pytest.mark.parametrize(
        "target, iterations",
        [("10", 1), ("110", 2), ("011", 2), ("0110", 3), ("10110", 4)],
    )
    def test_marked_state_is_q0_left(self, target, iterations):
        ctx = MockContext(
            **{
                "Marked State": target,
                "Num Iterations": str(iterations),
                "Output Format": "qasm2",
            }
        )
        result = QrispGroverCircuit().transform(ctx, MockFlowFile())
        assert result.relationship == "success"

        probs = _probabilities(result.attributes["circuit.qasm2"])
        top = max(probs, key=probs.get)
        assert top == target

    def test_probabilities_match_qiskit_builder(self):
        target, iterations = "110", 2
        ctx = MockContext(
            **{
                "Marked State": target,
                "Num Iterations": str(iterations),
                "Output Format": "qasm2",
            }
        )
        qrisp_result = QrispGroverCircuit().transform(ctx, MockFlowFile())
        qiskit_ctx = MockContext(
            **{
                "Marked State": target,
                "Num Iterations": str(iterations),
                "Insert Barriers": "false",
                "Output Format": "qasm2",
            }
        )
        qiskit_result = QiskitGroverCircuit().transform(qiskit_ctx, MockFlowFile())

        qrisp_probs = _probabilities(qrisp_result.attributes["circuit.qasm2"])
        qiskit_probs = _probabilities(qiskit_result.attributes["circuit.qasm2"])

        keys = set(qrisp_probs) | set(qiskit_probs)
        max_diff = max(
            abs(qrisp_probs.get(k, 0.0) - qiskit_probs.get(k, 0.0)) for k in keys
        )
        assert max_diff <= 1e-9


# ---------------------------------------------------------------------------
# Every counts engine
# ---------------------------------------------------------------------------

ENGINES = {
    "QiskitAerSimulator": QiskitAerSimulator,
    "CirqSimulator": CirqSimulator,
    "QrispSimulator": QrispSimulator,
}


class TestRunsOnEveryEngine:

    @pytest.mark.parametrize("engine_name", list(ENGINES))
    def test_runs_on_every_counts_engine(self, engine_name):
        target, iterations = "110", 2
        builder_ctx = MockContext(
            **{
                "Marked State": target,
                "Num Iterations": str(iterations),
                "Output Format": "qasm2",
            }
        )
        circuit_result = QrispGroverCircuit().transform(builder_ctx, MockFlowFile())
        assert circuit_result.relationship == "success"
        merged = result_to_flowfile_merged(circuit_result, MockFlowFile())

        engine_ctx = MockContext(**{"Shots": "1024", "Random Seed": "11"})
        sim_result = ENGINES[engine_name]().transform(engine_ctx, merged)

        assert sim_result.relationship == "success"
        assert sim_result.attributes["sim.top_result"] == "110"
        assert float(sim_result.attributes["sim.top_probability"]) >= 0.9
        assert sim_result.attributes["sim.bit_order"] == "q0_left"

        final = result_to_flowfile_merged(sim_result, merged)
        assert final.getAttribute("builder.component") == "QrispGrover"


# ---------------------------------------------------------------------------
# Attribute expression language, merge semantics, determinism
# ---------------------------------------------------------------------------


class TestAttributesAndMerge:

    def test_marked_state_from_attribute(self):
        ctx = MockContext(
            **{
                "Marked State": "${test.expected}",
                "Num Iterations": "2",
                "Output Format": "qasm2",
            }
        )
        flowfile = MockFlowFile(b"", {"test.expected": "110"})
        result = QrispGroverCircuit().transform(ctx, flowfile)
        assert result.relationship == "success"
        assert result.attributes["circuit.marked_state"] == "110"

    def test_stale_attributes_blanked(self):
        ctx = MockContext(
            **{
                "Marked State": "110",
                "Num Iterations": "2",
                "Output Format": "qasm2",
            }
        )
        upstream = MockFlowFile(
            b"",
            {
                "circuit.svg": "old",
                "circuit.qasm3": "old",
                "circuit.cirq_json": "old",
            },
        )
        result = QrispGroverCircuit().transform(ctx, upstream)
        merged = result_to_flowfile_merged(result, upstream)
        assert merged.getAttribute("circuit.svg") == ""
        assert merged.getAttribute("circuit.qasm3") == ""
        assert merged.getAttribute("circuit.cirq_json") == ""

    def test_deterministic(self):
        ctx = MockContext(
            **{
                "Marked State": "110",
                "Num Iterations": "2",
                "Output Format": "qasm2",
            }
        )
        first = QrispGroverCircuit().transform(ctx, MockFlowFile())
        second = QrispGroverCircuit().transform(ctx, MockFlowFile())
        assert first.attributes["circuit.qasm2"] == second.attributes["circuit.qasm2"]


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


class TestFailures:

    @pytest.mark.parametrize(
        "props, fragment",
        [
            (
                {
                    "Marked State": "1a0",
                    "Num Iterations": "1",
                    "Output Format": "qasm2",
                },
                "bitstring",
            ),
            (
                {"Marked State": " ", "Num Iterations": "1", "Output Format": "qasm2"},
                "bitstring",
            ),
            (
                {
                    "Marked State": "110100101",
                    "Num Iterations": "1",
                    "Output Format": "qasm2",
                },
                "1..8",
            ),
            (
                {
                    "Marked State": "110",
                    "Num Iterations": "0",
                    "Output Format": "qasm2",
                },
                ">= 1",
            ),
            (
                {
                    "Marked State": "110",
                    "Num Iterations": "two",
                    "Output Format": "qasm2",
                },
                "bad numeric",
            ),
            (
                {
                    "Marked State": "110",
                    "Num Iterations": "1",
                    "Output Format": "qasm3",
                },
                "qasm2",
            ),
        ],
    )
    def test_failures(self, props, fragment):
        ctx = MockContext(**props)
        flowfile = MockFlowFile(b"keep")
        result = QrispGroverCircuit().transform(ctx, flowfile)
        assert result.relationship == "failure"
        assert bytes(result.contents) == b"keep"
        assert fragment in result.attributes["grover.error"]
