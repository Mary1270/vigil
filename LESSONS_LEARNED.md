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

## 7. Offline test harness — confirmed findings from CI

`tests/test_offline.py` was written, pushed, and iterated against real
GitHub Actions runs (not run locally, since this environment has no
network access) until green. What was actually found, live:

- The installed `genlayer-test` (0.29.2, GenVM SDK v0.3.0-rc7) defaults
  to a GenVM release tag whose `genvm-universal.tar.xz` asset is missing
  (404). Fixed by pre-caching the correctly-named file in
  `~/.cache/gltest-direct/` from that same release's differently-named
  `genvm-runners-all.tar.xz` asset, before pytest runs (see
  `.github/workflows/tests.yml`).
- There is no `mock_web` / `mock_llm` pytest fixture, despite this being
  a reasonable-sounding guess. Mocking is a method on the `direct_vm`
  fixture instead: `direct_vm.mock_llm(pattern, response)`, where
  `pattern` is a regex matched against the prompt text.
- This SDK version tracks the single most-recently-loaded contract class
  in a process-global and raises `TypeError: only one contract is
  allowed` the moment a second, different contract type is loaded
  anywhere in the same pytest process. Loading a second contract type
  requires manually resetting that global
  (`genlayer.gl.genvm_contracts.__known_contract__ = None`) immediately
  after each individual deploy, not just once before the test — the
  first deploy re-arms the check before the second one runs. See
  `tests/conftest.py` and the `_reset_known_contract()` helper in
  `tests/test_offline.py`.
- A more specific, Direct-Mode-only bug: `ReputationLedger` and
  `ClaimVerifier` both declare a boolean field named `dispute_panel_set`
  (each contract's own one-time wiring guard). Calling
  `ledger.set_dispute_panel(...)` immediately before
  `verifier.set_dispute_panel(...)` makes the second call fail with
  "dispute panel already set" — on a freshly-deployed verifier that had
  never had that method called before. This points to Direct Mode's
  storage simulation not fully namespacing same-named fields across
  different contract types loaded in the same process. It is not a real
  GenVM bug: the identical wiring sequence was confirmed working
  correctly against real GenVM consensus live on Studio (§4/§5 below).
  Renaming the field would only work around a test-harness artifact at
  the cost of no longer matching what is actually deployed live, so the
  contract source was left as-is and DisputePanel wiring was excluded
  from the offline suite rather than worked around. `test_offline.py`
  documents this in full at the top of the file.

Given the above, the offline suite's final scope is `ReputationLedger`
in isolation (default score, unauthorized-caller rejection, one-time/
owner-gated wiring) and the `ClaimVerifier` + `ReputationLedger`
reputation loop (tier selection driven by live reputation, both
CONFIRMED and REJECTED deltas). `DisputePanel` and the full three-contract
wiring are treated as live-verified only, per §4 below, which is the
authoritative proof for the portal submission regardless.

One more Direct-Mode-only behavior, found getting the reputation-loop
tests green: `direct_vm.mock_llm(pattern, response)` auto-parses a
JSON-shaped mocked string and delivers a Python `dict` to
`gl.nondet.exec_prompt`'s caller, instead of the raw string real GenVM
always returns (confirmed live on Studio, §4 below — every
`exec_prompt` call there returned a string, which `_extract_json_object`
then stripped and parsed). `_extract_json_object` in `claim_verifier.py`
and `dispute_panel.py` was made defensive to accept either type (`if
isinstance(text, dict): return json.dumps(text)`), purely additive and
never exercised on real GenVM, where this function only ever receives a
string. **This does mean the repo's contract source is now one small,
backward-compatible line different from the exact bytes already deployed
live at the addresses in the README** — the live deployment was
verified before this line existed and remains valid; only a future
redeployment would pick up this defensive tweak.
