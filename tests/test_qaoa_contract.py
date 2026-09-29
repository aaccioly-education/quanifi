"""Unit tests for nifi_extensions/qaoa_contract.py -- the shared, non-circuit
QAOA machinery (parsing, validation, the portable qasm2 profile, energies,
the attribute contract and the descriptor factories) used by all 11 QAOA
processors. No NiFi/JVM -- conftest.py stubs nifiapi.*.
"""

import json

import numpy as np
import pytest

import qaoa_contract as qc
from conftest import MockFlowFile
from pauli_dsl import parse_pauli_sum, terms_to_wire
from qaoa_reference import H_3BODY, energies as ref_energies, terms_of


# ---------------------------------------------------------------------------
# parse_angles
# ---------------------------------------------------------------------------


class TestParseAngles:
    @pytest.mark.parametrize("value", ["[0.1, 0.2]", "0.1,0.2", " 0.1 , 0.2 "])
    def test_accepts_json_and_comma_forms(self, value):
        assert qc.parse_angles(value, 2, "Betas") == [0.1, 0.2]

    @pytest.mark.parametrize(
        "value",
        ["0.1", "0.1,0.2,0.3", "[NaN, 1.0]", "[Infinity, 1.0]", "abc", "[true, 1]", ""],
    )
    def test_rejects_bad_values(self, value):
        with pytest.raises(ValueError):
            qc.parse_angles(value, 2, "Betas")

    def test_round_trips_exactly(self):
        value = "0.123456789012345,-0.987654321098765"
        result = qc.parse_angles(value, 2, "Betas")
        assert json.loads(json.dumps(result)) == result


# ---------------------------------------------------------------------------
# parse_layers / parse_seed / parse_choice
# ---------------------------------------------------------------------------


class TestParseLayers:
    @pytest.mark.parametrize("value,expected", [("1", 1), ("16", 16)])
    def test_accepts(self, value, expected):
        assert qc.parse_layers(value) == expected

    @pytest.mark.parametrize("value", ["0", "17", "two", "1.5", True])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            qc.parse_layers(value)


class TestParseSeed:
    @pytest.mark.parametrize("value", [None, ""])
    def test_none_or_blank_is_none(self, value):
        assert qc.parse_seed(value) is None

    def test_accepts_boundaries(self):
        assert qc.parse_seed("0") == 0
        assert qc.parse_seed("4294967295") == 4294967295

    @pytest.mark.parametrize("value", ["-1", "4294967296", "x"])
    def test_rejects(self, value):
        with pytest.raises(ValueError):
            qc.parse_seed(value)


class TestParseChoice:
    def test_accepts_member(self):
        assert qc.parse_choice("COBYLA", ["COBYLA", "POWELL"], "Optimizer") == "COBYLA"

    def test_rejects_non_member(self):
        with pytest.raises(ValueError):
            qc.parse_choice("BOGUS", ["COBYLA", "POWELL"], "Optimizer")


# ---------------------------------------------------------------------------
# parse_initial_parameters
# ---------------------------------------------------------------------------


class TestParseInitialParameters:
    def test_random_is_none(self):
        assert qc.parse_initial_parameters("random", 2) is None

    def test_zeros_is_2p_zeros(self):
        assert qc.parse_initial_parameters("zeros", 2) == [0.0, 0.0, 0.0, 0.0]

    def test_explicit_list_ok(self):
        assert qc.parse_initial_parameters("[0.1,0.2,0.3,0.4]", 2) == [
            0.1,
            0.2,
            0.3,
            0.4,
        ]

    def test_wrong_length_message_contains_2p(self):
        with pytest.raises(ValueError, match="2p"):
            qc.parse_initial_parameters("[0.1,0.2]", 2)


# ---------------------------------------------------------------------------
# validate_wire
# ---------------------------------------------------------------------------


