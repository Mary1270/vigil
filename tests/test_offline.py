"""
Offline tests for the Vigil contracts, using genlayer-test's Direct Mode
(in-process GenVM, no Studio/Docker needed).

Confirmed live in CI (genlayer-test 0.29.2, GenVM SDK v0.3.0-rc7,
Python 3.12):

- There is no `mock_web` / `mock_llm` pytest fixture. Mocking goes
  through the `direct_vm` fixture instead: `direct_vm.mock_llm(pattern,
  response)`, where `pattern` is a regex matched against the prompt text.
- Loading more than one DIFFERENT contract type in a single test needs
  the reset in tests/conftest.py (this SDK version tracks the
  most-recently-loaded contract class in a process-global and raises
  "only one contract is allowed" otherwise), and that reset must run
  between each individual deploy within a test, not just once before it.

DisputePanel is intentionally NOT wired or exercised in these offline
tests. Reason, found live in CI: ReputationLedger and ClaimVerifier both
declare a boolean field named `dispute_panel_set` (each contract's own
one-time wiring guard). In this installed Direct Mode version, calling
`ledger.set_dispute_panel(...)` immediately before
`verifier.set_dispute_panel(...)` makes the second call fail with
"dispute panel already set" -- even on a freshly-deployed verifier that
has never had that method called on it before. This points to Direct
Mode's storage simulation not fully namespacing same-named fields across
different contract instances/types loaded in the same process. It is not
a real GenVM/contract bug: the exact same wiring sequence (see the
deployment steps in this repo's README) was confirmed working correctly
against real GenVM consensus live on Studio, transaction by transaction,
including the full dispute lifecycle -- see LESSONS_LEARNED.md for the
recorded live sequence and results. Renaming the field would only work
around a test-harness artifact at the cost of no longer matching what is
actually deployed live, so the contract source is left as-is and
DisputePanel's behavior is treated as live-verified rather than
offline-verified.

Run with:
    pip install genlayer-test
    pytest tests/ -v
"""

import json
import sys

import pytest


REPUTATION_LEDGER_PATH = "contracts/reputation_ledger.py"
CLAIM_VERIFIER_PATH = "contracts/claim_verifier.py"

LOW_THRESHOLD = 300
HIGH_THRESHOLD = 700
BASELINE_SCORE = 500

LOW_TIER_PATTERN = "lead reviewer for a claim"
MEDIUM_TIER_PATTERN = "two independent"

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


def _reset_known_contract():
    """See tests/conftest.py docstring. Must also run between each
    individual deploy below, not just once before the test."""
    for name, module in list(sys.modules.items()):
        if name.endswith("genvm_contracts") and hasattr(module, "__known_contract__"):
            module.__known_contract__ = None


# ---------------------------------------------------------------------------
# ReputationLedger in isolation
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
# ClaimVerifier + ReputationLedger: the reputation feedback loop
# (DisputePanel excluded -- see module docstring)
# ---------------------------------------------------------------------------

def test_reputation_loop_moves_tiers_and_deltas(direct_vm, direct_deploy):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    _reset_known_contract()
    verifier = direct_deploy(
        CLAIM_VERIFIER_PATH, ledger.address, LOW_THRESHOLD, HIGH_THRESHOLD
    )
    _reset_known_contract()

    ledger.set_claim_verifier(verifier.address)

    # Step 1: fresh address, no history -> low tier, CONFIRMED.
    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_CONFIRMED)
    claim0 = verifier.submit_claim(
        "Paris is the capital of France.", "https://example.test/paris"
    )
    assert claim0 == 0
    record0 = json.loads(verifier.get_claim(0))
    assert record0["tier"] == "low_reputation_or_unknown"
    assert record0["verdict"] == "CONFIRMED"
    assert record0["reputation_at_submission"] == BASELINE_SCORE

    claimant = record0["claimant"]
    assert ledger.get_score(claimant) == 530
    assert ledger.has_history(claimant) is True

    # Step 2: reputation 530 -> medium tier, CONFIRMED.
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_CONFIRMED)
    claim1 = verifier.submit_claim(
        "Tokyo is the capital of Japan.", "https://example.test/tokyo"
    )
    record1 = json.loads(verifier.get_claim(claim1))
    assert record1["tier"] == "medium_reputation"
    assert record1["reputation_at_submission"] == 530
    assert ledger.get_score(claimant) == 550


def test_rejected_claim_lowers_reputation(direct_vm, direct_deploy):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    _reset_known_contract()
    verifier = direct_deploy(
        CLAIM_VERIFIER_PATH, ledger.address, LOW_THRESHOLD, HIGH_THRESHOLD
    )
    _reset_known_contract()
    ledger.set_claim_verifier(verifier.address)

    direct_vm.mock_llm(LOW_TIER_PATTERN, NON_COMPARATIVE_REJECTED)
    claim_id = verifier.submit_claim(
        "The moon is made of cheese.", "https://example.test/moon"
    )
    record = json.loads(verifier.get_claim(claim_id))
    assert record["verdict"] == "REJECTED"

    claimant = record["claimant"]
    assert ledger.get_score(claimant) == BASELINE_SCORE - 30
