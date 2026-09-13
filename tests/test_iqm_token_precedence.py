"""A configured token must win over a stale one left in the process environment."""
import os

from QuantumIQMBatchSubmitter import QuantumIQMBatchSubmitter


class _Backend:
    def get_backend(self):
        return "backend"


def test_a_real_token_overrides_a_blank_one_left_by_an_earlier_run(monkeypatch):
    # Simulates the poisoned state: an earlier invocation with no token set
    # IQM_TOKEN="" in this long-lived process.
    monkeypatch.setenv("IQM_TOKEN", "")
    monkeypatch.setattr(
        "iqm.qiskit_iqm.IQMProvider", lambda *a, **k: _Backend(), raising=False)
    QuantumIQMBatchSubmitter().backend_for("https://x", "garnet", "REALTOKEN")
    assert os.environ["IQM_TOKEN"] == "REALTOKEN"


def test_no_token_leaves_the_environment_alone(monkeypatch):
    monkeypatch.setenv("IQM_TOKEN", "PREEXISTING")
    monkeypatch.setattr(
        "iqm.qiskit_iqm.IQMProvider", lambda *a, **k: _Backend(), raising=False)
    QuantumIQMBatchSubmitter().backend_for("https://x", "garnet", "")
    assert os.environ["IQM_TOKEN"] == "PREEXISTING"


# --- the environment fallback ----------------------------------------------

def test_resolve_secret_prefers_the_property_then_the_environment(monkeypatch):
    """NiFi returns "" for Expression Language on a *sensitive* property.

    The canvas deliberately leaves `API Token` as `${IBM_QUANTUM_TOKEN}` so no
    credential is written into flow.json.gz, and `just nifi-start` puts the real
    value in NiFi's process environment. Because sensitive properties do not
    evaluate EL, that property arrives blank -- so blank must mean "look in the
    environment", not "fail". On 2026-08-28 IQM had this fallback and worked
    while IBM did not, and every IBM preflight failed with "Unable to find
    account", which is what an empty token looks like to qiskit-ibm-runtime.
    """
    import batch_prep

    monkeypatch.setenv("TEST_PROVIDER_TOKEN", "from-environment")
    assert batch_prep.resolve_secret("configured", "TEST_PROVIDER_TOKEN") == "configured"
    assert batch_prep.resolve_secret("", "TEST_PROVIDER_TOKEN") == "from-environment"
    assert batch_prep.resolve_secret("   ", "TEST_PROVIDER_TOKEN") == "from-environment"
    monkeypatch.delenv("TEST_PROVIDER_TOKEN")
    assert batch_prep.resolve_secret("", "TEST_PROVIDER_TOKEN") == ""


def test_every_provider_processor_uses_the_fallback():
    """A processor that reads its token directly reintroduces the bug."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "nifi_extensions"
    for name in ("QuantumIBMBatchSubmitter", "QuantumIQMBatchSubmitter",
                 "QuantumIBMBatchPoller", "QuantumIQMBatchPoller"):
        source = (root / (name + ".py")).read_text()
        # Three shapes, all equivalent: the submitters put their own directory
        # on sys.path and import batch_prep.resolve_secret; the pollers inline
        # `_secret` because NiFi does not do that for them; the IQM submitter
        # writes the token into os.environ and lets an unset property fall
        # through to whatever is already there.
        assert ("resolve_secret" in source or "_secret(" in source
                or "os.environ[\"IQM_TOKEN\"]" in source), name
