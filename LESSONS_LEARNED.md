# Vigil — Lessons Learned (live-verified on GenLayer Studio, Sep 17 2026)

This document records only behaviors that were **live-tested and confirmed
on Studio**, not anything assumed from documentation or from Tribunal's
own `LESSONS_LEARNED.md` alone. Every constraint carried over from
AccreditationCheck/Covenant/Tribunal is listed once more here specifically
because it was reconfirmed while building Vigil, not merely copy-pasted.

## 1. All previously confirmed GenVM constraints held with no new failures

Every constraint recorded in Tribunal's `LESSONS_LEARNED.md` reconfirmed
cleanly while building and deploying Vigil, with no new violations
encountered:

- 2-line header comment limit (`# v0.1.0` + `Depends`), nothing more.
- Always `raise gl.vm.UserError(...)`, never a bare `UserError`.
- `gl.vm.run_nondet(leader_fn, validator_fn)` is positional-only.
- Constructor address inputs normalized on entry (`int`/`str`/`Address`).
- Raw `int` unsupported for persistent fields — `u256` used throughout.
- LLM JSON output stripped of markdown fences before `json.loads`.
- `gl.eq_principle.prompt_comparative(fn, principle)` — positional
  `principle`, not `task=`.
- `.view()`/`.emit()` calls made strictly outside any
  `run_nondet`/`eq_principle` block.
- `.emit()` is asynchronous — no same-transaction readback assumed
  anywhere in `ClaimVerifier` or `DisputePanel`.
- `gl.message.sender_address` inside an `.emit()`-reached contract is the
  calling contract's address — every cross-contract call in Vigil that
  needed the original human address (`ClaimVerifier` → `ReputationLedger`,
  `ClaimVerifier` → `DisputePanel`, `DisputePanel` → `ReputationLedger`)
  passes that address explicitly as a parameter rather than relying on
  `sender_address`.

No new GenVM-level constraint (beyond what Tribunal had already found) was
discovered during Vigil's build — the diagnostic-contract step planned in
`DESIGN_DECISIONS.md` §5 turned out to be unnecessary, since
`TreeMap[Address, u256]` (used for the reputation score map, a new key
type not exercised in Tribunal, which only used `TreeMap[u256, str]`)
deployed and read/wrote correctly on the first attempt with no schema
errors.

## 2. `TreeMap` keyed by `Address` works exactly like `TreeMap` keyed by `u256`

**Finding:** `ReputationLedger` declares `scores: TreeMap[Address, u256]`
and `known: TreeMap[Address, bool]` — the first time this contract set has
used a non-`u256` key type. Membership checks (`subject_addr in
self.scores`) and indexed reads/writes worked identically to the
`u256`-keyed maps in Tribunal's `PrecedentRegistry`, with no special
handling required. This was not obvious in advance (Tribunal never
exercised this path) and is worth stating plainly for future projects
that want a per-address mapping: it just works the same way.

## 3. Authorization checks on `.emit()`-reached write methods behave as expected

**Finding:** `ReputationLedger.apply_delta` checks
`gl.message.sender_address` against two stored contract addresses
(`claim_verifier`, `dispute_panel`) and rejects any other caller. This is
stricter than anything in Tribunal (whose `PrecedentRegistry.record_verdict`
and `AppealsCourt.file_appeal` have no caller restriction at all). The
check worked correctly against real cross-contract calls in both
directions (`ClaimVerifier` → `ReputationLedger` and `DisputePanel` →
`ReputationLedger`) during live testing — confirming that an
authorization check comparing `sender_address` to a previously-`set_*`
contract address is safe to rely on for cross-contract-only write methods,
not just for human-facing owner checks.

## 4. The full feedback loop was confirmed end to end on Studio

Live test sequence, starting from a fresh address (baseline reputation
500, `has_history = false`):

| Step | Call | Tier | EP | Verdict | Reputation before → after |
|---|---|---|---|---|---|
| 1 | `submit_claim` ("Paris is the capital of France.") | `low_reputation_or_unknown` | `prompt_non_comparative` | `CONFIRMED` | 500 → 530 |
| 2 | `submit_claim` ("Tokyo is the capital of Japan.") | `medium_reputation` | `prompt_comparative` | `CONFIRMED` | 530 → 550 |
| 3 | `submit_claim` ("Tokyo is the capital of France.") | `medium_reputation` | `prompt_comparative` | `REJECTED` | 550 → 530 |
| 4 | `request_dispute` on claim from step 3 | — | `prompt_comparative` (3 framings) | denied (`overturned: false`) | 530 → 520 |

This is the specific test named in `DESIGN_DECISIONS.md` §1 as the one
that would fail if the reputation loop were decorative: step 1 and step 2
are structurally the same kind of call (a true, verifiable fact) but took
two different equivalence-principle paths, purely because step 1's
verdict changed the reputation that step 2's tier selection read. The
non-comparative reasoning text returned in step 1's `EquivalenceOutputs`
and the two-reading JSON returned in step 2 are visibly different EP
shapes on-chain, not just different verdicts.

## 5. Deployment and wiring order confirmed live