def _good_wire(n=2):
    terms, num_qubits = parse_pauli_sum("Z0 - Z1", n)
    return terms_to_wire(terms, num_qubits)


class TestValidateWire:
    def test_accepts_valid_wire(self):
        terms, n = qc.validate_wire(json.dumps(_good_wire()))
        assert n == 2

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError):
            qc.validate_wire(json.dumps([1, 2, 3]))

    def test_rejects_bad_json(self):
        with pytest.raises(ValueError):
            qc.validate_wire("{not json")

    @pytest.mark.parametrize("n", [0, 17])
    def test_rejects_bad_num_qubits(self, n):
        w = _good_wire()
        w["num_qubits"] = n
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_empty_terms(self):
        w = _good_wire()
        w["terms"] = []
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_too_many_terms(self):
        w = _good_wire()
        w["terms"] = [["II", 1.0, 0.0]] * 4097
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_term_not_length_3(self):
        w = _good_wire()
        w["terms"] = [["ZI", 1.0]]
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_label_length_mismatch(self):
        w = _good_wire()
        w["terms"] = [["Z", 1.0, 0.0]]
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_bad_character(self):
        w = _good_wire()
        w["terms"] = [["ZA", 1.0, 0.0]]
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_nan_coefficient(self):
        w = _good_wire()
        w["terms"] = [["ZI", float("nan"), 0.0]]
        with pytest.raises(ValueError):
            qc.validate_wire(w)

    def test_rejects_imaginary_coefficient(self):
        w = _good_wire()
        w["terms"] = [["ZI", 1.0, 0.5]]
        with pytest.raises(ValueError):
            qc.validate_wire(w)


# ---------------------------------------------------------------------------
# require_qaoa_cost
# ---------------------------------------------------------------------------


class TestRequireQaoaCost:
    def test_rejects_nondiagonal_with_diagonal_message(self):
        terms, _n = parse_pauli_sum("X0 X1", 0)
        with pytest.raises(ValueError, match="diagonal"):
            qc.require_qaoa_cost(terms)

    def test_rejects_identity_only_with_no_z_terms_message(self):
        terms, _n = parse_pauli_sum("0.5", 1)
        with pytest.raises(ValueError, match="no Z terms"):
            qc.require_qaoa_cost(terms)


# ---------------------------------------------------------------------------
# read_cost_hamiltonian
# ---------------------------------------------------------------------------


class TestReadCostHamiltonian:
    def test_content_path_from_hamiltonian_processor(self):
        from QiskitHamiltonian import QiskitHamiltonian
        from conftest import MockContext

        res = QiskitHamiltonian().transform(
            MockContext(**{"Hamiltonian": "Z0 - Z1", "Num Qubits": "0"}), MockFlowFile()
        )
        assert res.relationship == "success"
        ff = MockFlowFile(content=res.contents, attributes=res.attributes)
        terms, n, raw = qc.read_cost_hamiltonian(ff)
        assert n == 2
        assert raw == res.contents.decode("utf-8")
        assert len(terms) == 2

    def test_rebuild_path_uses_hamiltonian_json_attribute(self):
        raw = json.dumps(_good_wire())
        ff = MockFlowFile(
            content=b"OPENQASM 2.0;\n",
            attributes={"circuit.format": "qasm2", "hamiltonian.json": raw},
        )
        terms, n, got_raw = qc.read_cost_hamiltonian(ff)
        assert n == 2
        assert got_raw == raw

    def test_circuit_without_hamiltonian_json_fails(self):
        ff = MockFlowFile(content=b"x", attributes={"circuit.format": "qasm2"})
        with pytest.raises(ValueError, match="hamiltonian.json"):
            qc.read_cost_hamiltonian(ff)

    def test_missing_hamiltonian_format_fails(self):
        with pytest.raises(ValueError, match="no Hamiltonian"):
            qc.read_cost_hamiltonian(MockFlowFile())


