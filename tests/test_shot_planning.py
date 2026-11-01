"""required_shots lives in the shot_planning helper and is shared, not copied.

The batch submitters used to import it from the QuantumSuccessProbabilityOracle
processor module. A processor must not import another processor, because the
Docker build copies only the selected processors plus their helper modules.
"""

import pytest

import shot_planning
import QuantumIBMBatchSubmitter
import QuantumInspireBatchSubmitter
import QuantumIQMBatchSubmitter
import QuantumSuccessProbabilityOracle


@pytest.mark.parametrize("module", [
    QuantumSuccessProbabilityOracle,
    QuantumIBMBatchSubmitter,
    QuantumIQMBatchSubmitter,
    QuantumInspireBatchSubmitter,
])
def test_processors_use_the_shared_function(module):
    assert module.required_shots is shot_planning.required_shots


@pytest.mark.parametrize("p_ref,min_diff,expected", [
    (0.90, 0.10, 201), (0.80, 0.15, 140), (0.60, 0.15, 174),
])
def test_expected_values(p_ref, min_diff, expected):
    assert shot_planning.required_shots(p_ref, min_diff, 0.05, 0.8) == expected
    assert shot_planning.required_shots(p_ref, min_diff) == expected


def test_oracle_still_exposes_norm_ppf():
    assert QuantumSuccessProbabilityOracle._norm_ppf is shot_planning._norm_ppf


def test_rejects_zero_min_diff():
    with pytest.raises(ValueError):
        shot_planning.required_shots(0.9, 0.0)
