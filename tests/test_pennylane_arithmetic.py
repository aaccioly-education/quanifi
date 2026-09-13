"""PennylaneQuantumArithmetic: the contract, and the two traps it sits on.

This lane exists to replace Qrisp in the confirmatory experiment, because
every Qrisp adder compiles a different circuit for different operands. The
value of the replacement depends entirely on it being *correct* and
*operand-invariant*, and both properties fail silently if either of the two
mappings below is wrong:

1. **The qasm2 wire map.** `qml.to_openqasm` numbers the qreg by order of
   first use unless `wires=` is passed. Without it, a circuit whose first gate
   lands on wire 2 emits that wire as `q[0]`, permuting the operand registers
   relative to `arithmetic_spec`'s layout. PennyLane's own simulation still
   looks right (it keeps wire labels), so this is invisible until the exported
   qasm is read back -- where `1+0` and `0+1` produced byte-identical circuits.
2. **Endianness.** PennyLane's arithmetic templates are big-endian; the rest of
   this project is little-endian. Over two bits, 0 and 3 are palindromes, so a
   wrong mapping still yields the right answer for 0+0, 0+3, 3+0 and 3+3 and
   fails only on operands containing a 1.

Both are pinned here with non-palindromic operands, per the standing rule in
tests/test_bit_order.py.
"""
import pytest

import arithmetic_spec as aspec
from conftest import MockContext, MockFlowFile
from PennylaneQuantumArithmetic import PennylaneQuantumArithmetic

CASES = [(0, 0), (1, 0), (0, 1), (1, 1), (2, 0), (3, 1), (1, 3), (3, 3), (2, 2)]

#: Operand pairs whose two-bit encodings are NOT palindromes -- the only ones
#: that can expose a reversed register.
NON_PALINDROMIC = [(1, 0), (0, 1), (1, 1), (3, 1), (1, 3), (2, 0), (2, 2)]


def build(a, b, bit_width=2, **overrides):
    props = {"Implementation": "semiadder", "Operation": "add",
             "Operand A": str(a), "Operand B": str(b),
             "Bit Width": str(bit_width)}
    props.update(overrides)
    return PennylaneQuantumArithmetic().transform(
        MockContext(**props), MockFlowFile())


def simulate(result):
    """Statevector-simulate the emitted qasm; return {q0_left_key: probability}."""
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import Statevector
    qc = QuantumCircuit.from_qasm_str(result.contents.decode())
    probs = Statevector.from_instruction(qc).probabilities_dict()
    return {key[::-1]: p for key, p in probs.items()}


class TestContract:
    def test_builds(self):
        assert build(3, 1).relationship == "success"

    def test_emits_the_circuit_contract(self):
        attrs = build(3, 1).attributes
        assert attrs["circuit.format"] == "qasm2"
        assert attrs["circuit.framework"] == "pennylane"
        assert attrs["circuit.qasm2"].startswith("OPENQASM 2.0")
        for key in ("circuit.num_qubits", "circuit.depth", "circuit.gate_count",
                    "circuit.nonlocal_gates", "circuit.t_count"):
            assert attrs[key].isdigit(), key

    def test_blanks_the_other_frameworks_presentation_attrs(self):
        """NiFi merges attributes; a stale SVG would render the wrong circuit."""
        attrs = build(3, 1).attributes
        assert attrs["circuit.svg"] == ""
        assert attrs["circuit.qasm3"] == ""

    def test_emits_no_measurements(self):
        """Measurement belongs to the simulator, not the builder."""
        qasm = build(3, 1).contents.decode()
        assert "measure" not in qasm
        assert "creg" not in qasm

    def test_emits_the_arithmetic_contract(self):
        attrs = build(3, 1).attributes
        assert attrs[aspec.ATTR_IMPLEMENTATION] == "semiadder"
        assert attrs[aspec.ATTR_FRAMEWORK] == "pennylane"
        assert attrs[aspec.ATTR_EXPECTED_RESULT] == "4"
        assert attrs["sim.bit_order"] == aspec.BIT_ORDER

    def test_semiadder_width_is_three_n_plus_one(self):
        assert build(3, 1, 2).attributes["circuit.num_qubits"] == "7"
        assert build(3, 1, 3).attributes["circuit.num_qubits"] == "10"

    def test_outadder_remains_available_for_pilot_replay(self):
        attrs = build(3, 1, Implementation="outadder").attributes
        assert attrs[aspec.ATTR_IMPLEMENTATION] == "outadder"
        assert attrs["circuit.num_qubits"] == "9"


