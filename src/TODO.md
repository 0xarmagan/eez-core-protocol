# TODO

## Follow-up reviews

- [ ] **Experiment with mutable retries and decide whether to implement them.**
      Not implemented; implementation is undecided. Keep the current support limitation in
      [Caveats](../docs/CAVEATS.md#delivery-and-execution-semantics). Use the
      [call-pattern analysis and checklist](../docs/CALL_PATTERN_LIMITS.md) to evaluate
      top-level and nested L1/L2 retries, successful calls undone by a parent revert,
      stale nested answers, rejected candidates' state/value rollback, valid application
      reverts and gas/search costs. If implementation is chosen, validate these cases
      before claiming support; experiments alone do not resolve the limitation.

## Gas

- [ ] **Meta-hook entries through the transient serializer instead of storage.** `_transientEntries` /
      `_transientStaticEntries` go to storage and back within one tx. Extend
      `ExpectedL1ToL2CallTransient` to whole entries and update the execution/static lookup
      sites. Evaluate L2 `executeIncomingCrossChainCall` separately: later entries and
      static rows must remain available to follow-up transactions in the load block,
      so an entirely transient replacement would change current behavior.
      Benchmark the savings per meta-hook batch against inline execution.
- [ ] **Look up L1 reentrant rows without copying the whole table in memory.** L1 nested lookup copies
      `expectedL1ToL2Calls` to memory before scanning. Scan the keys in place and copy only the match
      (nested CALL and STATICCALL; immediate, meta-hook and persistent tables — transient rows are
      already key-addressable). Keep cursor/rollback and empty-table behavior; no ABI, proof or
      storage-layout change. L2 already scans its expected-call table in storage. Benchmark how L1 copying
      scales with lookup count and total table size, along with gas and bytecode.
      Do after the item above.

- [ ] **Budget L2-to-L1 calls and return-data processing.** Measure total per-entry
      gas and memory: call count, calldata, proxy creation/forwarding, nested callbacks,
      and copying/hashing returned values or revert messages. Include candidate attempts
      when measuring execution costs and cover the corresponding L2 incoming-call paths.
      Define supported size/gas budgets; any limits must agree with proof/execution rules.
      No fixed on-chain limits are selected. Event-log measurements are tracked separately below.

## Benchmarks

- [ ] **Benchmark execution log payloads (L1/L2).** Events carry full return data; L2 also logs the
      whole loaded table. Measure payload size and gas for the current event interface.

## Bytecode

The current local artifacts report EEZ runtime 23,942 B of the 24,576 B EIP-170 limit
(634 B headroom) and EEZL2 runtime 14,017 B. EEZ creation bytecode is 25,618 B
before constructor arguments, below the 49,152 B initcode limit. The compiler is tuned for size
(`optimizer_runs = 1`, via-IR), so savings need config or source changes.

The per-region figures below are historical estimates, not a breakdown of the current
artifacts. Remeasure them before estimating savings for the current build.
Regions share helpers, so the deltas overlap:

| Region | Bytes |
|---|---|
| `postAndVerifyBatch` subsystem (validation 1,814 · verify 1,901 · vkeys 610 · save remainder 518 · transient pushes 393) | 9,681 |
| `_processL2ToL1Calls` | 1,721 |
| Embedded `CrossChainProxy` creation code (data block) | 1,458 |
| `ExpectedL1ToL2CallTransient` serializer | 1,140 |
| Nested path (`_consumeNestedCall`, `_resolveNestedReentrant`, `_getExpectedL1toL2Calls`) | 1,122 |
| Consume/match (`_consumeAndExecuteEntry`, `_findMatchingEntry`, `_entryMatches`) | 1,094 |
| `_executeEntry` | 1,021 |
| Static read path | 975 |
| `executeCrossChainCall` | 939 |
| `registerRollup` + `setRoot` + views | 746 |
| Revert-span machinery | 318 |
| CBOR metadata trailers (EEZ + embedded proxy initcode) | 107 |

The dominant cost is ABI machinery for the nested batch calldata struct: decoding, per-entry
`abi.encode` hashing, and full struct copies into storage / transient tables.

- [ ] **Drop CBOR metadata — measure the savings.** In `foundry.toml` `[profile.default]`:
      `bytecode_hash = "none"`, `cbor_metadata = false`. Explorers lose the embedded IPFS source
      hash; verification by compiler settings still works. Check the proxy init-code hash impact.

- [ ] **Stop embedding the proxy initcode.** Move `CrossChainProxy` creation code into
      a separate helper so EEZ and EEZL2 no longer embed it. Preserve proxy behavior and
      measure bytecode savings and deployment gas.

- [ ] **Move batch validation + proof verification to an external library — estimated 3.5–4 KB.**
      `_validateBatchStructure`, `_verifyProofSystemBatch`, `_getVerificationKeysPerRollup` are
      self-contained `view` logic over the calldata batch (`rollups` passes as a storage pointer).
      Cost: one extra deployment plus a DELEGATECALL per post. Use when the two above are not enough.
