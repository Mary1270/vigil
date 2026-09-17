# Vigil — Design Decisions

## 0. Context and goal

Fourth project, targeting 500–600 points in the portal's Intelligent
Contracts category. Previous projects: AccreditationCheck (accepted, 100
points), Covenant (three independent escrow contracts, each with its own
equivalence principle), Tribunal (a three-contract adjudication pipeline
with real precedent memory — FirstInstanceCourt, AppealsCourt,
PrecedentRegistry — all deployed and live-tested end to end). The lesson
carried forward from all three: a clear narrative plus a genuinely new
mechanism plus real live execution earns points — not contract count, and
not a reskinned version of a mechanism already used. Vigil must not be
Tribunal's cross-contract wiring with new names on the same shape.

## 1. The new mechanism (before writing any code)

Covenant's axis for choosing consensus strictness was *type of evidence*.
Tribunal's axis was *the value/importance of what is being judged* (claim
amount). Both of those axes are exogenous: nothing the contracts do changes
the input that drives EP selection on the next case.

Vigil's axis is different in kind, not just in label: **the track record of
the claim submitter itself**, expressed as a live, mutable reputation score
that the system updates based on its own verdicts, and which then feeds
back into the strictness of the *next* verdict for that same address. This
is a closed feedback loop — judgment output becomes judgment input — rather
than a fixed lookup table keyed by an external property of the claim. A
submitter with a strong record is checked lightly; a bad record (or no
history at all) is checked harder; and the outcome of that very check
adjusts the score that will decide how the submitter's *next* claim is
handled. Nothing in Covenant or Tribunal has this self-adjusting property:
their strictness tiers never change as a side effect of the contracts'
own verdicts.

What would make this a fake/decorative field rather than a real mechanism,
and why it isn't here: if the reputation score were only stored but never
read before EP selection, it would be an inert counter (exactly the kind
of "hello-world with a fake state field" the portal penalizes). Vigil's
score is read via a real `.view()` on `ReputationLedger` *before* the EP
branch is chosen, and the branch chosen is what determines which
equivalence principle a given claim goes through. The end-to-end test that
would fail if this loop were fake: submit two structurally identical claims
from two addresses with different starting reputations, and assert that
they take two different equivalence-principle paths — not that they reach
the same eventual answer by coincidence, but that the *path itself*
differs and is traceable to reputation state.

Why three contracts and not one contract with an internal mapping: the same
reasoning as Tribunal §1 — `ClaimVerifier` alone is just "an oracle with
tiered strictness," a shape already used for FirstInstanceCourt.
`ReputationLedger` alone is a passive, meaningless store.
`DisputePanel` alone has nothing to correct without a ledger and a verifier
feeding it. What makes the three non-trivial as a system is that each one's
behavior is contingent on the others actually existing and actually being
called — not adjacency for its own sake.

## 2. The three contracts and the justification for each equivalence principle

### ClaimVerifier — reputation-gated strictness, three paths, three EPs

**What it does:** registers a claim together with a fact to verify. Before
selecting an EP, it calls `ReputationLedger` with a real `.view()` (outside
any `run_nondet`/`eq_principle` block) to read the submitter's current
reputation score. Based on that score:
- **High reputation:** `strict_eq` against a real web fetch — the
  submitter has earned the cheapest, most literal check.
- **Medium reputation:** `prompt_comparative` — two independent readings
  of the same fact, results compared.
- **Low reputation or no history at all:** `prompt_non_comparative` — a
  deep, multi-step leader/validator review, the strictest path available.

After the verdict, it sends a real `.emit()` delta back to
`ReputationLedger` — positive if the claim was confirmed, negative if it
was rejected. This emit is what closes the loop: the same address's next
claim will be read against an already-updated score.

**Why this axis and not amount or evidence type:** amount (Tribunal) and
evidence type (Covenant) are both properties of the *claim*. Reputation is
a property of the *claimant*, carried across claims and mutated by the
system's own history of verdicts on that address. This is a materially
different dimension of the same underlying principle ("consensus
strictness should match what is actually being judged"), which is what
shows the principle generalizes rather than being an escrow-specific or
dispute-specific trick.

### ReputationLedger — no EP of its own, the system's connective tissue

**What it does:** holds a per-address reputation score (new addresses
start at a neutral baseline), exposes a real `.view()` to read the current
score, and a real `.emit()` to apply a signed delta.

**Why no EP:** this contract performs no judgment — reading is a
deterministic lookup, writing is deterministic arithmetic on a delta it is
told to apply. Imposing an equivalence principle on pure arithmetic would
be exactly the kind of decorative-EP pattern the portal rejects. Its value,
like `PrecedentRegistry` in Tribunal, is architectural rather than
computational: it is the single piece of shared, mutable state that makes
the feedback loop real. Unlike `PrecedentRegistry`, which is an
append-only log, `ReputationLedger` holds a value that is actively
overwritten — a different kind of shared state than anything in Tribunal.

### DisputePanel — a different framing, corrects the ledger, not the verifier

**What it does:** if a submitter disputes a reputation penalty they
received, `DisputePanel` re-reviews the underlying claim under a stricter
or differently-framed criterion than the one `ClaimVerifier` used. It can
confirm or reverse the penalty. Either way, it writes the final outcome
with a real `.emit()` directly to `ReputationLedger` — **not** back to
`ClaimVerifier`.

**Why the write target matters:** the object being corrected is shared
reputation state, not the original verifier's internal record (it doesn't
keep one). Writing anywhere except `ReputationLedger` would make the appeal
cosmetic — it has to touch the same state that fed the original decision
and will feed the next one, or the loop described in §1 is broken.

