# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


BASELINE_SCORE = u256(500)
MIN_SCORE = u256(0)
MAX_SCORE = u256(1000)
ZERO_ADDRESS = Address(int(0).to_bytes(20, "big"))


class ReputationLedger(gl.Contract):
    owner: Address
    claim_verifier: Address
    claim_verifier_set: bool
    dispute_panel: Address
    dispute_panel_set: bool
    scores: TreeMap[Address, u256]
    known: TreeMap[Address, bool]

    def __init__(self):
        self.owner = gl.message.sender_address
        self.claim_verifier = ZERO_ADDRESS
        self.claim_verifier_set = False
        self.dispute_panel = ZERO_ADDRESS
        self.dispute_panel_set = False

    @gl.public.write
    def set_claim_verifier(self, claim_verifier_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set claim verifier")
        if self.claim_verifier_set:
            raise gl.vm.UserError("claim verifier already set")
        self.claim_verifier = _normalize_address(claim_verifier_address)
        self.claim_verifier_set = True

    @gl.public.write
    def set_dispute_panel(self, dispute_panel_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set dispute panel")
        if self.dispute_panel_set:
            raise gl.vm.UserError("dispute panel already set")
        self.dispute_panel = _normalize_address(dispute_panel_address)
        self.dispute_panel_set = True

    @gl.public.write
    def apply_delta(self, subject, magnitude: u256, increase: bool) -> None:
        sender = gl.message.sender_address
        if sender != self.claim_verifier and sender != self.dispute_panel:
            raise gl.vm.UserError(
                "only claim_verifier or dispute_panel may adjust reputation"
            )
        subject_addr = _normalize_address(subject)

        current = BASELINE_SCORE
        if subject_addr in self.scores:
            current = self.scores[subject_addr]

        if increase:
            updated = current + magnitude
            if updated > MAX_SCORE:
                updated = MAX_SCORE
        else:
            if magnitude > current:
                updated = MIN_SCORE
            else:
                updated = current - magnitude

        self.scores[subject_addr] = updated
        self.known[subject_addr] = True

    @gl.public.view
    def get_score(self, subject) -> u256:
        subject_addr = _normalize_address(subject)
        if subject_addr in self.scores:
            return self.scores[subject_addr]
        return BASELINE_SCORE

    @gl.public.view
    def has_history(self, subject) -> bool:
        subject_addr = _normalize_address(subject)
        return subject_addr in self.known