# ---------------------------------------------------------------------------
# energy_vector
# ---------------------------------------------------------------------------


class TestEnergyVector:
    def test_nonpalindromic_z0_minus_z1(self):
        terms, n = parse_pauli_sum("Z0 - Z1", 0)
        e = qc.energy_vector(terms, n)
        assert e[int("10", 2)] == pytest.approx(-2.0)
        assert e[int("01", 2)] == pytest.approx(2.0)

    def test_matches_independent_reference_on_h_3body(self):
        terms, n = terms_of(H_3BODY)
        np.testing.assert_allclose(qc.energy_vector(terms, n), ref_energies(terms, n))


# ---------------------------------------------------------------------------
# qasm2_profile
# ---------------------------------------------------------------------------

QISKIT_QASM = (
    "OPENQASM 2.0;\n"
    'include "qelib1.inc";\n'
    "qreg q[2];\n"
    "h q[0];\n"
    "cx q[0],q[1];\n"
    "rz(0.5) q[1];\n"
)

CIRQ_QASM = (
    "OPENQASM 2.0;\n"
    'include "qelib1.inc";\n'
    "\n"
    "// Qubits: [q0, q1]\n"
    "qreg q[2];\n"
    "\n"
    "\n"
    "h q[0];\n"
    "cx q[0],q[1];\n"
    "rz(pi*0.1591549431) q[1];\n"
)

PENNYLANE_QASM = (
    "OPENQASM 2.0;\n"
    'include "qelib1.inc";\n'
    "qreg q[2];\n"
    "h q[0];\n"
    "cx q[0],q[1];\n"
    "rz(0.3) q[1];\n"
)

QRISP_QASM = (
    "OPENQASM 2.0;\n"
    'include "qelib1.inc";\n'
    "qreg q[3];\n"
    "h q[0];\n"
    "h q[1];\n"
    "h q[2];\n"
    "cx q[0],q[1];\n"
    "rz(0.4) q[1];\n"
    "cx q[0],q[1];\n"
    "rx(0.6) q[0];\n"
    "rx(0.6) q[1];\n"
    "rx(0.6) q[2];\n"
)

PYQUIL_QASM = (
    "OPENQASM 2.0;\n"
    'include "qelib1.inc";\n'
    "qreg q[2];\n"
    "h q[0];\n"
    "h q[1];\n"
    "cx q[0],q[1];\n"
    "rz(0.7853981633974483) q[1];\n"
    "cx q[0],q[1];\n"
    "rx(0.5) q[0];\n"
    "rx(0.5) q[1];\n"
)

BASE = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[2];\n'


class TestQasm2Profile:
    @pytest.mark.parametrize(
        "source", [QISKIT_QASM, CIRQ_QASM, PENNYLANE_QASM, QRISP_QASM, PYQUIL_QASM]
    )
    def test_accepts_each_dialect_and_matches_qiskit_metrics(self, source):
        from qiskit import qasm2 as qiskit_qasm2

        profile = qc.qasm2_profile(source)
        parsed = qiskit_qasm2.loads(
            source, custom_instructions=qiskit_qasm2.LEGACY_CUSTOM_INSTRUCTIONS
        )
        assert profile["num_qubits"] == parsed.num_qubits
        assert profile["depth"] == parsed.depth()
        assert profile["gate_count"] == parsed.size()

    @pytest.mark.parametrize(
        "bad,needle",
        [
            (BASE + "qreg r[1];\nh q[0];\n", "exactly one qreg"),
            (BASE + "h q[0];\ncreg c[2];\nmeasure q[0] -> c[0];\n", "unsupported"),
            (BASE + "h q[0];\ncreg c[2];\n", "unsupported"),
            (
                'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[1];\ngphase(pi);\n',
                "unsupported",
            ),
            (BASE + "sx q[0];\n", "unsupported"),
            (BASE + "rzz(0.5) q[0],q[1];\n", "unsupported"),
            (BASE + "gate foo a { h a; }\n", "unsupported"),
            (BASE + "h q[5];\n", "out of range"),
            (BASE + "h r[0];\n", "undeclared register"),
        ],
    )
    def test_rejects(self, bad, needle):
        with pytest.raises(ValueError, match=needle):
            qc.qasm2_profile(bad)


