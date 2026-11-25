# Chain Observer v1.3 — Transformation & Property Integrity Prototype

A small, rule-based Python prototype that extends epistemic claim tracking with explicit transformation lineage and declared property-integrity checks.

## What is new in v1.3
- Records input claim, output claim, operation, actor, conditions, timestamps and property snapshots.
- Checks declared property-preservation rules (`mode: preserve`) and explicit output constraints (`mode: constraint`).
- Reports each check as `PASS`, `VIOLATION` or `UNDETERMINED`.
- Missing or incomparable properties are never silently treated as a pass.
- A declared violation marks the transformation `REVIEW_REQUIRED`.
- If a caller requests `[R]` for an output whose property check is a violation or undetermined, the output is downgraded to `[H]` or `[U]` respectively.
- Retains the v1.2 claim registry, epistemic labels, guarded status transitions, verification-attestation record, conflict candidates and JSON snapshot.

## Important interpretation
A changed property is not automatically an error. A transformation may legitimately change some properties. The observer flags a change only when it violates an explicitly declared invariant or constraint.

`VIOLATION` means a declared rule was breached. It does not mean the output is universally false.
`UNDETERMINED` means the necessary property is missing or cannot be compared. It is not a pass.
`PASS` means the declared rules that were supplied were satisfied. It does not prove that the rule set was complete or appropriate.

## Scope limits
This prototype evaluates explicit, machine-readable property values. It does not infer arbitrary semantic properties from natural language, independently authenticate sources, establish scientific truth, or decide which properties ought to be invariant. Those require domain-specific adapters, justified rule definitions and independent validation.

The prototype is a standalone demonstrator, not the complete underlying architecture.

## Requirements and run
- Python 3.9+
- Standard library only

```bash
python -m unittest -v
python chain_observer_v1_3.py
```

## Test domains
The test suite includes:
- Mathematical transformations (rotation, scaling)
- AI summarization / rewriting (entity retention, negation, certainty)
- Scientific measurement and unit conversion
- Data filtering / aggregation constraints
- Translation and communication intent preservation
- Missing and incomparable property values
- v1.2 compatibility and lineage integrity

## Example rule
```python
{"property": "norm", "mode": "preserve", "tolerance": 1e-9}
```

Or a constraint on an output:
```python
{"property": "row_count", "mode": "constraint",
 "comparator": "lte", "expected": 100}
```


## V1.3 review hardening (post-independent review)

The reviewed build adds the following targeted corrections without changing the
rule-based scope of the prototype:

- **Boolean preservation:** booleans use logical equality, never numeric
  tolerance. A `True` → `False` change is therefore a violation even if a
  non-zero numeric tolerance is configured.
- **Rule configuration validation:** malformed modes, comparators, tolerances,
  and rule structures raise `RuleConfigurationError` before any output claim
  or transformation is recorded. This is distinct from `UNDETERMINED`, which
  remains reserved for missing or incomparable property evidence. An omitted
  or empty rule list still produces `UNDETERMINED`, never `PASS`.
- **Explicit review override:** a claim produced by a transformation marked
  `REVIEW_REQUIRED` cannot silently be promoted to `[R]` by
  `apply_transition`. A deliberate override requires `override_review=True`
  and still requires a matching source reference and verification attestation;
  the transition is recorded in the event log.
- **Defensive copying:** nested property/condition/rule structures are copied
  at ingestion and when snapshots/events are created, preventing mutation of
  caller-owned nested objects from retroactively changing the recorded trail.
- **NaN policy:** under `preserve`, NaN → NaN is treated as unchanged; NaN ↔
  non-NaN is a violation. This is a deliberate comparison policy for this
  prototype.

### Result semantics

- `PASS`: all declared checks passed.
- `VIOLATION`: at least one declared property rule was breached.
- `UNDETERMINED`: checks are absent, or required property evidence is missing
  or incomparable.
- `RuleConfigurationError`: the rule definition itself is malformed or
  unsupported. It is an exception raised before observer state is mutated,
  not a data-check result.

A `REVIEW_REQUIRED` transformation is not automatically cleared by a later
status transition. An operator may explicitly override the review gate, but
must provide fresh verification evidence and leave an auditable transition.

### Scope remains unchanged

This remains a rule-based demonstrator. It does not extract semantic
properties from arbitrary text, decide whether a declared invariant is
appropriate, authenticate source truth, or provide tamper-proof storage
against a caller with direct access to the observer's internal Python objects.
