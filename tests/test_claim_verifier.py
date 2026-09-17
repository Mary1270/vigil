"""
Tests for ClaimVerifier, exercising the real reputation feedback loop
via genuine (in-process, synchronous) cross-contract calls to
ReputationLedger through the stub's `get_contract_at` registry -- the
exact interaction genlayer-test's Direct Mode was confirmed NOT to
support (see LESSONS_LEARNED.md).
"""
import json
from unittest.mock import patch

import pytest

from _bootstrap import (
    make_wired, set_caller,
    CLAIMANT_ADDRESS, STRANGER_ADDRESS, VERIFIER_ADDRESS,
)
from genlayer import gl


def _confirmed(reasoning="supports the claim"):
    return json.dumps({"reasoning": reasoning, "final_verdict": "CONFIRMED"})


def _rejected(reasoning="does not support the claim"):
    return json.dumps({"reasoning": reasoning, "final_verdict": "REJECTED"})


def _comparative(verdict):
    return json.dumps({
        "reading_one": verdict, "reading_two": verdict, "final_verdict": verdict,
    })


def test_first_claim_from_unknown_address_uses_low_tier():
    ledger, verifier, _ = make_wired()
    set_caller(CLAIMANT_ADDRESS)

    with patch.object(gl.nondet, "exec_prompt", return_value=_confirmed()):
        claim_id = verifier.submit_claim(
            "Paris is the capital of France.", "https://example.test/paris"
        )

    assert claim_id == 0
    record = json.loads(verifier.get_claim(0))
    assert record["tier"] == "low_reputation_or_unknown"
    assert record["verdict"] == "CONFIRMED"
    assert record["reputation_at_submission"] == 500
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530
    assert ledger.has_history(CLAIMANT_ADDRESS) is True


def test_second_confirmed_claim_moves_to_medium_tier():
    ledger, verifier, _ = make_wired()
    set_caller(CLAIMANT_ADDRESS)

    with patch.object(gl.nondet, "exec_prompt", return_value=_confirmed()):
        verifier.submit_claim("Paris is the capital of France.", "https://example.test/paris")
        # reputation now 530 -- above low_threshold=300, below high_threshold=700

    with patch.object(gl.nondet, "exec_prompt", return_value=_comparative("CONFIRMED")):
        claim_id = verifier.submit_claim(
            "Tokyo is the capital of Japan.", "https://example.test/tokyo"
        )

    record = json.loads(verifier.get_claim(claim_id))
    assert record["tier"] == "medium_reputation"
    assert record["reputation_at_submission"] == 530
    assert ledger.get_score(CLAIMANT_ADDRESS) == 550


def test_rejected_claim_lowers_reputation():
    ledger, verifier, _ = make_wired()
    set_caller(CLAIMANT_ADDRESS)

    with patch.object(gl.nondet, "exec_prompt", return_value=_rejected()):
        claim_id = verifier.submit_claim(
            "The moon is made of cheese.", "https://example.test/moon"
        )

    record = json.loads(verifier.get_claim(claim_id))
    assert record["verdict"] == "REJECTED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 470


def test_high_reputation_uses_strict_eq_web_fetch():
    ledger, verifier, _ = make_wired()

    # Fast-forward CLAIMANT_ADDRESS's reputation to the high tier by
    # calling the ledger directly, authorized as the verifier -- exactly
    # what submit_claim itself would eventually do, just without
    # needing dozens of real submit_claim calls to get there.
    set_caller(VERIFIER_ADDRESS)  # only the registered verifier may call apply_delta
    ledger.apply_delta(CLAIMANT_ADDRESS, 250, True)  # 500 -> 750
    assert ledger.get_score(CLAIMANT_ADDRESS) == 750

    set_caller(CLAIMANT_ADDRESS)
    with patch.object(gl.nondet.web, "render", return_value="Paris is the capital of France."):
        claim_id = verifier.submit_claim(
            "Paris is the capital of France.", "https://example.test/paris"
        )

    record = json.loads(verifier.get_claim(claim_id))
    assert record["tier"] == "high_reputation"
    assert record["verdict"] == "CONFIRMED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 760  # +10 for high tier


def test_request_dispute_only_by_original_claimant():
    _, verifier, _ = make_wired()
    set_caller(CLAIMANT_ADDRESS)
    with patch.object(gl.nondet, "exec_prompt", return_value=_rejected()):
        claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")

    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        verifier.request_dispute(claim_id)


def test_request_dispute_rejects_confirmed_claims():
    _, verifier, _ = make_wired()
    set_caller(CLAIMANT_ADDRESS)
    with patch.object(gl.nondet, "exec_prompt", return_value=_confirmed()):
        claim_id = verifier.submit_claim("good fact", "https://example.test/good")

    with pytest.raises(gl.vm.UserError):
        verifier.request_dispute(claim_id)


def test_cannot_dispute_same_claim_twice():
    _, verifier, panel = make_wired()
    set_caller(CLAIMANT_ADDRESS)
    with patch.object(gl.nondet, "exec_prompt", return_value=_rejected()):
        claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")

    with patch.object(
        gl.nondet, "exec_prompt",
        return_value=json.dumps({
            "framing_a": "REJECTED", "framing_b": "REJECTED",
            "framing_c": "REJECTED", "final_verdict": "REJECTED",
        }),
    ):
        verifier.request_dispute(claim_id)

    with pytest.raises(gl.vm.UserError):
        verifier.request_dispute(claim_id)
