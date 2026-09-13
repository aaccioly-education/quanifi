"""Layout pinning on the IQM lane.

Why it exists: without a pinned layout every job searches its own, so two jobs
of the same batch differ by qubit-assignment luck as much as by anything else.
Measured on IBM on 2026-08-23 -- 30 byte-identical circuits ran in two jobs
twelve minutes apart, 13 of them moved by more than the 0.05 kill threshold,
and one implementation's control success went 0.78 -> 0.16 purely because the
searched layout changed. IQM runs are planned, so the same control is needed
there before any cross-job claim is made.
"""
import pytest

from QuantumIQMBatchSubmitter import (
    QuantumIQMBatchSubmitter, _parse_layout as iqm_parse_layout,
)
from QuantumIBMBatchSubmitter import _parse_layout as ibm_parse_layout


class TestParserParity:
    """The parser is duplicated across the two submitters, on purpose.

    NiFi loads each processor module by file path, so importing a sibling
    module at module scope raises ModuleNotFoundError and the processor is
    skipped silently -- what happened to QuantumMutator on 2026-08-23. The cost
    of that safety is two copies, so the copies are pinned to agree.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("", None),
        (None, None),
        ("   ", None),
        ("122,144,124,142", [122, 144, 124, 142]),
        (" 5, 6 ,7 ", [5, 6, 7]),
        ("0", [0]),
    ])
    def test_both_parsers_agree(self, raw, expected):
        assert iqm_parse_layout(raw) == expected
        assert ibm_parse_layout(raw) == expected

    def test_both_reject_non_integers(self):
        for parser in (iqm_parse_layout, ibm_parse_layout):
            with pytest.raises(ValueError):
                parser("122,oops,124")

    def test_order_is_preserved_not_sorted(self):
        """The list IS the qubit->physical mapping; sorting would rewire it."""
        assert iqm_parse_layout("9,3,7") == [9, 3, 7]


def _fake_backend():
    """A 20-qubit stand-in for garnet.

    GenericBackendV2 rather than a hand-rolled stub because the pinned path
    actually transpiles a probe circuit to count two-qubit gates, and that
    needs a real `target` with a coupling map -- a bare object only gets as far
    as the validation checks.
    """
    from qiskit.providers.fake_provider import GenericBackendV2
    return GenericBackendV2(num_qubits=20, seed=11)


@pytest.fixture
def qasms():
    from test_arithmetic_equivalence import build
    return [build("qiskit/cdkm", 3, 1, 2).contents.decode()]


@pytest.fixture
def padded_width(qasms):
    from batch_prep import circuits_from_qasm, pad_batch
    _, _, width = pad_batch(circuits_from_qasm(qasms))
    return width


class TestFixedLayoutValidation:
    """A mis-specified pin must fail loudly here, not silently on hardware."""

    def _prepare(self, layout, qasms, monkeypatch):
        monkeypatch.setattr(QuantumIQMBatchSubmitter, "backend_for",
                            lambda self, *a: _fake_backend())
        return QuantumIQMBatchSubmitter().prepare(
            qasms, 512, "garnet", "https://example.invalid", "tok", 1, 0,
            fixed_layout=layout)

    def test_wrong_width_is_rejected(self, qasms, monkeypatch):
        with pytest.raises(RuntimeError, match="Fixed Layout names"):
            self._prepare([1, 2, 3], qasms, monkeypatch)

    def test_repeated_qubit_is_rejected(self, qasms, padded_width, monkeypatch):
        with pytest.raises(RuntimeError, match="repeats a physical qubit"):
            self._prepare([7] * padded_width, qasms, monkeypatch)

    def test_qubit_beyond_device_is_rejected(self, qasms, padded_width, monkeypatch):
        beyond = list(range(100, 100 + padded_width))
        with pytest.raises(RuntimeError, match="names qubit"):
            self._prepare(beyond, qasms, monkeypatch)

    def test_a_valid_layout_is_used_verbatim(self, qasms, padded_width, monkeypatch):
        layout = list(range(padded_width))
        plan = self._prepare(layout, qasms, monkeypatch)
        assert plan["layout"] == layout
        assert plan["padded_width"] == padded_width

    def test_blank_layout_still_searches(self, qasms, monkeypatch):
        """Blank must not become [] and then be treated as a pin."""
        monkeypatch.setattr(QuantumIQMBatchSubmitter, "backend_for",
                            lambda self, *a: _fake_backend())
        plan = QuantumIQMBatchSubmitter().prepare(
            qasms, 512, "garnet", "https://example.invalid", "tok", 1, 0,
            fixed_layout=iqm_parse_layout(""))
        assert len(plan["layout"]) == plan["padded_width"]

    def test_two_qubit_count_is_reported_when_pinned(self, qasms, padded_width,
                                                     monkeypatch):
        """The metric must mean the same thing pinned or searched."""
        plan = self._prepare(list(range(padded_width)), qasms, monkeypatch)
        assert isinstance(plan["two_qubit_gates"], int)


class TestProperty:
    def test_property_exists_and_defaults_to_searching(self):
        descriptors = {d.name: d for d in
                       QuantumIQMBatchSubmitter().getPropertyDescriptors()}
        assert "Fixed Layout" in descriptors
        assert descriptors["Fixed Layout"].default_value in ("", None)


# --- which circuit the layout is searched on -------------------------------

def test_layout_is_probed_with_the_most_demanding_circuit():
    """Padding adds width, not gates, so `circuits[0]` is the wrong probe.

    A 6-qubit adder widened to 9 still entangles only 6, and the transpiler puts
    the three idle lines wherever it likes. `outadder` uses all nine, so it
    inherits that arbitrary placement for the qubits it depends on -- 88
    two-qubit gates on garnet instead of 58, which is what failed it at IQM
    calibration on 2026-08-29 while it stayed perfect on IBM's 156 qubits.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nifi_extensions"))
    import batch_prep
    from qiskit import QuantumCircuit

    idle_heavy = QuantumCircuit(9)          # wide, but only two qubits entangled
    idle_heavy.cx(0, 1)
    busy = QuantumCircuit(9)                # every qubit carries a 2q gate
    for control in range(8):
        busy.cx(control, control + 1)

    assert batch_prep.layout_probe([idle_heavy, busy]) is busy
    assert batch_prep.layout_probe([busy, idle_heavy]) is busy


def test_both_submitters_balance_all_representative_circuits():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "nifi_extensions"
    for name in ("QuantumIBMBatchSubmitter", "QuantumIQMBatchSubmitter"):
        source = (root / (name + ".py")).read_text()
        assert "balanced_layout_for(" in source, name
        assert "layout_for(circuits[0]" not in source, name
