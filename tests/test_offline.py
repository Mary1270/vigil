"""
Offline tests for the Vigil contracts, using genlayer-test's Direct Mode
(in-process GenVM, no Studio/Docker needed).

Confirmed live in CI (genlayer-test 0.29.2, GenVM SDK v0.3.0-rc7,
Python 3.12): there is no `mock_web` / `mock_llm` pytest fixture. Mocking
goes through the `direct_vm` fixture instead:

    direct_vm.mock_llm(pattern: str, response: str)   # pattern is a regex
                                                       # matched against
                                                       # the prompt text
    direct_vm.mock_web(pattern: str, response: dict)  # not used here --
                                                       # none of these
                                                       # tests exercise the
                                                       # high_reputation
                                                       # tier's strict_eq
                                                       # web fetch
    direct_vm.clear_mocks()
    direct_vm.sender = <address>   # also confirmed: passing sender=...
                                    # directly as a call kwarg works too

Rather than relying on `clear_mocks()` ordering, each test registers one
`mock_llm` pattern per distinct prompt shape it expects to trigger (low
tier, medium tier, dispute review), using a short substring from that
prompt as the regex. Since the three prompt shapes never overlap, multiple
patterns can be registered up front in a single test with no risk of one
overriding another.

Also confirmed live in CI: this installed SDK tracks the single
most-recently-loaded contract class in a process-global and raises
`TypeError: only one contract is allowed` the moment a second, different
contract type is loaded anywhere in the same pytest session. See
tests/conftest.py for the reset fixture this requires -- without it,
every test here that deploys more than one contract type would fail.

Run with:
    pip install genlayer-test
    pytest tests/ -v
"""

import json
import sys

import pytest


REPUTATION_LEDGER_PATH = "contracts/reputation_ledger.py"
CLAIM_VERIFIER_PATH = "contracts/claim_verifier.py"
DISPUTE_PANEL_PATH = "contracts/dispute_panel.py"

LOW_THRESHOLD = 300
HIGH_THRESHOLD = 700
BASELINE_SCORE = 500

# Unique substrings from each resolver's prompt, used as mock_llm patterns.
LOW_TIER_PATTERN = "lead reviewer for a claim"
MEDIUM_TIER_PATTERN = "two independent"
DISPUTE_PATTERN = "independent dispute panel"

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


def _reset_known_contract():
    """See tests/conftest.py for why this is needed. Unlike the conftest
    fixture (which only resets once before each test), this must also run
    immediately after EACH individual deploy inside _wire: the first
    deploy in a test re-arms the check, so the second deploy trips it
    again unless reset in between."""
    for name, module in list(sys.modules.items()):
        if name.endswith("genvm_contracts") and hasattr(module, "__known_contract__"):
            module.__known_contract__ = None


def _wire(direct_deploy):
    """Deploy all three contracts and wire them together, matching the
    exact deploy-then-wire order used live on Studio."""
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    _reset_known_contract()
    verifier = direct_deploy(
        CLAIM_VERIFIER_PATH, ledger.address, LOW_THRESHOLD, HIGH_THRESHOLD
    )
    _reset_known_contract()
    panel = direct_deploy(DISPUTE_PANEL_PATH, ledger.address)
    _reset_known_contract()

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

def test_first_claim_from_unknown_address_uses_low_tier(direct_vm, direct_deploy):
    ledger, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_CONFIRMED)

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


def test_second_confirmed_claim_moves_to_medium_tier(direct_vm, direct_deploy):
    ledger, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_CONFIRMED)
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_CONFIRMED)

    verifier.submit_claim("Paris is the capital of France.", "https://example.test/paris")
    # score is now 530 -- above low_threshold=300, below high_threshold=700

    claim_id = verifier.submit_claim(
        "Tokyo is the capital of Japan.",
        "https://example.test/tokyo",
    )
    claim = json.loads(verifier.get_claim(claim_id))
    assert claim["tier"] == "medium_reputation"
    assert claim["reputation_at_submission"] == 530

    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == 550


def test_rejected_claim_lowers_reputation(direct_vm, direct_deploy):
    ledger, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)

    claim_id = verifier.submit_claim(
        "The moon is made of cheese.",
        "https://example.test/moon",
    )
    claim = json.loads(verifier.get_claim(claim_id))
    assert claim["verdict"] == "REJECTED"

    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30


def test_score_clamps_at_1000(direct_vm, direct_deploy):
    ledger, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_CONFIRMED)
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_CONFIRMED)

    verifier.submit_claim("fact 0", "https://example.test/0")  # 500 -> 530
    for i in range(1, 30):
        verifier.submit_claim(f"fact {i}", f"https://example.test/{i}")

    claim0 = json.loads(verifier.get_claim(0))
    claimant = claim0["claimant"]
    assert ledger.get_score(claimant) <= 1000


def test_request_dispute_only_by_original_claimant(direct_vm, direct_deploy, direct_accounts):
    _, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)

    claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")

    not_claimant = direct_accounts[1]
    with pytest.raises(Exception):
        verifier.request_dispute(claim_id, sender=not_claimant)


def test_request_dispute_rejects_confirmed_claims(direct_vm, direct_deploy):
    _, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_CONFIRMED)

    claim_id = verifier.submit_claim("good fact", "https://example.test/good")

    with pytest.raises(Exception):
        verifier.request_dispute(claim_id)


# ---------------------------------------------------------------------------
# DisputePanel -- correction lands on ReputationLedger, not ClaimVerifier
# ---------------------------------------------------------------------------

def test_dispute_overturned_reverses_penalty(direct_vm, direct_deploy):
    ledger, verifier, panel = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)
    direct_vm.mock_llm(DISPUTE_PATTERN, DISPUTE_OVERTURNED)

    claim_id = verifier.submit_claim("wrongly rejected fact", "https://example.test/x")
    claim = json.loads(verifier.get_claim(claim_id))
    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30

    verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is True

    # Original penalty (-30) fully reversed and credited: 470 + 60 = 530.
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30 + 60


def test_dispute_denied_adds_extra_penalty(direct_vm, direct_deploy):
    ledger, verifier, panel = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)
    direct_vm.mock_llm(DISPUTE_PATTERN, DISPUTE_DENIED)

    claim_id = verifier.submit_claim("correctly rejected fact", "https://example.test/y")
    claim = json.loads(verifier.get_claim(claim_id))
    claimant = claim["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30

    verifier.request_dispute(claim_id)

    dispute = json.loads(panel.get_dispute(0))
    assert dispute["overturned"] is False

    # Original penalty (-30) stands, plus the denied-dispute penalty (-10):
    # 500 - 30 - 10 = 460.
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30 - 10


def test_cannot_dispute_same_claim_twice(direct_vm, direct_deploy):
    _, verifier, _ = _wire(direct_deploy)
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)
    direct_vm.mock_llm(DISPUTE_PATTERN, DISPUTE_DENIED)

    claim_id = verifier.submit_claim("bad fact", "https://example.test/bad")
    verifier.request_dispute(claim_id)

    with pytest.raises(Exception):
        verifier.request_dispute(claim_id)
