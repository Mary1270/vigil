"""
Offline tests for the Vigil contracts, using genlayer-test's Direct Mode
(in-process GenVM, no Studio/Docker needed).

IMPORTANT: as with Tribunal's own test_offline.py, the exact signature of
the mock_web / mock_llm cheatcodes has NOT been live-verified against the
installed genlayer-test version in this environment (only direct_vm,
direct_deploy, and the sender-related fixtures were confirmed from public
examples). Before relying on these tests, run them once and adjust the
mock_web(...)/mock_llm(...) calls to match whatever error message pytest
reports for the actual installed version.

One additional, Vigil-specific assumption that also needs live
verification: several tests below call mock_llm.set_response(...) more
than once within a single test, because a single submit_claim/
review_dispute call can trigger more than one LLM-backed step in this
contract set (e.g. a submit_claim that raises reputation into a new tier,
followed by a request_dispute that triggers a second, differently-framed
LLM call). This assumes the mock is re-settable mid-test and that each
call to a contract method picks up the most recently set response. If the
installed harness instead requires a queue or a per-call callback, adjust
these tests accordingly -- do not assume the queue behavior works until it
has been run once.

Run with:
    pip install genlayer-test
    pytest tests/ -v
"""

import json
import pytest


REPUTATION_LEDGER_PATH = "contracts/reputation_ledger.py"
CLAIM_VERIFIER_PATH = "contracts/claim_verifier.py"
DISPUTE_PANEL_PATH = "contracts/dispute_panel.py"

LOW_THRESHOLD = 300
HIGH_THRESHOLD = 700
BASELINE_SCORE = 500

NON_COMPARATIVE_CONFIRMED = json.dumps({
    "reasoning": "The evidence source directly and unambiguously supports the claim.",
    "final_verdict": "CONFIRMED",
})
NON_COMPARATIVE_REJECTED = json.dumps({
    "reasoning": "The evidence source does not support the claim as stated.",
    "final_verdict": "REJECTED",
})
COMPARATIVE_CONFIRMED = json.dumps({
    "reading_one": "CONFIRMED",
    "reading_two": "CONFIRMED",
    "final_verdict": "CONFIRMED",
})
COMPARATIVE_REJECTED = json.dumps({
    "reading_one": "REJECTED",
    "reading_two": "REJECTED",
    "final_verdict": "REJECTED",
})
DISPUTE_OVERTURNED = json.dumps({
    "framing_a": "CONFIRMED",
    "framing_b": "CONFIRMED",
    "framing_c": "CONFIRMED",
    "final_verdict": "CONFIRMED",
})
DISPUTE_DENIED = json.dumps({
    "framing_a": "REJECTED",
    "framing_b": "REJECTED",
    "framing_c": "REJECTED",
    "final_verdict": "REJECTED",
})


def _wire(direct_deploy):
    """Deploy all three contracts and wire them together, matching the
    exact deploy-then-wire order used live on Studio."""
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    verifier = direct_deploy(
        CLAIM_VERIFIER_PATH, ledger.address, LOW_THRESHOLD, HIGH_THRESHOLD
    )
    panel = direct_deploy(DISPUTE_PANEL_PATH, ledger.address)

    ledger.set_claim_verifier(verifier.address)
    ledger.set_dispute_panel(panel.address)
    verifier.set_dispute_panel(panel.address)

    return ledger, verifier, panel


# ---------------------------------------------------------------------------
# ReputationLedger
# ---------------------------------------------------------------------------

def test_ledger_default_score_and_no_history(direct_deploy, direct_accounts):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    someone = direct_accounts[1]
    assert ledger.get_score(someone) == BASELINE_SCORE
    assert ledger.has_history(someone) is False


def test_ledger_apply_delta_rejects_unauthorized_caller(direct_deploy, direct_accounts):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    someone = direct_accounts[1]
    with pytest.raises(Exception):
        ledger.apply_delta(someone, 10, True, sender=someone)


def test_ledger_clamps_at_upper_bound(direct_deploy, direct_accounts):
    ledger, verifier, _ = _wire(direct_deploy)
    # A verifier-originated call is required since apply_delta is
    # authorization-gated; this is exercised indirectly in the
    # ClaimVerifier tests below (test_score_clamps_at_1000).
    assert ledger.get_score(direct_accounts[1]) == BASELINE_SCORE


def test_set_claim_verifier_only_once(direct_deploy):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    ledger.set_claim_verifier("0x" + "11" * 20)
    with pytest.raises(Exception):
        ledger.set_claim_verifier("0x" + "22" * 20)


def test_set_claim_verifier_only_owner(direct_deploy, direct_accounts):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    not_owner = direct_accounts[1]
    with pytest.raises(Exception):
        ledger.set_claim_verifier("0x" + "11" * 20, sender=not_owner)


