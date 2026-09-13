"""Tests for the shared, framework-neutral Pauli-sum parser (pauli_dsl).

This is the single Hamiltonian syntax used across all framework Hamiltonian
processors, so its behaviour is pinned here independently of any framework.
"""

import pytest

from pauli_dsl import parse_pauli_sum, terms_to_wire, wire_to_terms, PauliDSLError


class TestCompactIndexed:

    def test_canonical(self):
        terms, n = parse_pauli_sum("Z0 + Z1 + 0.5 X0 X1")
        assert n == 2
        assert terms == [("Z", [0], 1.0), ("Z", [1], 1.0), ("XX", [0, 1], 0.5)]

    def test_spaced_back_compat_is_identical(self):
        assert parse_pauli_sum("Z 0 + Z 1 + 0.5 X 0 X 1") == parse_pauli_sum("Z0 + Z1 + 0.5 X0 X1")

    def test_minus_separator_and_identity_term(self):
        terms, n = parse_pauli_sum("0.5 X0 X1 - 1.5 Z2 + 0.25")
        assert n == 3
        assert terms == [("XX", [0, 1], 0.5), ("Z", [2], -1.5), ("", [], 0.25)]

    def test_case_insensitive_and_multidigit_index(self):
        terms, n = parse_pauli_sum("z0 + x10")
        assert n == 11
        assert terms == [("Z", [0], 1.0), ("X", [10], 1.0)]

    def test_num_qubits_hint_pads(self):
        _, n = parse_pauli_sum("Z0", num_qubits_hint=4)
        assert n == 4

    def test_identity_factor_dropped_but_pins_size(self):
        terms, n = parse_pauli_sum("I3")
        assert terms == [("", [], 1.0)]
        assert n == 4


class TestJson:

    def test_list_form(self):
        terms, n = parse_pauli_sum('[{"pauli":"ZZ","qubits":[0,1],"coeff":0.5},'
                                   ' {"pauli":"Z","qubits":[0]}]')
        assert n == 2
        assert terms == [("ZZ", [0, 1], 0.5), ("Z", [0], 1.0)]

    def test_object_form_with_num_qubits(self):
        terms, n = parse_pauli_sum('{"num_qubits":4,"terms":[{"pauli":"X","qubits":[3],"coeff":2.0}]}')
        assert n == 4
        assert terms == [("X", [3], 2.0)]

    def test_text_and_json_agree(self):
        a, na = parse_pauli_sum("Z0 + 0.5 X0 X1")
        b, nb = parse_pauli_sum('[{"pauli":"Z","qubits":[0]},{"pauli":"XX","qubits":[0,1],"coeff":0.5}]')
        assert (a, na) == (b, nb)


class TestErrors:

    @pytest.mark.parametrize("bad", ["", "   ", "Q0", "Z", "1e-3 Z0", "+ "])
    def test_malformed_raises(self, bad):
        with pytest.raises(PauliDSLError):
            parse_pauli_sum(bad)

    def test_index_out_of_range(self):
        with pytest.raises(PauliDSLError):
            parse_pauli_sum("Z5", num_qubits_hint=2)

    def test_json_pauli_qubits_mismatch(self):
        with pytest.raises(PauliDSLError):
            parse_pauli_sum('[{"pauli":"ZZ","qubits":[0]}]')


class TestWireFormat:

    def test_terms_to_wire_dense_labels_msb_first(self):
        terms, n = parse_pauli_sum("Z0 + Z1 + 0.5 X0 X1")
        wire = terms_to_wire(terms, n)
        assert wire == {"num_qubits": 2,
                        "terms": [["IZ", 1.0, 0.0], ["ZI", 1.0, 0.0], ["XX", 0.5, 0.0]]}

    def test_round_trip_text_to_wire_to_terms(self):
        terms, n = parse_pauli_sum("0.5 X0 X1 - 1.5 Z2")
        back, n2 = wire_to_terms(terms_to_wire(terms, n))
        assert n2 == n
        # same operator content (order/qubit-list orientation preserved by index)
        assert sorted((p, tuple(sorted(i)), c) for p, i, c in back) == \
               sorted((p, tuple(sorted(i)), c) for p, i, c in terms)

    def test_wire_to_terms_accepts_json_string(self):
        terms, n = wire_to_terms('{"num_qubits": 2, "terms": [["IZ", 1.0, 0.0]]}')
        assert (terms, n) == ([("Z", [0], 1.0)], 2)
