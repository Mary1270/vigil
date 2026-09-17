"""
Offline tests for the Vigil contracts, using genlayer-test's Direct Mode
(in-process GenVM, no Studio/Docker needed).

Confirmed live in CI (genlayer-test 0.29.2, GenVM SDK v0.3.0-rc7,
Python 3.12):

- There is no `mock_web` / `mock_llm` pytest fixture. Mocking goes
  through the `direct_vm` fixture instead: `direct_vm.mock_llm(pattern,
  response)`, where `pattern` is a regex matched against the prompt text.
  `direct_vm.mock_web` is not used here -- none of these tests exercise
  the high_reputation tier's strict_eq web fetch.
- Loading more than one DIFFERENT contract type in a single test needs
  the reset in tests/conftest.py (this SDK version tracks the
  most-recently-loaded contract class in a process-global and raises
  "only one contract is allowed" otherwise) -- AND that reset must run
  between each individual deploy within a test, not just once before it,
  since the first deploy re-arms the check before the second one runs.
- ReputationLedger/ClaimVerifier/DisputePanel deployed with IDENTICAL
  constructor arguments across DIFFERENT test functions were observed to
  share persistent state (a fresh deploy in a later test saw
  `dispute_panel_set` already True from an earlier test's wiring), even
  though each test gets its own freshly-loaded Python object. Since
  ReputationLedger takes no constructor arguments at all, every test that
  deployed the full three-contract set was deploying with identical
  arguments and colliding. The fix used here: wire the three contracts
  together exactly ONCE, in a single comprehensive scenario test, instead
  of redeploying a fresh set per test function. This sidesteps the
  collision entirely and also mirrors how the system was actually
  end-to-end verified live on Studio (one continuous sequence of calls,
  not independent isolated scenarios).

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
    """See tests/conftest.py docstring. Must also run between each
    individual deploy below, not just once before the test."""
    for name, module in list(sys.modules.items()):
        if name.endswith("genvm_contracts") and hasattr(module, "__known_contract__"):
            module.__known_contract__ = None


# ---------------------------------------------------------------------------
# ReputationLedger in isolation (no wiring needed)
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


# ---------------------------------------------------------------------------
# Full three-contract lifecycle, wired exactly once (see module docstring
# for why this is a single test rather than many independent ones)
# ---------------------------------------------------------------------------

def test_full_lifecycle(direct_vm, direct_deploy, direct_accounts):
    ledger = direct_deploy(REPUTATION_LEDGER_PATH)
    _reset_known_contract()
    verifier = direct_deploy(
        CLAIM_VERIFIER_PATH, ledger.address, LOW_THRESHOLD, HIGH_THRESHOLD
    )
    _reset_known_contract()
    panel = direct_deploy(DISPUTE_PANEL_PATH, ledger.address)
    _reset_known_contract()

    # --- wiring: only-once / only-owner checks, then real wiring ---
    not_owner = direct_accounts[1]
    with pytest.raises(Exception):
        ledger.set_claim_verifier(verifier.address, sender=not_owner)

    ledger.set_claim_verifier(verifier.address)
    with pytest.raises(Exception):
        ledger.set_claim_verifier(verifier.address)

    ledger.set_dispute_panel(panel.address)
    verifier.set_dispute_panel(panel.address)

    # --- step 1: fresh address, no history -> low tier, CONFIRMED ---
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

    # --- step 2: reputation 530 -> medium tier, CONFIRMED ---
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_CONFIRMED)

    claim1 = verifier.submit_claim(
        "Tokyo is the capital of Japan.", "https://example.test/tokyo"
    )
    record1 = json.loads(verifier.get_claim(claim1))
    assert record1["tier"] == "medium_reputation"
    assert record1["reputation_at_submission"] == 530
    assert ledger.get_score(claimant) == 550

    # --- step 3: reputation 550, false fact -> medium tier, REJECTED ---
    direct_vm.clear_mocks()
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_REJECTED)

    claim2 = verifier.submit_claim(
        "Tokyo is the capital of France.", "https://example.test/tokyo"
    )
    record2 = json.loads(verifier.get_claim(claim2))
    assert record2["verdict"] == "REJECTED"
    assert ledger.get_score(claimant) == 530

    # A CONFIRMED claim (claim0) carries no penalty and cannot be disputed.
    with pytest.raises(Exception):
        verifier.request_dispute(claim0)

    # Only the original claimant may dispute claim2.
    with pytest.raises(Exception):
        verifier.request_dispute(claim2, sender=not_owner)

    # --- step 4: dispute claim2, panel denies it -> extra penalty ---
    direct_vm.mock_llm(DISPUTE_PATTERN, DISPUTE_DENIED)

    verifier.request_dispute(claim2)
    dispute0 = json.loads(panel.get_dispute(0))
    assert dispute0["overturned"] is False
    # 550 - 20 (rejection) - 10 (denied dispute) = 520 -- matches the
    # live Studio verification recorded in LESSONS_LEARNED.md exactly.
    assert ledger.get_score(claimant) == 520

    # Disputing the same claim twice is rejected.
    with pytest.raises(Exception):
        verifier.request_dispute(claim2)

    # --- step 5: a second rejected claim, this time the panel overturns it ---
    direct_vm.clear_mocks()
    direct_vm.mock_llm(MEDIUM_TIER_PATTERN, COMPARATIVE_REJECTED)

    claim3 = verifier.submit_claim(
        "Berlin is the capital of Spain.", "https://example.test/berlin"
    )
    record3 = json.loads(verifier.get_claim(claim3))
    assert record3["verdict"] == "REJECTED"
    assert ledger.get_score(claimant) == 500  # 520 - 20

    direct_vm.mock_llm(DISPUTE_PATTERN, DISPUTE_OVERTURNED)
    verifier.request_dispute(claim3)

    dispute1 = json.loads(panel.get_dispute(1))
    assert dispute1["overturned"] is True
    # Original penalty (-20) fully reversed and credited: 480 + 40 = 520.
    assert ledger.get_score(claimant) == 500 - 20 + 40