# ---------------------------------------------------------------------------
# builder_attributes blanks EVALUATOR_KEYS
# ---------------------------------------------------------------------------


def test_builder_attributes_blanks_evaluator_keys():
    attrs = qc.builder_attributes("raw", 2, 1, [0.1], [0.2])
    for key in qc.EVALUATOR_KEYS:
        assert attrs[key] == ""
    assert attrs["hamiltonian.json"] == "raw"
    assert attrs["qaoa.betas"] == "[0.1]"
    assert attrs["qaoa.gammas"] == "[0.2]"
    assert attrs["qaoa.mixer_type"] == "RX"
    assert attrs["qaoa.error"] == ""


# ---------------------------------------------------------------------------
# Descriptor factories
# ---------------------------------------------------------------------------


class TestSolverDescriptors:
    def test_names_order_and_defaults(self):
        descs = qc.solver_descriptors(["COBYLA", "POWELL"])
        names = [d.name for d in descs]
        assert names == [
            "Layers",
            "Optimizer",
            "Max Iterations",
            "Initial Parameters",
            "Random Seed",
        ]
        by_name = {d.name: d for d in descs}
        assert by_name["Layers"].default_value == "2"
        assert by_name["Layers"].required is True
        assert by_name["Optimizer"].default_value == "COBYLA"
        assert by_name["Optimizer"].allowable_values == ["COBYLA", "POWELL"]
        assert by_name["Max Iterations"].default_value == "100"
        assert by_name["Initial Parameters"].default_value == "random"
        rs = by_name["Random Seed"]
        assert rs.required is False
        assert rs.default_value is None
        assert rs.expression_language_scope == "FLOWFILE_ATTRIBUTES"
        assert by_name["Layers"].expression_language_scope == "FLOWFILE_ATTRIBUTES"
        assert (
            by_name["Max Iterations"].expression_language_scope == "FLOWFILE_ATTRIBUTES"
        )
        assert (
            by_name["Initial Parameters"].expression_language_scope
            == "FLOWFILE_ATTRIBUTES"
        )
        assert by_name["Optimizer"].expression_language_scope == "NONE"

    def test_with_mixer_inserts_at_index_1(self):
        descs = qc.solver_descriptors(["COBYLA"], with_mixer=True)
        names = [d.name for d in descs]
        assert names == [
            "Layers",
            "Mixer Type",
            "Optimizer",
            "Max Iterations",
            "Initial Parameters",
            "Random Seed",
        ]
        mixer = {d.name: d for d in descs}["Mixer Type"]
        assert mixer.default_value == "RX"
        assert mixer.allowable_values == ["RX", "XY"]


class TestBuilderDescriptors:
    def test_names_order_and_defaults(self):
        descs = qc.builder_descriptors()
        names = [d.name for d in descs]
        assert names == ["Layers", "Betas", "Gammas"]
        by_name = {d.name: d for d in descs}
        assert by_name["Layers"].default_value == "1"
        assert by_name["Betas"].default_value == qc.DEFAULT_BETAS
        assert by_name["Gammas"].default_value == qc.DEFAULT_GAMMAS
        for name in names:
            assert by_name[name].expression_language_scope == "FLOWFILE_ATTRIBUTES"

    def test_with_mixer_appends_last(self):
        descs = qc.builder_descriptors(with_mixer=True)
        names = [d.name for d in descs]
        assert names == ["Layers", "Betas", "Gammas", "Mixer Type"]
        assert descs[-1].default_value == "RX"
        assert descs[-1].allowable_values == ["RX", "XY"]