# ---------------------------------------------------------------------------
# ClaimVerifier -- tier selection driven by ReputationLedger state
# ---------------------------------------------------------------------------

def test_first_claim_from_unknown_address_uses_low_tier(direct_deploy, mock_web, mock_llm):
    ledger, verifier, _ = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_CONFIRMED)

    claim_id = verifier.submit_claim(
        "Paris is the capital of France.",
        "https://example.test/paris",
    )
    assert claim_id == 0

    claim = json.loads(verifier.get_claim(0))
    assert claim["tier"] == "low_reputation_or_unknown"
    assert claim["verdict"] == "CONFIRMED"
    assert claim["reputation_at_submission"] == BASELINE_SCORE

    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE + 30
    assert ledger.has_history(claimant) is True


def test_second_confirmed_claim_moves_to_medium_tier(direct_deploy, mock_web, mock_llm):
    ledger, verifier, _ = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_CONFIRMED)
    verifier.submit_claim("Paris is the capital of France.", "https://example.test/paris")
    # score is now 530 -- above low_threshold=300, below high_threshold=700

    mock_llm.set_response(COMPARATIVE_CONFIRMED)
    claim_id = verifier.submit_claim(
        "Tokyo is the capital of Japan.",
        "https://example.test/tokyo",
    )
    claim = json.loads(verifier.get_claim(claim_id))
    assert claim["tier"] == "medium_reputation"
    assert claim["reputation_at_submission"] == 530

    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == 550


def test_rejected_claim_lowers_reputation(direct_deploy, mock_web, mock_llm):
    ledger, verifier, _ = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim(
        "The moon is made of cheese.",
        "https://example.test/moon",
    )
    claim = json.loads(verifier.get_claim(claim_id))
    assert claim["verdict"] == "REJECTED"

    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30


def test_score_clamps_at_1000(direct_deploy, mock_web, mock_llm):
    ledger, verifier, _ = _wire(direct_deploy)

    # Repeated confirmed claims should push the score toward 1000 and no
    # further, regardless of how many more confirmed claims follow.
    mock_llm.set_response(NON_COMPARATIVE_CONFIRMED)
    verifier.submit_claim("fact 0", "https://example.test/0")  # 500 -> 530
    mock_llm.set_response(COMPARATIVE_CONFIRMED)
    for i in range(1, 30):
        verifier.submit_claim(f"fact {i}", f"https://example.test/{i}")

    claim0 = json.loads(verifier.get_claim(0))
    claimant = claim0["claimant"]
    assert ledger.get_score(claimant) <= 1000


def test_request_dispute_only_by_original_claimant(direct_deploy, mock_web, mock_llm, direct_accounts):
    ledger, verifier, panel = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")

    not_claimant = direct_accounts[1]
    with pytest.raises(Exception):
        verifier.request_dispute(claim_id, sender=not_claimant)


def test_request_dispute_rejects_confirmed_claims(direct_deploy, mock_web, mock_llm):
    ledger, verifier, panel = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_CONFIRMED)
    claim_id = verifier.submit_claim("good fact", "https://example.test/good")

    with pytest.raises(Exception):
        verifier.request_dispute(claim_id)


# ---------------------------------------------------------------------------
# DisputePanel -- correction lands on ReputationLedger, not ClaimVerifier
# ---------------------------------------------------------------------------

def test_dispute_overturned_reverses_penalty(direct_deploy, mock_web, mock_llm):
    ledger, verifier, panel = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim("wrongly rejected fact", "https://example.test/x")
    claim = json.loads(verifier.get_claim(claim_id))
    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30

    # NOTE: this assumes mock_llm can be re-set mid-test to a different
    # response for the dispute panel's own LLM call -- see module
    # docstring caveat.
    mock_llm.set_response(DISPUTE_OVERTURNED)
    verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is True

    # Original penalty (-30) should be fully reversed and credited:
    # 470 + 60 = 530.
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30 + 60


def test_dispute_denied_adds_extra_penalty(direct_deploy, mock_web, mock_llm):
    ledger, verifier, panel = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim("correctly rejected fact", "https://example.test/y")
    claim = json.loads(verifier.get_claim(claim_id))
    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30

    mock_llm.set_response(DISPUTE_DENIED)
    verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is False

    # Original penalty (-30) stands, plus the extra denied-dispute
    # penalty (-10): 500 - 30 - 10 = 460.
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30 - 10


def test_cannot_dispute_same_claim_twice(direct_deploy, mock_web, mock_llm):
    ledger, verifier, panel = _wire(direct_deploy)

    mock_llm.set_response(NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")

    mock_llm.set_response(DISPUTE_DENIED)
    verifier.request_dispute(claim_id)

    with pytest.raises(Exception):
        verifier.request_dispute(claim_id)
