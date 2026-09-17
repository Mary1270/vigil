import sys

import pytest


@pytest.fixture(autouse=True)
def _reset_known_contract_registry():
    """
    Direct Mode's installed SDK (genlayer-test 0.29.2 / GenVM SDK
    v0.3.0-rc7) tracks the single most-recently-loaded gl.Contract
    subclass in a module-level global (`__known_contract__` inside
    genlayer/gl/genvm_contracts.py) and never resets it automatically.
    Once any contract class has been loaded once in the pytest process,
    loading a second, DIFFERENT contract class anywhere else in that
    same process raises:

        TypeError: only one contract is allowed; first: `...` second: `...`

    Vigil's tests each deploy three different contract types
    (ReputationLedger, ClaimVerifier, DisputePanel) together, so without
    this reset every test after the first one to deploy more than one
    contract type would fail. This reset runs before every test so each
    test can freely (re-)deploy its own set of contract types.

    This is a workaround for behavior observed live in CI, not something
    confirmed against genlayer-test's own documentation or other
    installed versions -- see LESSONS_LEARNED.md.
    """
    for name, module in list(sys.modules.items()):
        if name.endswith("genvm_contracts") and hasattr(module, "__known_contract__"):
            module.__known_contract__ = None
    yield
