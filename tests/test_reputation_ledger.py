"""
Standalone tests for ReputationLedger: the one contract whose own logic
never depends on a cross-contract call, so it was already testable even
under genlayer-test's Direct Mode. Kept here using the same stub-based
bootstrap as the other test files, for a single consistent offline
testing approach across the whole suite (see LESSONS_LEARNED.md for why
Direct Mode was dropped in favor of this stub for the rest of the
system).
"""
import pytest

from _bootstrap import (
    make_ledger, set_caller,
    OWNER_ADDRESS, VERIFIER_ADDRESS, PANEL_ADDRESS,
    CLAIMANT_ADDRESS, STRANGER_ADDRESS,
)
from genlayer import gl


def test_default_score_and_no_history():
    ledger = make_ledger()
    assert ledger.get_score(CLAIMANT_ADDRESS) == 500
    assert ledger.has_history(CLAIMANT_ADDRESS) is False


def test_apply_delta_rejects_unauthorized_caller():
    ledger = make_ledger()
    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        ledger.apply_delta(CLAIMANT_ADDRESS, 10, True)


def test_apply_delta_increase_and_decrease():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_claim_verifier(VERIFIER_ADDRESS)

    set_caller(VERIFIER_ADDRESS)
    ledger.apply_delta(CLAIMANT_ADDRESS, 30, True)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 530
    assert ledger.has_history(CLAIMANT_ADDRESS) is True

    set_caller(VERIFIER_ADDRESS)
    ledger.apply_delta(CLAIMANT_ADDRESS, 50, False)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 480


def test_apply_delta_authorizes_dispute_panel_too():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_dispute_panel(PANEL_ADDRESS)

    set_caller(PANEL_ADDRESS)
    ledger.apply_delta(CLAIMANT_ADDRESS, 20, True)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 520


def test_score_clamps_at_upper_bound():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_claim_verifier(VERIFIER_ADDRESS)

    set_caller(VERIFIER_ADDRESS)
    ledger.apply_delta(CLAIMANT_ADDRESS, 900, True)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 1000


def test_score_clamps_at_lower_bound():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_claim_verifier(VERIFIER_ADDRESS)

    set_caller(VERIFIER_ADDRESS)
    ledger.apply_delta(CLAIMANT_ADDRESS, 900, False)
    assert ledger.get_score(CLAIMANT_ADDRESS) == 0


def test_set_claim_verifier_only_once():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_claim_verifier(VERIFIER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        set_caller(OWNER_ADDRESS)
        ledger.set_claim_verifier(VERIFIER_ADDRESS)


def test_set_claim_verifier_only_owner():
    ledger = make_ledger()
    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        ledger.set_claim_verifier(VERIFIER_ADDRESS)


def test_set_dispute_panel_only_once():
    ledger = make_ledger()
    set_caller(OWNER_ADDRESS)
    ledger.set_dispute_panel(PANEL_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        set_caller(OWNER_ADDRESS)
        ledger.set_dispute_panel(PANEL_ADDRESS)


def test_set_dispute_panel_only_owner():
    ledger = make_ledger()
    set_caller(STRANGER_ADDRESS)
    with pytest.raises(gl.vm.UserError):
        ledger.set_dispute_panel(PANEL_ADDRESS)
