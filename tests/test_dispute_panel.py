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

from _bootstrap import make_wired, set_caller, CLAIMANT_ADDRESS
from genlayer import gl


def _rejected():
    return json.dumps({"reasoning": "does not support the claim", "final_verdict": "REJECTED"})


def _three_framing(verdict):
    return json.dumps({
        "framing_a": verdict, "framing_b": verdict, "framing_c": verdict,
        "final_verdict": verdict,
    })


def _reject_a_claim(verifier):
    set_caller(CLAIMANT_ADDRESS)
    with patch.object(gl.nondet, "exec_prompt", return_value=_rejected()):
        return verifier.submit_claim("bad fact", "https://example.test/bad")


def test_dispute_overturned_reverses_penalty():
    ledger, verifier, panel = make_wired()
    claim_id = _reject_a_claim(verifier)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 470  # 500 - 30 (low tier)

    with patch.object(gl.nondet, "exec_prompt", return_value=_three_framing("CONFIRMED")):
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

    with patch.object(gl.nondet, "exec_prompt", return_value=_three_framing("REJECTED")):
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

    with patch.object(gl.nondet, "exec_prompt", return_value=_three_framing("CONFIRMED")):
        verifier.request_dispute(claim_id)

    # The claim record on ClaimVerifier itself is untouched by the
    # dispute outcome (still shows the original REJECTED verdict) --
    # only the ledger's score reflects the correction.
    record = json.loads(verifier.get_claim(claim_id))
    assert record["verdict"] == "REJECTED"
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530
