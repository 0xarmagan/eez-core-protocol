# Call-pattern support and current limits

Static candidate retries do not establish a universal guarantee for all CALL,
STATICCALL and revert patterns. Rejecting inconsistent tables is required for
soundness, but a legitimate application trace can also lack a faithful table encoding.
A prover rejecting such a trace preserves safety; it does not make the trace supported.

The [characterization tests](../test/EEZExpressiveness.t.sol) distinguish these cases.
They use the local fixtures, not a production prover. Tests asserting a current
limitation are expected to pass until that limitation is fixed.

## 1. Mutable call: revert, local write, identical retry

**Decision:** mutable candidate retries are not implemented. Experiments are planned
to assess whether they are worth implementing; no implementation decision has been
made. The current support limits are documented in [Caveats](CAVEATS.md#delivery-and-execution-semantics).
The proposals and regression checklist below guide that evaluation, not committed
implementation work.

A real remote contract can implement:

```solidity
function run() external returns (uint256) {
    uint256 currentRate = IRate(msg.sender).rate();
    if (currentRate == 1) revert RateNotReady(currentRate);
    successfulCalls++;
    return currentRate;
}
```

The source application can legitimately do all of this in one transaction:

```solidity
rate = 1;
try remoteProxy.run() {} catch {} // expected application revert
rate = 2;
remoteProxy.run();               // should succeed, returning 2
```

With unchanged call identity (including gas-independent keying), the current mutable
resolver selects the first matching row on both attempts. The first attempt's revert
restores the entry cursor; nested calls also restore the host rolling hash and the
nested cursor. On L1, root updates performed by the failed frame are restored too.
The direct local write changes none of these lookup discriminators.

The second attempt executes the first row's callback, obtains rate 2, and fails its
hash check with `RollingHashMismatch`. The later success row is never tried. This is
not a missing-row example: the tests supply both alternatives, and the top-level
tests show the success alternative resolves when loaded on its own at rate 2.
Putting the success row first cannot faithfully produce the required initial failure.

Confirmed on all four mutable lookup paths:

- L1 top-level and nested.
- L2 top-level and nested with `USE_GAS_LEFT == false`.

Code: L1 `_consumeAndExecuteEntry`, `_consumeNestedCall`, `_resolveNestedReentrant`;
L2 `_findMatchingEntry`, `_consumeAndExecute`, `_consumeNestedCall`,
`_resolveNestedReentrant`. Only the STATIC resolver supports candidate retries.

An application can make retries distinguishable by changing calldata/caller or by
performing a suitable intervening committed execution, but that changes the original
trace. Refreshing tables between transactions does not solve the example within one
application invocation. Gas observation on L2 is not a universal occurrence identifier.

A future mutable-candidate retry must isolate and roll back rejected attempts:
callbacks may change state or transfer value, unlike the static callbacks.

### A successful call can also be affected by an enclosing revert

The same family of failures occurs when the remote call succeeds, but its source
parent subsequently reverts. The parent rollback undoes the successful consumption
and its root/hash/cursor updates. A catch followed by a local write and identical
retry can select the old successful row and fail its callback hash. The top-level
entry case is reproduced on L1 and L2 by
`test_ParentRevertMakesSuccessfulCallRetryReuseOldRow`; ordinary execution of the
same pattern succeeds after the catch. Those tests do not cover the successful
nested-row case below, where the outer hash can match despite an incorrect answer.

### A successful nested retry can return a stale answer without a hash mismatch

**Open source-level finding.** The successful nested resolvers on both L1 and L2
have the relevant behavior. The characterization tests do not reproduce this
specific case or establish production-proof acceptance. Concrete asset impact
also requires a complete settlement reproduction.
Track this case with the
[dedicated regression checklist](#regression-checklist-successful-nested-calls-after-parent-rollback);
it is not the accepted limitation about proving work
inside an erased frame. Here the incorrect answer affects surviving application state.

Consider one outer execution entry calling this application:

```solidity
function batch() external {
    amount = 100;
    try this.onePayment(true) {} catch {}

    amount = 5;
    this.onePayment(false);
}

function onePayment(bool failAfterward) external {
    uint256 answer = remoteProxy.f();
    if (failAfterward) revert();
    credit += answer;
}

function getAmount() external view returns (uint256) {
    return amount;
}
```

This is illustrative code, not a deployable reproduction. The intended remote
`f()` calls back to `getAmount()` and returns that amount. Both calls to `f()`
have identical call identities, including any gas discriminator; gas-independent
keying supplies that assumption. The differing local `onePayment` argument is
not passed to `f()`. Both remote calls succeed: the first is subsequently undone
by its enclosing local application call.

The outer entry contains two successful nested rows with the same lookup key K:

| Row | Callback instruction | Saved answer | `revertedOrStaticRollingHash` |
| --- | --- | --- | --- |
| R100 | `getAmount()` | 100 | Zero |
| R5 | `getAmount()` | 5 | Zero |

The callback instruction reads live state; it does not prescribe its result.

1. The first attempt selects R100, executes the callback at amount 100 and returns 100.
2. The enclosing application call reverts. Its hash updates and nested-row cursor
   advance roll back, along with its other state effects.
3. The application catches the failure and sets amount to 5. That local write
   does not change the lookup hash.
4. The second attempt selects R100 again. Its callback now reads 5, but the
   resolver still returns R100's saved answer, 100.
5. The application credits 100 and returns empty bytes. The outer hash can match
   the expected execution, whose surviving callback also reads 5.

The distinction between three return values explains the missing detection:

| Surviving observation | Intended execution | Replay using R100 | Included in the local entry hash? |
| --- | --- | --- | --- |
| Callback result | 5 | 5 | Yes, through `CALL_END` |
| Saved nested answer | 5 | 100 | No; successful `NESTED_END` has no answer |
| Outer application's return | Empty | Empty | Yes, through `CALL_END` |
| Application credit | +5 | +100 | Not directly |

The nested identity and callback identity also match. If the incorrect answer
instead changes an outer return or a later hashed call/outcome, the final hash
can detect it. This example requires its effects to remain outside the surviving
hashed observations. Hashing callback results does not hash every local state change.
A remote payout of 5 alongside local credit of 100 is a potential consequence,
not an established asset exploit.

In [EEZ](../src/EEZ.sol) and [EEZL2](../src/L2/EEZL2.sol), `_consumeNestedCall`
selects the first matching row from the current cursor. The successful branch of
`_resolveNestedReentrant` runs callbacks, requires a zero
`revertedOrStaticRollingHash`, closes the nested frame and returns the saved
`returnData`. It does not check a candidate-specific callback hash.
[EEZBase](../src/base/EEZBase.sol) hashes callback results with
`CALL_END, success, retData`, but closes a successful nested frame with
`NESTED_END` alone.

[TableGenerator](../script/blob/TableGenerator.sol) retains appended nested rows
when restoring the host hash after rollback.
[TableStitcher](../script/blob/TableStitcher.sol) advances its reconstruction row
cursor without restoring that cursor at the corresponding region boundary.
Reconstructing R100 followed by R5 therefore does not establish that the runtime
will select them in that order. Generation and stitching of this exact example
still need an executable regression.

Retrying top-level candidates only after `RollingHashMismatch` cannot by itself
address this case: both attempts occur inside one entry, and that entry can pass
its hash check. Repeating the entry with unchanged nested selection also selects
R100 again. Detection, alternative selection and failure propagation need to be
designed together; see proposal 1 and the regression checklist below.

## 2. Static results that depend on gas but have no distinguishing callbacks

A legitimate view function can return different values for different gas budgets:

```solidity
function read() external view returns (uint256) {
    return gasleft() < 400_000 ? 1 : 2;
}
```

The test confirms direct calls with gas caps 200,000 and 600,000 return 1 and 2.
In gas-independent L2 mode, the corresponding proxy reads have the same key and
cursor. Both candidate rows have empty callback arrays, so both callback hashes are
zero. Both proxy reads select the first row and return 1; the second result cannot
be selected by the callback-hash retry predicate.

This is a limit on preserving arbitrary gas-sensitive read behavior, not an argument
that a correct prover should accept contradictory rows. The prover must reject a
trace whose required outcomes the resolver cannot select. L1 also keys static reads
with zero gas; the executable reproduction here is on gas-independent L2.

More generally, speculative static callbacks consume gas and can change access
warmth. Exact gas-sensitive behavior requires separate modeling and cannot follow
from the callback-hash retry mechanism alone.

## 3. Static retries can exceed a fixed budget even with gas-insensitive results

`test_StaticRetryCanExhaustBudgetThatFitsEitherCandidateAlone` uses an expensive,
deterministic callback that returns the local rate. Both the native quote and either
correct EEZ candidate fit the supplied call cap. Trying a stale candidate first
spends that budget, and the later correct candidate fails `InsufficientCallGas`.
Reordering the rows merely changes which rate fails. The output does not depend on
`gasleft()`; the extra work from candidate search is the cause. Callers must budget
for validation attempts, not only the intended remote execution.

## Proposed improvements (not implemented)

The following three approaches are design proposals. They do not change the current
support claims above; each needs runtime, table/proof-format and regression-test
validation before it can be treated as supported behavior.

### 1. Retry mutable candidates in an isolated execution context

Extend static-style candidate search to full execution entries and nested mutable
entries on both L1 and L2:

1. Find a candidate with matching call identity and applicable root/context checks.
2. Open a revertible execution context and execute the candidate, including callbacks
   and its rolling-hash validation.
3. If the rolling hash does not match, revert that entire attempt and try the next
   candidate from the original context.
4. If it matches, accept its outcome: commit a successful execution, or roll back and
   propagate its validated application revert. If no candidate validates, reject.

The isolation boundary must include state writes, value transfers, root updates,
queue/reentrant cursors and transient hash state. Internal mismatch signaling must
be distinguishable from arbitrary application revert data. A hash-valid
`success == false` is the intended result, not permission to try a success candidate.
For successful nested rows, define a locally checkable expected hash: the current
`revertedOrStaticRollingHash` must be zero for those rows, so the existing field
cannot simply be used as their validation target.

For the stale successful-nested-answer case, evaluate both a candidate-specific
callback/frame check before returning the saved answer and a nested closing hash
that includes the answer (or its explicitly defined hash). Bind the candidate's
expected hash and saved outcome to the same remote execution in the proof/table
construction. Define matching checkpoints and seeds on both chains, and update
runtime helpers, generation, reconstruction, proof constraints and test helpers
together. These are design proposals, not a validated fix. Hashing the answer can
detect surviving answer divergence; it does not prove completion of erased work.

An application can catch a nested validation revert. Do not assume that throwing
inside the resolver necessarily reaches a whole-entry retry boundary. The chosen
design must prevent an inconsistent surviving outcome from committing while
preserving valid application reverts.

This targets mutable retries, including successful calls undone by an enclosing
revert, only where execution supplies a distinguishing validation result and can
reach the matching alternative. Reverted attempts still spend gas; this does not
resolve the fixed-budget limitation or establish exact gas-sensitive equivalence.

### 2. Optional per-call rolling-hash checks and alternative outcomes

Add an optional expected rolling hash to `L2ToL1Call` on L1, with a corresponding
field on L2's incoming-call representation. When present, the hash must match;
a mismatch causes the resolver to search for another eligible call candidate.
When absent, it adds no hash constraint. Use an explicit presence flag so that
`bytes32(0)` remains a valid expected hash.

The design must specify the checkpoint: a pre-call hash can filter candidates
before execution, while an execution-dependent hash requires running the candidate
inside the rollback context from proposal 1. A pre-call hash alone cannot distinguish
a local write that leaves the host rolling hash unchanged. Also distinguish
alternative candidates from sequential calls in an array: skipping a required
sequential call must not become a successful lookup.

Each `ExpectedL1ToL2Call` or `ExecutionEntry` would carry arrays of alternative
`success` values and `returnData`, instead of one result pair, with equivalent
changes on L2. Their lengths and indexing must agree. The validated branch must
select exactly one pair and bind it to that branch's callbacks, hash checks and,
where applicable, root/ether transitions in the proof. An explicit outcome index or
paired outcome structure can express this association; array order alone does not
explain which result is valid. An occurrence counter that rolls back with the caller
would reproduce the existing retry problem.

This allows one lookup identity to represent multiple validated outcomes. It still
requires a discriminator for those outcomes; adding arrays alone cannot distinguish
the gas-sensitive example whose callback hashes are both zero.

### 3. Include exact gas in call identity

Differentiate otherwise identical calls using gas at a precisely defined observation
point. L2 already has outgoing observed-gas keying through `USE_GAS_LEFT`; extending
this approach to L1 would strengthen the requirement that simulation and settlement
reproduce the same gas value. Define whether the discriminator is a supplied cap or
observed remaining gas: those are different quantities and must not be conflated.

This can distinguish occurrences whose gas values differ, including some reads
without callbacks. It requires agreement on the measurement point, forwarding,
wrapper overhead, access warmth and preceding execution. Candidate retries themselves
spend gas, so combining this approach with proposal 1 requires modeling that work.

Exact gas is not a universal occurrence identifier: separate calls can use the same
cap or arrive at the observation point with the same remaining gas. Conversely, a
small simulation/settlement gas difference can prevent an otherwise valid lookup.
The guarantee therefore needs an explicit supported execution model and a policy
for collisions; it cannot be stated as “every call is perfectly recognizable.”

All three proposals should be checked against the existing mutable retry,
parent-rollback, gas-sensitive static and fixed-budget counterexamples. New tests
must also cover rejected attempts leaving no state/value effects, valid application
reverts remaining terminal, and incorrect hash/outcome associations being rejected.

## Regression checklist: successful nested calls after parent rollback

- [ ] Reproduce the one-entry R100/R5 example on both L1 and L2 with identical
  nested identities. Check whether the current code commits credit 100 while
  the surviving callback reads 5. Distinguish fixture acceptance from production
  proof acceptance.
- [ ] If a fix is adopted, verify that the first attempt receives 100 before its
  enclosing rollback and that the second receives 5, credits only 5 and commits.
- [ ] Generate and stitch the table from the trace, then compare reconstructed
  occurrence order with actual runtime row selection; hand-built rows alone are
  insufficient for this integration check.
- [ ] Reverse candidate order and include rejected callbacks with writes or
  transfers. Rejected effects must roll back, and valid application reverts must
  remain terminal. Characterize the additional gas needed for candidate search.
- [ ] Have the application catch a nested validation failure. Recovery or
  rejection must prevent an inconsistent surviving outcome from committing.
- [ ] Add a control where the outer application returns the stale answer, making
  the divergence visible to the existing final hash check.
- [ ] Review EEZ/EEZL2 NatSpec and the integrity claims in the core specification
  against the implemented guarantees. A lookup key identifies a hash context,
  not a unique historical occurrence across rollback.

Do not close this case solely because top-level retry tests pass.

## DSL support versus runtime support

The current DSL supports deeper static callbacks, nested Snapshot/Revert regions
and successful children inside failed frames across parsing, generation, reverse
decoding and local execution; see
[the DSL guide](../script/blob/README.md). This does not change the core resolvers or
fix the runtime counterexamples above. Unique auto-generated DSL payloads also mean
generic call-tree scenarios do not by themselves exercise identical-calldata retries.

## Positive controls and scope

Static `revert -> local write -> success` is supported when the callback-result
hashes distinguish the alternatives. The characterization tests exercise it on
L1 and L2: rate 1
selects a hash-valid row whose cached outcome reverts; rate 2 skips that row and
selects the successful sibling. No table reload occurs between reads.

The eleven local tests cover one ordinary-execution reference, four mutable retry
counterexamples, two successful-call/parent-revert counterexamples, one gas-sensitive
static counterexample, one fixed-budget static retry counterexample, and two
successful static retry controls. They characterize runtime behavior, not production proof acceptance
or a complete classification of all possible EVM traces. They do not establish
coverage of the stale successful-nested-answer case described in section 1;
its regression checklist remains open.

The four static local-write E2Es remain NOT LIVE YET in staged/parallel network
testing and remain excluded from automatic `all` suites; see the
[E2E status note](../script/e2e/README.md).
