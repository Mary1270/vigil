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


VERDICT_CONFIRMED = "CONFIRMED"
VERDICT_REJECTED = "REJECTED"

DISPUTE_DENIED_PENALTY = u256(10)


class DisputePanel(gl.Contract):
    owner: Address
    reputation_ledger: Address
    next_dispute_id: u256
    disputes: TreeMap[u256, str]

    def __init__(self, reputation_ledger_address):
        self.owner = gl.message.sender_address
        self.reputation_ledger = _normalize_address(reputation_ledger_address)
        self.next_dispute_id = u256(0)

    @gl.public.write
    def review_dispute(self, claim_id: u256, claimant, claim_json: str) -> None:
        claimant_addr = _normalize_address(claimant)
        claim_record = json.loads(claim_json)
        fact = claim_record.get("fact", "")
        evidence_url = claim_record.get("evidence_url", "")
        original_verdict = claim_record.get("verdict", VERDICT_REJECTED)
        original_magnitude_raw = claim_record.get("delta_magnitude", 0)
        original_magnitude = u256(int(original_magnitude_raw))

        final_verdict = self._review(fact, evidence_url, original_verdict)
        overturned = final_verdict != original_verdict

        dispute_id = self.next_dispute_id
        self.next_dispute_id = self.next_dispute_id + 1
        dispute_record = {
            "dispute_id": int(dispute_id),
            "claim_id": int(claim_id),
            "claimant": str(claimant_addr),
            "original_verdict": original_verdict,
            "final_verdict": final_verdict,
            "overturned": overturned,
        }
        self.disputes[dispute_id] = json.dumps(dispute_record)

        # Cross-contract .emit() outside any nondet block, writing directly
        # to ReputationLedger rather than back to ClaimVerifier: the shared
        # reputation state is what must be corrected, since it is the
        # state that will decide this claimant's next equivalence-principle
        # path.
        if overturned:
            # Reverse the original penalty and credit as if the claim had
            # been confirmed from the start: cancel the prior negative
            # delta and add an equal positive one in the same call.
            reversal_magnitude = original_magnitude + original_magnitude
            gl.get_contract_at(self.reputation_ledger).emit().apply_delta(
                claimant_addr, reversal_magnitude, True
            )
        else:
            # The penalty stands, and a denied dispute costs a little more:
            # it discourages disputing a well-founded rejection.
            gl.get_contract_at(self.reputation_ledger).emit().apply_delta(
                claimant_addr, DISPUTE_DENIED_PENALTY, False
            )

    def _review(self, fact: str, evidence_url: str, original_verdict: str) -> str:
        def analyze() -> str:
            prompt = (
                "You are an independent dispute panel re-reviewing a factual "
                "claim that was already rejected once, with a reputation "
                "penalty applied to the submitter. Do not defer to the "
                "original verdict. Consider the claim under three separate, "
                "independent framings before giving one final verdict.\n\n"
                "Claim to verify:\n<fact>\n" + fact + "\n</fact>\n\n"
                "Evidence source to check against:\n<source>\n" + evidence_url + "\n</source>\n\n"
                "Original verdict under dispute (may be wrong):\n<original>\n"
                + original_verdict + "\n</original>\n\n"
                "Framing A - A skeptical, evidence-first reading that "
                "assumes nothing not explicitly stated in the source.\n"
                "Framing B - A charitable reading that gives the claim the "
                "benefit of reasonable interpretation of the source.\n"
                "Framing C - A literal, word-for-word comparison between "
                "the claim's wording and the source's wording.\n\n"
                "Give each framing's answer (CONFIRMED or REJECTED), then a "
                "final_verdict that is the majority of the three (if all "
                "three differ in a way that prevents a majority, use "
                "REJECTED).\n\n"
                "Respond with strict JSON only, no other text, no markdown "
                "fence:\n"
                '{"framing_a": "CONFIRMED or REJECTED", '
                '"framing_b": "CONFIRMED or REJECTED", '
                '"framing_c": "CONFIRMED or REJECTED", '
                '"final_verdict": "CONFIRMED or REJECTED"}'
            )
            raw = gl.nondet.exec_prompt(prompt)
            return _extract_json_object(raw)

        verdict_json = gl.eq_principle.prompt_comparative(
            analyze,
            "Three-framing dispute review must reach the same final_verdict.",
        )
        try:
            parsed = json.loads(verdict_json)
            verdict = parsed.get("final_verdict", VERDICT_REJECTED)
        except Exception:
            verdict = VERDICT_REJECTED
        if verdict not in (VERDICT_CONFIRMED, VERDICT_REJECTED):
            verdict = VERDICT_REJECTED
        return verdict

    @gl.public.view
    def get_dispute(self, dispute_id: u256) -> str:
        if dispute_id not in self.disputes:
            raise gl.vm.UserError("dispute not found")
        return self.disputes[dispute_id]

    @gl.public.view
    def get_dispute_count(self) -> u256:
        return self.next_dispute_id