`ReputationLedger` (no args) → `ClaimVerifier` (ledger address + two
thresholds) → `DisputePanel` (ledger address) → `set_claim_verifier` on
the ledger → `set_dispute_panel` on the ledger → `set_dispute_panel` on
`ClaimVerifier`. All six steps returned `SUCCESS`/`Accepted` on the first
attempt, with no reordering needed.

## 6. Final deployed addresses for Vigil (Studio, Sep 17 2026)

- `ReputationLedger`: `0x9544144caf6ACe52c5BCE6effE8b05dE0fAf0C3e`
- `ClaimVerifier`: `0xc280b1029dFB4167f24BbecC1C893137E4B432b8`
- `DisputePanel`: `0x08B886927Dc77CA1B1d745ED6DB8e54e7C08F559`

## 7. Offline test harness — final approach (genlayer-test's Direct Mode abandoned)

Getting `tests/test_offline.py` green through `genlayer-test`'s Direct
Mode took many CI round-trips and surfaced four real, confirmed findings
before hitting a wall that Direct Mode itself cannot get past:

- The installed `genlayer-test` (0.29.2, GenVM SDK v0.3.0-rc7) defaults
  to a GenVM release tag whose `genvm-universal.tar.xz` asset is
  missing (404) — fixable by pre-caching a differently-named asset from
  the same release.
- There is no `mock_web` / `mock_llm` pytest fixture; mocking is a
  method on the `direct_vm` fixture instead.
- This SDK version tracks the single most-recently-loaded contract
  class in a process-global and raises `TypeError: only one contract is
  allowed` the moment a second, different contract type is loaded in
  the same process — fixable by resetting that global between deploys.
- `ReputationLedger` and `ClaimVerifier` sharing a field name
  (`dispute_panel_set`) triggered a Direct-Mode-only storage collision
  between the two contract types.

The wall: **Direct Mode does not execute real cross-contract calls
between two independently-deployed contracts at all.**
`gl.get_contract_at(other_address).view().some_method()` was confirmed
live in CI to return `None` instead of the real value (trace:
`"Unknown gl_call request type: ['CallContract']"`), even though the
identical call against the identical deployed contracts worked
correctly against real GenVM consensus live on Studio (§4 above). This
tracks with genlayer-test's own documentation, which describes Direct
Mode as being for single-contract "Unit tests, rapid development,
CI/CD" and Studio mode for "Integration tests, consensus validation" —
it was never built to simulate a second deployed contract for a
cross-contract call to route to. Since Vigil's entire mechanism is built
on real cross-contract calls, none of it could be meaningfully exercised
in Direct Mode, no matter how the wiring or mocking code was written.

**The fix, found by reviewing a sibling project:** MatchGuard (an
earlier, single-contract project in this same series) uses a completely
different offline-testing approach: a small, hand-written, pure-Python
stub of the `genlayer` SDK's surface area (`tests/genlayer_stub/`),
imported in place of the real package for tests, with no dependency on
`genlayer-test`/GenVM at all. MatchGuard's stub never needed
`get_contract_at` (single contract), but the rest of its design —
`Address`, `TreeMap`/`DynArray`/`u256` stand-ins, no-op `@gl.public.write`/
`@gl.public.view` decorators, a settable `gl.message.sender_address`,
and `gl.nondet.web.render`/`gl.nondet.exec_prompt` raising by default so
tests must explicitly mock them via `unittest.mock.patch.object` — was
reused directly for Vigil's stub, with one addition: a small in-process
contract registry plus a call-stack-aware proxy, so that
`gl.get_contract_at(address).view()/.emit()` genuinely routes to the
other real, already-deployed contract instance and correctly simulates
GenVM's rule that `gl.message.sender_address`, inside the CALLED
contract, is the calling CONTRACT's address (not the original human
sender) — the one piece MatchGuard's single-contract stub never needed.

This fully replaced `genlayer-test`/Direct Mode for Vigil. The CI
workflow no longer installs `genlayer-test` or pre-caches any GenVM
binary — just `pip install pytest`. All 21 offline tests
(`tests/test_reputation_ledger.py`, `test_claim_verifier.py`,
`test_dispute_panel.py`, `test_end_to_end.py`) run against the real
cross-contract logic in all three contracts, including the full dispute
lifecycle (both overturned and denied outcomes) and a dedicated
end-to-end test that reproduces the exact live Studio sequence and
final scores from §4 above (500 → 530 → 550 → 530 → 520). Every test
was actually executed (not just syntax-checked) before being pushed,
using a minimal local harness standing in for `pytest.raises`, since
this environment itself has no network access to install real `pytest`.

One honest limitation of this approach, stated plainly: this stub is a
hand-written approximation of the real SDK's semantics, not the real
GenVM runtime. It is a much better offline substitute than Direct Mode
turned out to be for a multi-contract system, but the live Studio
verification in §4 remains the authoritative proof this project relies
on — the offline suite exists to catch regressions quickly, not to
replace that live verification.

`_extract_json_object` in `claim_verifier.py` and `dispute_panel.py`
still carries the small, backward-compatible `isinstance(text, dict)`
defensive branch added while debugging Direct Mode (see the prior
version of this section in git history) — harmless and never exercised
by this stub (which always returns a raw string from `exec_prompt`,
matching real GenVM), so it was left in rather than churned again.