**Why a different framing than ClaimVerifier, not the same prompt rerun:**
mirrors the reasoning in Tribunal's AppealsCourt — a review that reuses the
first reviewer's exact framing is a re-vote with the same error profile,
not an independent check.

## 3. What is deliberately not built

Per the portal's exclusion list: no thin "ask an LLM about X" wrapper, no
EP bolted onto `ReputationLedger` for the sake of having three EPs, no
fourth contract added only to raise the contract count. `ReputationLedger`
is simple, but its role is explicitly argued above, not assumed.

## 4. Confirmed GenVM constraints inherited from AccreditationCheck, Covenant, and Tribunal (not to be rediscovered)

- Contract file headers are exactly two comment lines, nothing more:
  ```
  # v0.1.0
  # { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
  ```
  A single additional comment line immediately after causes
  `VM_ERROR: invalid_contract` with completely empty stdout/stderr. Any
  design commentary goes in this file, never in the contract header.
- Always `raise gl.vm.UserError(...)`, never a bare `UserError`.
- `gl.vm.run_nondet(leader_fn, validator_fn)` — positional-only, no
  keyword arguments.
- Every validator calls `gl.vm.unpack_result()` on the leader's result
  before using it, as the first line of the validator.
- Constructor address inputs are normalized on entry — they may arrive as
  a raw `int` or `str`, not only `Address`.
- Raw `int` is not supported for persistent fields — use `u256`/`i32`/
  the appropriate fixed-width or bigint type.
- LLM JSON output is stripped of markdown code fences before
  `json.loads`.
- `gl.eq_principle.prompt_comparative(fn, principle)` — second argument is
  positional, named `principle` (not the documented `task=` keyword; the
  live signature was confirmed against a real error).
- `gl.eq_principle.prompt_non_comparative(fn, *, task, criteria)` —
  keyword-only `task`/`criteria`, and the documentation for this one is
  accurate.
- Every `gl.get_contract_at(...).view()` / `.emit()` call happens strictly
  outside any `run_nondet`/`eq_principle` block. Calling either from
  inside a `leader_fn`/`validator_fn` raises `SystemError: 6: forbidden`
  — a VM-level trap, not catchable with `try/except`.
- `.emit()` (with or without arguments, or `emit_transfer`) is
  asynchronous — an immediate same-transaction `.view()` on the
  written-to contract still returns the pre-write value. No contract
  logic assumes an immediate readback of its own write.
- `gl.message.sender_address`, inside a contract reached via `.emit()`,
  is the address of the calling *contract*, not the original human
  sender. Any contract that needs the original sender's address (e.g.
  `ReputationLedger` crediting the right human account) must receive
  that address explicitly as part of the passed data.
- `gl.message.value` is returned with 18 decimals (wei-style).
- Time source is `https://www.cloudflare.com/cdn-cgi/trace` (the `ts=`
  line); `worldtimeapi.org` is permanently sunset (HTTP 410).
- GEN transfer from within a contract:
  `gl.get_contract_at(addr).emit_transfer(value=amount)`.

## 5. What still needs live verification in Studio before relying on it

Vigil's reputation loop does not obviously need a cross-contract shape
beyond the `.view()` / `.emit()` pattern already confirmed live in
Tribunal (§5 of Tribunal's own `DESIGN_DECISIONS.md`). If implementation
turns up a need for something genuinely new — for example, aggregating or
comparing more than one reputation score in a single call, or any
array-of-scores read — a small diagnostic contract pair will be deployed
live on Studio to confirm its exact behavior before it is relied on inside
`ClaimVerifier`, `ReputationLedger`, or `DisputePanel`. Nothing new is
assumed from documentation alone; only what Tribunal already confirmed
live is treated as settled going in.

## 6. Status

Design finalized. No contract code written yet — next step is
`ClaimVerifier`, `ReputationLedger`, and `DisputePanel`, in that
dependency order reversed at deploy time (`ReputationLedger` first, since
the other two depend on its address).
