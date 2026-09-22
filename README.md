# Vigil

A fact-verification oracle built from three GenLayer Intelligent Contracts
whose consensus strictness is driven by a live, self-adjusting reputation
score — not by the claim's amount or evidence type, but by the track
record of the address submitting it:

- **ReputationLedger** — holds a per-address reputation score (baseline
  500, range 0–1000). No equivalence principle of its own: it is pure,
  deterministic shared state, and the single piece of connective tissue
  that makes the feedback loop below real. Only `ClaimVerifier` and
  `DisputePanel` are authorized to change a score.
- **ClaimVerifier** — before picking an equivalence principle, reads the
  submitter's current reputation from `ReputationLedger` via a real
  `.view()`. High reputation → `strict_eq` (a real web fetch); medium →
  `prompt_comparative` (two independent readings); low reputation or no
  history at all → `prompt_non_comparative` (the deepest, multi-step
  review). After the verdict, it writes a signed reputation delta back to
  `ReputationLedger` via `.emit()` — closing the loop, since that same
  delta will decide the strictness of this address's *next* claim.
- **DisputePanel** — if a submitter disputes a rejection, re-reviews the
  underlying claim under a differently-framed, three-way comparative
  check and writes the final correction directly to `ReputationLedger`
  (not back to `ClaimVerifier`): either fully reversing the original
  penalty, or adding a small extra penalty for a denied dispute.

Full architecture reasoning and the justification for each equivalence
principle are in [`DESIGN_DECISIONS.md`](./DESIGN_DECISIONS.md).
Live-verified GenVM findings are recorded in
[`LESSONS_LEARNED.md`](./LESSONS_LEARNED.md).

**v1.1 update:** steward review found that the medium-, low-, and
dispute-review paths judged claims against a bare evidence URL passed to
the LLM, without the contract ever actually fetching the page — so
verdicts on those paths were not grounded in real evidence content.
Fixed: every fact-checking path now calls `gl.nondet.web.render` itself
and grounds its verdict strictly in the fetched content. The review also
found `DisputePanel.review_dispute` had no caller restriction, so anyone
could invoke it directly with a fabricated claim and manipulate
reputation with no authorization at all. Fixed: `review_dispute` now
requires the caller to be the registered `ClaimVerifier` (a new
`set_claim_verifier` wiring step on `DisputePanel`) and rejects any
`claim_id` it has already processed once, independent of `ClaimVerifier`'s
own guard. See `LESSONS_LEARNED.md` and the four new regression tests in
`tests/test_claim_verifier.py`/`tests/test_dispute_panel.py` covering
both fixes.

## Deployed addresses (GenLayer Studio) — v1.1

| Contract | Address | Explorer |
|---|---|---|
| ReputationLedger | `0x3119Ca64573cdBF0865136B064c8Fa19D4C8582A` | [view](https://explorer-studio.genlayer.com/address/0x3119Ca64573cdBF0865136B064c8Fa19D4C8582A) |
| ClaimVerifier | `0x1567c2C62fAfe8a1D71D664EF6889E1BD610Af57` | [view](https://explorer-studio.genlayer.com/address/0x1567c2C62fAfe8a1D71D664EF6889E1BD610Af57) |
| DisputePanel | `0x63f2f963BF5fB602A8172942C4e5373349577d5B` | [view](https://explorer-studio.genlayer.com/address/0x63f2f963BF5fB602A8172942C4e5373349577d5B) |

These addresses replace the v1.0 deployment (`0x9544...`, `0xc280...`,
`0x08B8...`), which used contract source without the grounding and
authorization fixes described below and in `LESSONS_LEARNED.md` §8.

## Repository structure

```
contracts/
  reputation_ledger.py
  claim_verifier.py
  dispute_panel.py
tests/
  genlayer_stub/genlayer/__init__.py   # offline SDK stub (see LESSONS_LEARNED.md)
  _bootstrap.py
  test_reputation_ledger.py
  test_claim_verifier.py
  test_dispute_panel.py
  test_end_to_end.py
.github/
  workflows/
    tests.yml
DESIGN_DECISIONS.md
LESSONS_LEARNED.md
README.md
```

## Testing

Offline tests run against a small, hand-written pure-Python stub of the
`genlayer` SDK (`tests/genlayer_stub/`) rather than `genlayer-test`'s
Direct Mode, which was confirmed live in CI not to execute real
cross-contract calls at all — see `LESSONS_LEARNED.md` §7 for the full
story. No GenVM binary or network access is needed to run them. They run
automatically on every push via GitHub Actions (see
`.github/workflows/tests.yml`); results appear in the repository's
**Actions** tab. To run them locally instead:

```bash
pip install pytest
pytest tests/ -v
```

## Deployment

Use GenLayer Studio (`studio.genlayer.com`). Deployment order matters
because the contracts need each other's addresses:

1. `reputation_ledger.py` (no arguments)
2. `claim_verifier.py` with `reputation_ledger_address`, `low_threshold`
   (`300`), `high_threshold` (`700`)
3. `dispute_panel.py` with `reputation_ledger_address`
4. On `ReputationLedger`, call `set_claim_verifier` with the
   `ClaimVerifier` address
5. On `ReputationLedger`, call `set_dispute_panel` with the
   `DisputePanel` address
6. On `ClaimVerifier`, call `set_dispute_panel` with the `DisputePanel`
   address
7. On `DisputePanel`, call `set_claim_verifier` with the `ClaimVerifier`
   address (added in v1.1 — restricts `review_dispute` to only be
   callable by the real `ClaimVerifier`, see `LESSONS_LEARNED.md`)

**Important:** keep each `.py` file's header to at most 2 lines of comment
(`# v0.1.0` + `Depends`) — a longer comment block causes schema-loading to
fail. Details in `LESSONS_LEARNED.md`.

## Live end-to-end test (confirmed on Studio, v1.1)

Starting from a brand-new address (baseline reputation 500), re-verified
against the v1.1 addresses above:

1. `submit_claim` ("Paris is the capital of France.", no history) →
   `low_reputation_or_unknown` tier → `prompt_non_comparative` →
   `CONFIRMED`, with `reasoning` quoting the actually-fetched Wikipedia
   content → reputation 500 → 530.
2. `submit_claim` ("Tokyo is the capital of Japan.", reputation 530) →
   `medium_reputation` tier → `prompt_comparative` → `CONFIRMED`, with
   `grounding` quoting the actually-fetched page → reputation 530 → 550.
3. `submit_claim` ("Tokyo is the capital of France.", reputation 550) →
   `medium_reputation` tier → `prompt_comparative` → `REJECTED`,
   correctly grounded in the real (unrelated) fetched content →
   reputation 550 → 530.
4. `request_dispute` on the rejected claim → `DisputePanel` re-fetches
   the same page and re-reviews under three independent framings → all
   three agree `REJECTED` → dispute denied → extra penalty applied
   directly to `ReputationLedger` → reputation 530 → 520.

This confirms both the full feedback loop (verdicts change reputation,
and reputation changes which equivalence principle the *next* claim from
the same address is checked under) and the v1.1 fix (every verdict above
is grounded in real fetched page content, not a bare URL).