class TestRejections:
    def test_unknown_implementation(self):
        result = build(1, 1, Implementation="ripple_carry")
        assert result.relationship == "failure"
        assert "unknown implementation" in result.attributes["arithmetic.error"]

    def test_unsupported_operation(self):
        result = build(1, 1, Operation="multiply")
        assert result.relationship == "failure"
        assert "not implemented" in result.attributes["arithmetic.error"]

    def test_non_integer_operand(self):
        result = build(1, 1, **{"Operand A": "two"})
        assert result.relationship == "failure"
        assert "non-integer" in result.attributes["arithmetic.error"]

    def test_operand_wider_than_the_register(self):
        result = build(9, 1, 2)
        assert result.relationship == "failure"


class TestCorrectness:
    """The answer must be certain, on every input, in the project's bit order."""

    @pytest.mark.parametrize("a,b", CASES)
    def test_expected_bitstring_is_certain(self, a, b):
        result = build(a, b)
        assert result.relationship == "success"
        expected = result.attributes[aspec.ATTR_EXPECTED_BITS]
        probs = simulate(result)
        assert probs.get(expected, 0.0) > 0.999, (
            "%d+%d expected %s, got %s"
            % (a, b, expected, max(probs, key=probs.get)))

    @pytest.mark.parametrize("a,b", NON_PALINDROMIC)
    def test_non_palindromic_operands_pin_the_wire_map(self, a, b):
        """Guards both the endianness and the to_openqasm renumbering bug.

        A permuted or reversed register still passes on palindromic operands,
        so these cases carry the whole weight of the mapping.
        """
        result = build(a, b)
        probs = simulate(result)
        top = max(probs, key=probs.get)
        layout = PennylaneQuantumArithmetic._layout(2, "semiadder")
        # Read the sum straight off the result wires, little-endian.
        value = sum(1 << i for i, wire in enumerate(layout.result_qubits)
                    if top[wire] == "1")
        assert value == a + b, "%d+%d read back as %d from %s" % (a, b, value, top)

    def test_operands_survive_the_export_distinctly(self):
        """1+0 and 0+1 must not produce the same circuit.

        They did, before `wires=` was passed to to_openqasm: the qreg was
        numbered by order of first use, so whichever operand was set landed on
        q[0] either way.
        """
        assert build(1, 0).contents != build(0, 1).contents


class TestMutatorInterop:
    def test_carry_qubit_is_the_highest_result_wire(self):
        """`carry.break` takes max(result_qubits); it must be the real carry."""
        result = build(3, 1)          # 3+1 = 4 -> only the carry bit is set
        layout = PennylaneQuantumArithmetic._layout(2, "semiadder")
        carry = max(layout.result_qubits)
        top = max(simulate(result), key=simulate(result).get)
        assert top[carry] == "1", "carry not on wire %d in %s" % (carry, top)
        # and a non-carrying case must leave it clear
        plain = build(1, 1)           # 1+1 = 2, fits in two bits
        top_plain = max(simulate(plain), key=simulate(plain).get)
        assert top_plain[carry] == "0"

    @pytest.mark.parametrize("a,b", CASES)
    def test_prep_qubits_matches_the_operand_bits(self, a, b):
        wires = [w for w in build(a, b).attributes[aspec.ATTR_PREP_QUBITS].split(",") if w]
        assert len(wires) == bin(a).count("1") + bin(b).count("1")

    def test_prep_qubits_name_operand_wires_only(self):
        """A prep wire inside the result register would corrupt the body split."""
        attrs = build(3, 3).attributes
        layout = PennylaneQuantumArithmetic._layout(2, "semiadder")
        operand_wires = set(layout.a_qubits) | set(layout.b_qubits)
        wires = {int(w) for w in attrs[aspec.ATTR_PREP_QUBITS].split(",") if w}
        assert wires <= operand_wires


class TestDependencies:
    def test_declares_pennylane_only(self):
        """Pulling pennylane-qiskit would drag the qiskit<2.5 pin in for nothing."""
        deps = PennylaneQuantumArithmetic.ProcessorDetails.dependencies
        assert deps == ["pennylane>=0.40"]

    def test_carries_the_sys_path_preamble(self):
        """Without it the module-scope arithmetic_spec import fails inside NiFi.

        pytest cannot detect its absence -- conftest puts nifi_extensions/ on
        sys.path itself -- so this asserts on the source text.
        """
        import pathlib
        source = (pathlib.Path(__file__).resolve().parent.parent
                  / "nifi_extensions" / "PennylaneQuantumArithmetic.py").read_text()
        assert "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))" in source
