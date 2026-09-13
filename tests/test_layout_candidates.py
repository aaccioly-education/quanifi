import hashlib
import json

import pytest

from tools.freeze_layout_candidates import freeze
from tools.add_generation2_canvas import load_qualification_candidate


CAMPAIGN = "generation2-semiadder-balanced-independent-v3"
DESIGN = "arithmetic-cdkm-qft-semiadder-balanced-independent-v3"


def write(path, body):
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def fixtures(tmp_path):
    candidates = []
    for rank, layout in enumerate(([16, 21, 22, 20, 41, 23, 36],
                                   [1, 2, 3, 4, 5, 6, 7],
                                   [7, 6, 5, 4, 3, 2, 1],
                                   [8, 9, 10, 11, 12, 13, 14]), 1):
        candidates.append({"attempt": rank, "search_rank": rank,
                           "layout": layout, "objective": [rank],
                           "representatives": [], "source": {},
                           "physical_qubits": sorted(layout)})
    preflight = write(tmp_path / "preflight.json", {
        "providers": {"ibm": {
            "device": "ibm_kingston",
            "layout_evidence": {
                "retry_sequence_strategy": "ranked-distinct-physical-sets-v1",
                "candidate_sequence_sha256": "source-sequence",
                "candidate_sequence": candidates,
                "backend_provenance": {"snapshot_sha256": "snapshot"},
            },
        }},
    })
    attempt = write(tmp_path / "attempt-1.json", {
        "vendor": "ibm", "attempt": 1, "status": "rejected",
        "layout": [16, 21, 22, 20, 41, 23, 36],
        "provider_job_id": "job-1", "reason": "cdkm 2/7",
    })
    return preflight, attempt


def test_freeze_preserves_rejection_and_skips_failed_or_permuted_sets(tmp_path):
    preflight, attempt = fixtures(tmp_path)
    output = tmp_path / "ibm.json"
    body = freeze(preflight, attempt, output, "ibm", CAMPAIGN, DESIGN)
    assert [row["status"] for row in body["attempts"]] == [
        "rejected", "frozen-not-run", "frozen-not-run"]
    assert [row["layout"] for row in body["attempts"]] == [
        [16, 21, 22, 20, 41, 23, 36],
        [1, 2, 3, 4, 5, 6, 7],
        [8, 9, 10, 11, 12, 13, 14],
    ]
    expected = hashlib.sha256(json.dumps(
        body["attempts"], sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    assert body["sequence_sha256"] == expected


def test_canvas_loader_selects_only_the_requested_frozen_attempt(tmp_path):
    preflight, attempt = fixtures(tmp_path)
    output = tmp_path / "ibm.json"
    body = freeze(preflight, attempt, output, "ibm", CAMPAIGN, DESIGN)
    selected = load_qualification_candidate(output, "ibm", 2, CAMPAIGN)
    assert selected == {
        "attempt": 2,
        "layout": "1,2,3,4,5,6,7",
        "sequence_sha256": body["sequence_sha256"],
        "selection_snapshot_sha256": "snapshot",
    }
    with pytest.raises(SystemExit, match="rejected"):
        load_qualification_candidate(output, "ibm", 1, CAMPAIGN)


def test_canvas_loader_requires_a_file_for_a_retry():
    with pytest.raises(SystemExit, match="needs a candidate file"):
        load_qualification_candidate("", "ibm", 2, CAMPAIGN)
