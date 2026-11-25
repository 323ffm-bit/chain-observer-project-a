# Chain Observer — Project A

A portable, bounded Python demonstrator for tracking claims, transformations, context relations, references, and an auditable event history.

Project A continues the public Chain Observer V1.3 baseline. The original V1.3 repository and its files are not modified by this working build.

## Scope

The observer records and checks **explicit, machine-readable information**. It does not infer arbitrary semantic meaning from natural language, independently authenticate source truth, decide which properties ought to be invariant, or prove that a recorded relation is semantically correct.

Context relations are recorded assessments, not proof of semantic understanding. `PROVISIONAL`, `CONFIRMED`, `REJECTED`, and `UNRESOLVED` are assessment states; relation kinds (`primary`, `analogy`, `example`, `contrast`, `transfer`, `topic_shift`) remain a separate dimension.

`PASS` means only that the declared property checks passed. It does not prove that the rules were complete or appropriate. Missing or incomparable evidence is `UNDETERMINED`, never silently `PASS`.

## Six logical module areas

The implementation uses six responsibility areas. They are not split into more physical modules unless later evidence shows that a responsibility needs independent ownership.

1. `observer.py` — public API and orchestration.
2. `models.py` — shared data models and aggregate state.
3. `events.py` — event envelope, ordering, append-only API, and legacy event projection.
4. `references.py` — reference markers and resolution.
5. `context.py` — context frames, contextual relations, assessment, and feedback lifecycle.
6. `rules.py` — V1.3 property-rule validation and evaluation.

`chain_observer_v1_3.py` is a compatibility import surface, not a seventh functional module.

## Key integrity decisions

- **Copy-on-write operations:** each write prepares a complete draft state, validates and appends all required events to that draft, and swaps the aggregate state once. If an operation fails, neither partial state nor partial events are committed.
- **API-level append-only events:** event reads return copies. The public API exposes read-only/copy views rather than mutable internal dictionaries and lists.
- **No false immutability claim:** this is an in-memory Python prototype. A caller deliberately modifying private internals can still alter memory. `validate_integrity()` detects several classes of inconsistency; it is not cryptographic tamper protection or durable storage.
- **Canonical and legacy event shape:** events carry the new envelope (`event_type`, `sequence`, `actor_type`, `actor_id`, `subject_ref`, `references`, `payload`, `previous_event_id`) while retaining V1.3 fields (`event`, `claim_id`, `details`). Legacy names such as `STATUS_TRANSITION` and `CONFLICT_CANDIDATE_RECORDED` remain in the compatibility field.
- **Ordered chain:** sequence and `previous_event_id` define order; timestamps are descriptive, not the ordering authority.
- **Backward references only:** event references must resolve to objects/events that existed before the event was appended. A newly created object may be the event's subject; it is not treated as a pre-existing reference.
- **Initial relation assessment:** `CONTEXT_RELATION_RECORDED` contains the initial assessment status. Later changes append `CONTEXT_RELATION_ASSESSED`; current assessment is derived from the event history.
- **Reference dimensions stay separate:** marker resolution, external source availability, and source authenticity/verification are not conflated.
- **Feedback is bounded:** each request can have only one terminal event (`FEEDBACK_RESOLVED` or `FEEDBACK_CANCELLED`). A renewed request must explicitly reference the earlier request.

## Compatibility

The V1.3-facing API remains available:

- `VerificationAttestation.create(...)`
- `register_claim(...)`
- `apply_transition(...) -> bool`
- `evaluate_transition(...)` as a backward-compatible alias
- `record_transformation(...)`
- `record_conflict_candidate(...)`
- `snapshot()` and `export_json()`

The legacy snapshot keys remain present: `prototype`, `claims`, `transformations`, `events`, `conflict_candidates`, and `scope_note`. New context/reference/feedback data are additive.

Read access to `registry`, `events`, `transformations`, `conflicts`, `markers`, `contexts`, and `relations` returns copies or read-only mappings. Code that previously mutated these internals directly must use the observer API instead. This is an intentional integrity boundary.

## Run

- Python 3.9+
- Standard library only

From the project root (the directory containing the `chain_observer_v1_3` package):

```bash
python -m unittest discover -v
```

A compact example:

```python
from chain_observer_v1_3 import ChainObserver

o = ChainObserver()
o.register_claim("c1", "Lil Red Dots", "[K]")
o.create_context("ctx1", "Lil Red Dots", "human")
o.activate_context("ctx1")
o.record_context_relation(
    "rel1", "c1", "ctx1", "analogy", "human",
    domain_label="construction",
    initial_assessment_status="PROVISIONAL",
)
print(o.validate_integrity())
```

## Tests

The suite retains all 31 V1.3 regression tests and adds Project A tests for context anchoring, cross-domain relation recording, reference resolution, feedback lifecycle, immutable read views, event ordering, transition provenance, and atomic rollback behavior.

## Explicit non-goals for this build

- No natural-language semantic classifier.
- No automatic topic-shift or analogy inference.
- No numeric confidence scores.
- No source-authenticity verification.
- No database, persistence, import/recovery, concurrency, or cryptographic hash-chain guarantee.
- No connection to any external user's environment or data.
