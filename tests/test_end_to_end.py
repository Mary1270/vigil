"""
Full lifecycle test, mirroring exactly the live Studio verification
sequence recorded in LESSONS_LEARNED.md and README.md: the same facts,
the same tier transitions, and the same final scores, run here offline
against the real cross-contract logic via the stub.
"""
import json
from unittest.mock import patch

from _bootstrap import make_wired, set_caller, CLAIMANT_ADDRESS
from genlayer import gl


def _non_comparative(verdict):
    return json.dumps({"reasoning": "step-by-step analysis", "final_verdict": verdict})


def _comparative(verdict):
    return json.dumps({
        "reading_one": verdict, "reading_two": verdict, "final_verdict": verdict,
    })


def _three_framing(verdict):
    return json.dumps({
        "framing_a": verdict, "framing_b": verdict, "framing_c": verdict,
        "final_verdict": verdict,
    })


def test_full_lifecycle_matches_live_studio_verification():
    ledger, verifier, panel = make_wired()
    set_caller(CLAIMANT_ADDRESS)

    # Step 1 (live: claim 0) -- no history -> low tier -> CONFIRMED -> 500 -> 530
    with patch.object(gl.nondet, "exec_prompt", return_value=_non_comparative("CONFIRMED")):
        claim0 = verifier.submit_claim(
            "Paris is the capital of France.", "https://example.test/paris"
        )
    record0 = json.loads(verifier.get_claim(claim0))
    assert record0["tier"] == "low_reputation_or_unknown"
    assert record0["verdict"] == "CONFIRMED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530

    # Step 2 (live: claim 1) -- reputation 530 -> medium tier -> CONFIRMED -> 530 -> 550
    with patch.object(gl.nondet, "exec_prompt", return_value=_comparative("CONFIRMED")):
        claim1 = verifier.submit_claim(
            "Tokyo is the capital of Japan.", "https://example.test/tokyo"
        )
    record1 = json.loads(verifier.get_claim(claim1))
    assert record1["tier"] == "medium_reputation"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 550

    # Step 3 (live: claim 2) -- false fact -> medium tier -> REJECTED -> 550 -> 530
    with patch.object(gl.nondet, "exec_prompt", return_value=_comparative("REJECTED")):
        claim2 = verifier.submit_claim(
            "Tokyo is the capital of France.", "https://example.test/tokyo"
        )
    record2 = json.loads(verifier.get_claim(claim2))
    assert record2["verdict"] == "REJECTED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530

    # Step 4 (live: dispute 0) -- panel denies the dispute -> extra penalty -> 530 -> 520
    with patch.object(gl.nondet, "exec_prompt", return_value=_three_framing("REJECTED")):
        verifier.request_dispute(claim2)
    dispute0 = json.loads(panel.get_dispute(0))
    assert dispute0["overturned"] is False
    assert ledger.get_score(CLAIMANT_ADDRESS) == 520

    # This matches LESSONS_LEARNED.md's recorded live Studio sequence
    # exactly: 500 -> 530 -> 550 -> 530 -> 520.
