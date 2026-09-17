# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *
import json


def _normalize_address(value) -> Address:
    if isinstance(value, Address):
        return value
    if isinstance(value, int):
        return Address(value.to_bytes(20, "big"))
    return Address(value)


def _extract_json_object(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start != -1 and end != -1 and end > start:
        return t[start:end + 1]
    return t


TIER_HIGH = "high_reputation"
TIER_MEDIUM = "medium_reputation"
TIER_LOW = "low_reputation_or_unknown"

VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_REJECTED = "REJECTED"

DELTA_HIGH = u256(10)
DELTA_MEDIUM = u256(20)
DELTA_LOW = u256(30)

ZERO_ADDRESS = Address(int(0).to_bytes(20, "big"))


class ClaimVerifier(gl.Contract):
    owner: Address
    reputation_ledger: Address
    dispute_panel: Address
    dispute_panel_set: bool
    low_threshold: u256
    high_threshold: u256
    next_claim_id: u256
    claims: TreeMap[u256, str]

    def __init__(self, reputation_ledger_address, low_threshold: u256, high_threshold: u256):
        self.owner = gl.message.sender_address
        self.reputation_ledger = _normalize_address(reputation_ledger_address)
        self.dispute_panel = ZERO_ADDRESS
        self.dispute_panel_set = False
        self.low_threshold = low_threshold
        self.high_threshold = high_threshold
        self.next_claim_id = u256(0)

    @gl.public.write
    def set_dispute_panel(self, dispute_panel_address) -> None:
        if gl.message.sender_address != self.owner:
            raise gl.vm.UserError("only owner can set dispute panel")
        if self.dispute_panel_set:
            raise gl.vm.UserError("dispute panel already set")
        self.dispute_panel = _normalize_address(dispute_panel_address)
        self.dispute_panel_set = True

    @gl.public.write
    def submit_claim(self, fact: str, evidence_url: str) -> u256:
        claimant = gl.message.sender_address

        # Cross-contract .view() calls MUST happen outside any nondet block.
        reputation = gl.get_contract_at(self.reputation_ledger).view().get_score(claimant)
        has_history = gl.get_contract_at(self.reputation_ledger).view().has_history(claimant)

        if (not has_history) or reputation < self.low_threshold:
            tier = TIER_LOW
            verdict = self._resolve_low(fact, evidence_url)
            delta_magnitude = DELTA_LOW
        elif reputation >= self.high_threshold:
            tier = TIER_HIGH
            verdict = self._resolve_high(fact, evidence_url)
            delta_magnitude = DELTA_HIGH
        else:
            tier = TIER_MEDIUM
            verdict = self._resolve_medium(fact, evidence_url)
            delta_magnitude = DELTA_MEDIUM

        claim_id = self.next_claim_id
        self.next_claim_id = self.next_claim_id + 1

        record = {
            "claim_id": int(claim_id),
            "claimant": str(claimant),
            "fact": fact,
            "evidence_url": evidence_url,
            "reputation_at_submission": int(reputation),
            "tier": tier,
            "verdict": verdict,
            "delta_magnitude": int(delta_magnitude),
            "disputed": False,
        }
        self.claims[claim_id] = json.dumps(record)

        increase = verdict == VERDICT_CONFIRMED
        # Cross-contract .emit() (fire-and-forget, async) also happens
        # outside any nondet block. The claimant address is passed
        # explicitly because sender_address inside ReputationLedger would
        # otherwise resolve to this contract, not the human claimant.
        gl.get_contract_at(self.reputation_ledger).emit().apply_delta(
            claimant, delta_magnitude, increase
        )

        return claim_id

    def _resolve_high(self, fact: str, evidence_url: str) -> str:
        fact_lower = fact.strip().lower()

        def fetch_and_check() -> str:
            page = gl.nondet.web.render(evidence_url, mode="text")
            return "MATCH" if fact_lower in page.lower() else "NO_MATCH"

        result = gl.eq_principle.strict_eq(fetch_and_check)
        return VERDICT_CONFIRMED if result == "MATCH" else VERDICT_REJECTED

    def _resolve_medium(self, fact: str, evidence_url: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are verifying a factual claim under two independent "
                "readings, then giving one final verdict.\n\n"
                "Claim to verify:\n<fact>\n" + fact + "\n</fact>\n\n"
                "Evidence source to check against:\n<source>\n" + evidence_url + "\n</source>\n\n"
                "Step 1 - Retrieve and read the evidence source directly.\n"
                "Step 2 - Independently judge whether the evidence source "
                "supports the claim as stated. Answer CONFIRMED or "
                "REJECTED.\n"
                "Step 3 - Independently judge it a second time from a fresh "
                "reading of the same source. Answer CONFIRMED or REJECTED.\n"
                "Step 4 - If both readings agree, use that as the final "
                "verdict. If they disagree, use REJECTED (a disagreement "
                "can still be escalated on dispute).\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"reading_one": "CONFIRMED or REJECTED", '
                '"reading_two": "CONFIRMED or REJECTED", '
                '"final_verdict": "CONFIRMED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        verdict_json = gl.eq_principle.prompt_comparative(
            analyze,
            "Two-reading claim verification must reach the same final_verdict.",
        )
        try:
            parsed = json.loads(verdict_json)
            verdict = parsed.get("final_verdict", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_CONFIRMED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    def _resolve_low(self, fact: str, evidence_url: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are the lead reviewer for a claim from a submitter with "
                "low or no reputation history. Because the submitter is "
                "unproven, apply the deepest available scrutiny before "
                "producing a verdict.\n\n"
                "Claim to verify:\n<fact>\n" + fact + "\n</fact>\n\n"
                "Evidence source to check against:\n<source>\n" + evidence_url + "\n</source>\n\n"
                "Retrieve the evidence source, analyze it in multiple steps "
                "(what it directly states, what it implies, whether it could "
                "be read to contradict the claim), and only then give a "
                "final verdict.\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"reasoning": "<detailed step-by-step analysis, max 800 '
                'chars>", "final_verdict": "CONFIRMED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        verdict_json = gl.eq_principle.prompt_non_comparative(
            analyze,
            task=(
                "Produce a thorough, multi-step factual verification of an "
                "unproven submitter's claim, with reasoning and a final "
                "verdict."
            ),
            criteria=(
                "The final_verdict must be exactly CONFIRMED or REJECTED, "
                "must follow from the stated reasoning, and the reasoning "
                "must explicitly reference the evidence source's content."
            ),
        )
        try:
            parsed = json.loads(verdict_json)
            verdict = parsed.get("final_verdict", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_CONFIRMED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    @gl.public.write
    def request_dispute(self, claim_id: u256) -> None:
        if not self.dispute_panel_set:
            raise gl.vm.UserError("dispute panel not configured yet")
        if claim_id not in self.claims:
            raise gl.vm.UserError("claim not found")

        record = json.loads(self.claims[claim_id])
        claimant_addr = _normalize_address(record.get("claimant"))
        if gl.message.sender_address != claimant_addr:
            raise gl.vm.UserError("only the original claimant can dispute this claim")
        if record.get("verdict") != VERDICT_REJECTED:
            raise gl.vm.UserError("only a REJECTED claim carries a penalty to dispute")
        if record.get("disputed"):
            raise gl.vm.UserError("claim already disputed")

        record["disputed"] = True
        self.claims[claim_id] = json.dumps(record)

        # Cross-contract .emit() outside any nondet block. The claimant
        # address is passed explicitly for the same reason as above.
        gl.get_contract_at(self.dispute_panel).emit().review_dispute(
            claim_id, claimant_addr, json.dumps(record)
        )

    @gl.public.view
    def get_claim(self, claim_id: u256) -> str:
        if claim_id not in self.claims:
            raise gl.vm.UserError("claim not found")
        return self.claims[claim_id]

    @gl.public.view
    def get_claim_count(self) -> u256:
        return self.next_claim_id
