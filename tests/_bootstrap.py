"""
Shared test bootstrap - wires up the offline genlayer SDK stub and loads
all three Vigil contracts once. Same pattern used by the sibling
MatchGuard/ScoreSettle projects, extended here with multi-contract
deploy + wiring helpers since Vigil (unlike those single-contract
projects) is a genuine three-contract system.
"""
import importlib.util
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

_CONTRACTS_DIR = os.path.join(os.path.dirname(_THIS_DIR), "contracts")


def _load(module_name, filename):
    path = os.path.join(_CONTRACTS_DIR, filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ledger_module = _load("vigil_reputation_ledger", "reputation_ledger.py")
_verifier_module = _load("vigil_claim_verifier", "claim_verifier.py")
_panel_module = _load("vigil_dispute_panel", "dispute_panel.py")

ReputationLedger = _ledger_module.ReputationLedger
ClaimVerifier = _verifier_module.ClaimVerifier
DisputePanel = _panel_module.DisputePanel

from genlayer import gl, Address, register_contract, clear_registry  # noqa: E402

# Fixed, valid, distinct addresses reused across test files.
LEDGER_ADDRESS = "0x" + "aa" * 20
VERIFIER_ADDRESS = "0x" + "bb" * 20
PANEL_ADDRESS = "0x" + "cc" * 20
OWNER_ADDRESS = "0x" + "33" * 20
CLAIMANT_ADDRESS = "0x" + "11" * 20
STRANGER_ADDRESS = "0x" + "22" * 20

LOW_THRESHOLD = 300
HIGH_THRESHOLD = 700


def set_caller(address_str: str) -> None:
    """Simulate a specific wallet calling the next contract method."""
    gl.message.sender_address = Address(address_str)


def make_ledger() -> "ReputationLedger":
    """Deploy a standalone ReputationLedger, owned by OWNER_ADDRESS."""
    set_caller(OWNER_ADDRESS)
    ledger = ReputationLedger()
    register_contract(LEDGER_ADDRESS, ledger)
    return ledger


def make_wired():
    """Deploy and wire all three contracts, in the exact order used
    live on Studio: ReputationLedger -> ClaimVerifier -> DisputePanel,
    then set_claim_verifier / set_dispute_panel (x2)."""
    clear_registry()
    ledger = make_ledger()

    set_caller(OWNER_ADDRESS)
    verifier = ClaimVerifier(LEDGER_ADDRESS, LOW_THRESHOLD, HIGH_THRESHOLD)
    register_contract(VERIFIER_ADDRESS, verifier)

    set_caller(OWNER_ADDRESS)
    panel = DisputePanel(LEDGER_ADDRESS)
    register_contract(PANEL_ADDRESS, panel)

    set_caller(OWNER_ADDRESS)
    ledger.set_claim_verifier(VERIFIER_ADDRESS)
    set_caller(OWNER_ADDRESS)
    ledger.set_dispute_panel(PANEL_ADDRESS)
    set_caller(OWNER_ADDRESS)
    verifier.set_dispute_panel(PANEL_ADDRESS)

    return ledger, verifier, panel
