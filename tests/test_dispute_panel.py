"""
Tests for DisputePanel, exercising the real cross-contract correction it
writes to ReputationLedger -- the interaction genlayer-test's Direct
Mode was confirmed NOT to support (see LESSONS_LEARNED.md), and the
entire reason a hand-written stub with a working `get_contract_at` was
built for this project (see tests/genlayer_stub/).
"""
import json
from unittest.mock import patch

import pytest

from _bootstrap import make_wired, set_caller, mock_llm_and_page, CLAIMANT_ADDRESS
from genlayer import gl


def _rejected():
    return json.dumps({"grounding": "does not support the claim", "final_verdict": "REJECTED"})


def _three_framing(verdict):
    return json.dumps({
        "framing_a": verdict, "framing_b": verdict, "framing_c": verdict,
        "final_verdict": verdict,
    })


def _reject_a_claim(verifier):
    set_caller(CLAIMANT_ADDRESS)
    with mock_llm_and_page(_rejected()):
        return verifier.submit_claim("bad fact", "https://example.test/bad")


def test_dispute_overturned_reverses_penalty():
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 470  # 500 - 30 (low tier)

    with mock_llm_and_page(_three_framing("CONFIRMED")):
        verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is True
    assert dispute["claim_id"] == claim_id
    # Original penalty (-30) fully reversed and credited: 470 + 60 = 530.
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530


def test_dispute_denied_adds_extra_penalty():
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 470

    with mock_llm_and_page(_three_framing("REJECTED")):
        verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is False
    # Original penalty (-30) stands, plus the denied-dispute penalty (-10).
    assert ledger.get_score(CLAIMANT_ADDRESS) == 460


def test_correction_lands_on_ledger_not_verifier():
    """The design's key property: DisputePanel writes its correction
    directly to ReputationLedger, never back to ClaimVerifier."""
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)

    with mock_llm_and_page(_three_framing("CONFIRMED")):
        verifier.request_dispute(claim_id)

    # The claim record on ClaimVerifier itself is untouched by the
    # dispute outcome (still shows the original REJECTED verdict) --
    # only the ledger's score reflects the correction.
    record = json.loads(verifier.get_claim(claim_id))
    assert record["verdict"] == "REJECTED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530


def test_dispute_review_is_grounded_in_fetched_content():
    """Regression test for the steward-feedback fix: DisputePanel's
    review must actually fetch the evidence page too, not just pass
    the URL to the LLM."""
    _, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)

    with patch.object(gl.nondet.web, "render") as mocked_render:
        mocked_render.return_value = "Some fetched dispute evidence content."
        with patch.object(gl.nondet, "exec_prompt", return_value=_three_framing("REJECTED")):
            verifier.request_dispute(claim_id)
        mocked_render.assert_called_once_with("https://example.test/bad", mode="text")


def test_review_dispute_rejects_calls_not_from_claim_verifier():
    """Regression test for the steward-feedback fix: review_dispute
    must reject calls that don't come from the registered
    ClaimVerifier, even with an otherwise well-formed claim_json --
    otherwise anyone could fabricate a claim and manipulate reputation
    directly."""
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)
    record = verifier.get_claim(claim_id)

    set_caller(CLAIMANT_ADDRESS)  # NOT the registered ClaimVerifier address
    with pytest.raises(gl.vm.UserError):
        panel.review_dispute(claim_id, CLAIMANT_ADDRESS, record)


def test_review_dispute_rejects_replayed_claim_id():
    """Regression test for the steward-feedback fix: even if somehow
    called twice for the same claim_id (bypassing ClaimVerifier's own
    disputed-flag guard), DisputePanel must independently refuse to
    process the same claim_id more than once."""
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)
    record = verifier.get_claim(claim_id)

    from _bootstrap import VERIFIER_ADDRESS
    set_caller(VERIFIER_ADDRESS)
    with mock_llm_and_page(_three_framing("REJECTED")):
        panel.review_dispute(claim_id, CLAIMANT_ADDRESS, record)

    set_caller(VERIFIER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        panel.review_dispute(claim_id, CLAIMANT_ADDRESS, record)
