"""Public API and orchestration for the portable Chain Observer demonstrator."""
from __future__ import annotations

from dataclasses import asdict
from copy import deepcopy
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Optional
import json
import uuid

from .models import (ObserverState, VerificationAttestation, EpistemicClaim,
                     ContextFrame, ContextRelation, ReferenceMarker, FeedbackRequest)
from .events import EventStore, EventValidationError, utc_now
from .rules import RuleConfigurationError, validate_rules, evaluate_rules
from .references import register_marker, resolve_marker
from .context import (create_context, activate_context, record_relation, assess_relation,
                      current_relation_assessment, request_feedback, resolve_feedback,
                      cancel_feedback)

VALID_STATUSES = {"[R]", "[K]", "[I]", "[H]", "[U]"}


class ChainObserver:
    """A rule-based observer with event-backed context and controlled read access.

    The aggregate is updated copy-on-write: each operation validates and prepares
    a complete draft, records its events, then swaps one state reference. This
    gives the in-memory prototype atomic logical operations without claiming
    durable or cryptographically tamper-proof storage.
    """

    def __init__(self) -> None:
        self._state = ObserverState()

    @staticmethod
    def _validate_nonempty(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid epistemic status {status!r}.")

    @staticmethod
    def _verification_dict(value):
        return asdict(value) if value else None

    @staticmethod
    def _claim_dict(claim: EpistemicClaim) -> dict[str, Any]:
        return asdict(claim)

    @property
    def registry(self):
        return MappingProxyType(deepcopy(self._state.registry))

    @property
    def events(self) -> list[dict[str, Any]]:
        return EventStore.export_events(self._state)

    @property
    def transformations(self):
        return MappingProxyType(deepcopy(self._state.transformations))

    @property
    def conflicts(self) -> list[dict[str, Any]]:
        return deepcopy(self._state.conflicts)

    @property
    def markers(self):
        return MappingProxyType(deepcopy(self._state.markers))

    @property
    def contexts(self):
        return MappingProxyType(deepcopy(self._state.contexts))

    @property
    def relations(self):
        return MappingProxyType(deepcopy(self._state.relations))

    @property
    def feedback_requests(self):
        return MappingProxyType(deepcopy(self._state.feedback_requests))

    @property
    def active_context_id(self) -> Optional[str]:
        return self._state.active_context_id

    @staticmethod
    def _ensure_reference_exists(state: ObserverState, ref: dict[str, str]) -> None:
        kind, identifier = ref.get("type"), ref.get("id")
        stores = {
            "claim": state.registry, "context": state.contexts, "relation": state.relations,
            "marker": state.markers, "feedback_request": state.feedback_requests,
            "transformation": state.transformations,
        }
        if kind == "event":
            exists = any(event["event_id"] == identifier for event in state.events)
        elif kind in stores:
            exists = identifier in stores[kind]
        elif kind == "external_source":
            exists = isinstance(identifier, str) and bool(identifier.strip())
        else:
            exists = False
        if not exists:
            raise KeyError(f"Event reference {kind!r}:{identifier!r} does not resolve at creation time.")

    @staticmethod
    def _append_event(state, event_type, subject_type, subject_id, payload,
                      actor_type="system", actor_id=None, references=None,
                      legacy_event_name=None):
        refs = references or []
        for ref in refs:
            ChainObserver._ensure_reference_exists(state, ref)
        return EventStore.append(state, event_type, {"type": subject_type, "id": subject_id},
                                 payload, actor_type, actor_id, refs,
                                 legacy_event_name=legacy_event_name)

    def register_claim(self, claim_id: str, content: str, status: str,
                       source_ref: Optional[str] = None,
                       verification: Optional[VerificationAttestation] = None,
                       properties: Optional[dict[str, Any]] = None,
                       actor_type: str = "human", actor_id: str = "operator") -> EpistemicClaim:
        self._validate_nonempty(claim_id, "claim_id")
        self._validate_nonempty(content, "content")
        self._validate_status(status)
        if claim_id in self._state.registry:
            raise ValueError(f"Claim ID {claim_id!r} already exists.")
        if source_ref is not None:
            self._validate_nonempty(source_ref, "source_ref")
            source_ref = source_ref.strip()
        if properties is not None and not isinstance(properties, dict):
            raise ValueError("properties must be a dictionary.")
        if verification is not None and not isinstance(verification, VerificationAttestation):
            raise TypeError("verification must be a VerificationAttestation or None.")
        if status == "[R]":
            if not source_ref or verification is None:
                raise ValueError("[R] requires source_ref and verification attestation.")
            if verification.source_ref != source_ref:
                raise ValueError("Attestation source_ref must match claim source_ref.")
        now = utc_now()
        claim = EpistemicClaim(claim_id, content.strip(), status, source_ref, verification,
                               deepcopy(properties or {}), now, now)
        draft = deepcopy(self._state)
        draft.registry[claim_id] = claim
        self._append_event(draft, "CLAIM_REGISTERED", "claim", claim_id,
                           {"content": claim.content, "status": status, "source_ref": source_ref,
                            "properties": deepcopy(claim.properties),
                            "verification_attestation": self._verification_dict(verification)},
                           actor_type, actor_id)
        self._state = draft
        return deepcopy(claim)

    def apply_transition(self, claim_id: str, new_status: str, reason: str,
                         source_ref: Optional[str] = None,
                         verification: Optional[VerificationAttestation] = None,
                         override_review: bool = False,
                         trigger_marker_ids: Optional[list[str]] = None,
                         evidence_marker_ids: Optional[list[str]] = None,
                         reasoning_marker_id: Optional[str] = None,
                         validation_result: str = "PASS") -> bool:
        if claim_id not in self._state.registry:
            raise KeyError(f"Claim {claim_id!r} not found.")
        self._validate_status(new_status)
        self._validate_nonempty(reason, "reason")
        if validation_result not in {"PASS", "VIOLATION", "UNDETERMINED"}:
            raise ValueError("validation_result must be PASS, VIOLATION or UNDETERMINED.")
        trigger_ids, evidence_ids = list(trigger_marker_ids or []), list(evidence_marker_ids or [])
        all_markers = trigger_ids + evidence_ids + ([reasoning_marker_id] if reasoning_marker_id else [])
        for marker_id in all_markers:
            if marker_id not in self._state.markers:
                raise KeyError(f"Transition marker {marker_id!r} not found.")
        claim = self._state.registry[claim_id]
        old_status, old_source, old_verification = claim.status, claim.source_ref, claim.verification
        blocking = [item for item in self._state.transformations.values()
                    if item["output_claim_id"] == claim_id and item["disposition"] == "REVIEW_REQUIRED"]
        blocked_reason = None
        if new_status == "[R]":
            if validation_result != "PASS":
                blocked_reason = f"[R] promotion requires PASS validation; received {validation_result}."
            elif blocking and not override_review:
                blocked_reason = "A prior transformation requires review; explicit override_review=True is required."
            elif not source_ref or verification is None:
                blocked_reason = "Source reference and verification attestation required."
            elif not isinstance(verification, VerificationAttestation):
                raise TypeError("verification must be a VerificationAttestation.")
            elif verification.source_ref != source_ref:
                raise ValueError("Attestation source_ref must match transition source_ref.")
        else:
            source_ref, verification = None, None
        if blocked_reason:
            draft = deepcopy(self._state)
            refs = [{"type": "marker", "id": mid} for mid in all_markers]
            self._append_event(draft, "TRANSITION_BLOCKED", "claim", claim_id,
                               {"from_status": old_status, "to_status": new_status,
                                "from": old_status, "to": new_status,
                                "reason": blocked_reason,
                                "blocking_transformations": [x["transformation_id"] for x in blocking]},
                               "system", "observer", refs)
            self._state = draft
            return False
        draft = deepcopy(self._state)
        previous_transition = next((e["event_id"] for e in reversed(draft.events)
                                    if e["event_type"] == "STATE_TRANSITION"
                                    and e["subject_ref"] == {"type": "claim", "id": claim_id}), None)
        updated = EpistemicClaim(claim.claim_id, claim.content, new_status, source_ref, verification,
                                 deepcopy(claim.properties), claim.created_at, utc_now())
        draft.registry[claim_id] = updated
        refs = [{"type": "marker", "id": mid} for mid in all_markers]
        if previous_transition:
            refs.append({"type": "event", "id": previous_transition})
        self._append_event(draft, "STATE_TRANSITION", "claim", claim_id,
                           {"from_status": old_status, "to_status": new_status,
                            "from": old_status, "to": new_status, "reason": reason.strip(),
                            "previous_source_ref": old_source,
                            "previous_verification_attestation": self._verification_dict(old_verification),
                            "current_source_ref": source_ref,
                            "current_verification_attestation": self._verification_dict(verification),
                            "override_review": bool(override_review),
                            "trigger_marker_ids": trigger_ids,
                            "evidence_marker_ids": evidence_ids,
                            "reasoning_marker_id": reasoning_marker_id,
                            "validation_result": validation_result,
                            "previous_transition_event_id": previous_transition},
                           "human", "operator", refs, "STATUS_TRANSITION")
        self._state = draft
        return True

    # V1.2/V1.3 backward-compatible alias.
    def evaluate_transition(self, *args, **kwargs) -> bool:
        return self.apply_transition(*args, **kwargs)

    def record_transformation(self, transformation_id: str, input_claim_id: str,
                              output_claim_id: str, operation: str,
                              conditions: Optional[dict[str, Any]] = None,
                              output_content: Optional[str] = None,
                              output_status: str = "[I]",
                              output_source_ref: Optional[str] = None,
                              output_verification: Optional[VerificationAttestation] = None,
                              output_properties: Optional[dict[str, Any]] = None,
                              rules: Optional[list[dict[str, Any]]] = None,
                              actor: str = "operator") -> dict[str, Any]:
        for value, field in ((transformation_id, "transformation_id"), (input_claim_id, "input_claim_id"),
                             (output_claim_id, "output_claim_id"), (operation, "operation"), (actor, "actor")):
            self._validate_nonempty(value, field)
        self._validate_status(output_status)
        if transformation_id in self._state.transformations:
            raise ValueError(f"Transformation ID {transformation_id!r} already exists.")
        if input_claim_id not in self._state.registry:
            raise KeyError(f"Input claim {input_claim_id!r} not found.")
        if output_claim_id in self._state.registry:
            raise ValueError(f"Output claim ID {output_claim_id!r} already exists.")
        if output_properties is not None and not isinstance(output_properties, dict):
            raise ValueError("output_properties must be a dictionary.")
        if conditions is not None and not isinstance(conditions, dict):
            raise ValueError("conditions must be a dictionary.")
        if output_content is not None:
            self._validate_nonempty(output_content, "output_content")
        if output_source_ref is not None:
            self._validate_nonempty(output_source_ref, "output_source_ref")
            output_source_ref = output_source_ref.strip()
        if output_verification is not None and not isinstance(output_verification, VerificationAttestation):
            raise TypeError("output_verification must be a VerificationAttestation or None.")
        validated_rules = validate_rules(rules)
        source = self._state.registry[input_claim_id]
        props_after = deepcopy(output_properties or {})
        checks, overall = evaluate_rules(source.properties, props_after, validated_rules)
        if overall != "PASS" and output_status == "[R]":
            output_status = "[H]" if overall == "VIOLATION" else "[U]"
            output_source_ref, output_verification = None, None
        if output_status == "[R]":
            if not output_source_ref or output_verification is None:
                raise ValueError("[R] output requires source_ref and verification attestation.")
            if not isinstance(output_verification, VerificationAttestation) or output_verification.source_ref != output_source_ref:
                raise ValueError("Attestation source_ref must match output_source_ref.")
        now = utc_now()
        output = EpistemicClaim(output_claim_id,
                                (output_content or f"Output of {transformation_id}").strip(),
                                output_status, output_source_ref, output_verification,
                                props_after, now, now)
        record = {"transformation_id": transformation_id, "input_claim_id": input_claim_id,
                  "output_claim_id": output_claim_id, "operation": operation,
                  "conditions": deepcopy(conditions or {}), "actor": actor,
                  "timestamp": now, "input_properties": deepcopy(source.properties),
                  "output_properties": deepcopy(props_after), "property_checks": deepcopy(checks),
                  "overall_check": overall,
                  "disposition": "REVIEW_REQUIRED" if overall != "PASS" else "RULES_SATISFIED"}
        draft = deepcopy(self._state)
        draft.registry[output_claim_id] = output
        draft.transformations[transformation_id] = record
        self._append_event(draft, "CLAIM_REGISTERED", "claim", output_claim_id,
                           {"content": output.content, "status": output.status,
                            "source_ref": output.source_ref, "properties": deepcopy(output.properties),
                            "verification_attestation": self._verification_dict(output.verification)},
                           "system", "observer")
        self._append_event(draft, "TRANSFORMATION_RECORDED", "transformation", transformation_id,
                           record, "human", actor,
                           [{"type": "claim", "id": input_claim_id}, {"type": "claim", "id": output_claim_id}])
        self._state = draft
        return deepcopy(record)

    def record_conflict_candidate(self, claim_id_a: str, claim_id_b: str,
                                  reported_by: str, note: str) -> dict[str, Any]:
        if claim_id_a == claim_id_b:
            raise ValueError("A conflict candidate must reference two different claims.")
        if claim_id_a not in self._state.registry or claim_id_b not in self._state.registry:
            raise KeyError("Both claims must exist.")
        self._validate_nonempty(reported_by, "reported_by")
        self._validate_nonempty(note, "note")
        pair = tuple(sorted((claim_id_a, claim_id_b)))
        normalized_note = " ".join(note.split()).casefold()
        normalized_reporter = " ".join(reported_by.split()).casefold()
        for item in self._state.conflicts:
            if (tuple(sorted((item["claim_a"], item["claim_b"]))) == pair
                    and " ".join(item["note"].split()).casefold() == normalized_note
                    and " ".join(item["reported_by"].split()).casefold() == normalized_reporter):
                return deepcopy(item)
        item = {"conflict_id": f"CF-{len(self._state.conflicts)+1:04d}", "timestamp": utc_now(),
                "claim_a": claim_id_a, "claim_b": claim_id_b,
                "status": "UNRESOLVED_REVIEW_REQUIRED", "reported_by": reported_by.strip(),
                "note": note.strip()}
        draft = deepcopy(self._state)
        draft.conflicts.append(item)
        self._append_event(draft, "CONFLICT_RECORDED", "conflict", item["conflict_id"], item,
                           "human", reported_by.strip(),
                           [{"type": "claim", "id": claim_id_a}, {"type": "claim", "id": claim_id_b}],
                           "CONFLICT_CANDIDATE_RECORDED")
        self._state = draft
        return deepcopy(item)

    def register_reference_marker(self, marker_id: str, target_type: str, target_id: str,
                                  created_by: str, locator: Optional[str] = None) -> ReferenceMarker:
        draft = deepcopy(self._state)
        marker = register_marker(draft, marker_id, target_type, target_id, created_by, locator)
        self._state = draft
        return deepcopy(marker)

    def resolve_reference_marker(self, marker_id: str, source_available: Optional[bool] = None) -> str:
        return resolve_marker(self._state, marker_id, source_available)

    def create_context(self, context_id: str, topic_label: str, created_by: str,
                       parent_context_id: Optional[str] = None) -> ContextFrame:
        draft = deepcopy(self._state)
        frame = create_context(draft, context_id, topic_label, created_by, parent_context_id)
        self._state = draft
        return deepcopy(frame)

    def activate_context(self, context_id: str, actor_id: str = "operator") -> None:
        draft = deepcopy(self._state)
        activate_context(draft, context_id, actor_id)
        self._state = draft

    def record_context_relation(self, relation_id: str, claim_id: str, context_id: str,
                                relation_type: str, assigned_by: str,
                                domain_label: Optional[str] = None,
                                basis_marker_ids: Optional[list[str]] = None,
                                initial_assessment_status: str = "PROVISIONAL",
                                supersedes_relation_id: Optional[str] = None) -> ContextRelation:
        draft = deepcopy(self._state)
        relation = record_relation(draft, relation_id, claim_id, context_id, relation_type,
                                   assigned_by, domain_label, basis_marker_ids,
                                   initial_assessment_status, supersedes_relation_id)
        self._state = draft
        return deepcopy(relation)

    def assess_context_relation(self, relation_id: str, assessment_status: str,
                                reason: str, actor_id: str = "operator") -> None:
        draft = deepcopy(self._state)
        assess_relation(draft, relation_id, assessment_status, reason, actor_id)
        self._state = draft

    def current_relation_assessment(self, relation_id: str) -> str:
        return current_relation_assessment(self._state, relation_id)

    def request_feedback(self, request_id: str, target_type: str, target_id: str,
                         question: str, reason: str, created_by: str = "operator",
                         supersedes_request_id: Optional[str] = None) -> FeedbackRequest:
        draft = deepcopy(self._state)
        request = request_feedback(draft, request_id, target_type, target_id, question,
                                   reason, created_by, supersedes_request_id)
        self._state = draft
        return deepcopy(request)

    def resolve_feedback(self, request_id: str, resolution_marker_id: str,
                         actor_id: str = "operator") -> None:
        draft = deepcopy(self._state)
        resolve_feedback(draft, request_id, resolution_marker_id, actor_id)
        self._state = draft

    def cancel_feedback(self, request_id: str, reason: str,
                        actor_id: str = "operator") -> None:
        draft = deepcopy(self._state)
        cancel_feedback(draft, request_id, reason, actor_id)
        self._state = draft

    def snapshot(self) -> dict[str, Any]:
        state = self._state
        return {
            "prototype": "Chain Observer v1.3",
            "claims": {cid: self._claim_dict(claim) for cid, claim in state.registry.items()},
            "transformations": deepcopy(state.transformations),
            "events": EventStore.export_events(state),
            "conflict_candidates": deepcopy(state.conflicts),
            "scope_note": (
                "Property checks evaluate only explicit machine-readable values and declared rules. "
                "They do not infer arbitrary semantic meaning or authenticate source truth. "
                "Missing/incomparable evidence is UNDETERMINED, not PASS. The event stream is "
                "API-level append-only and copy-on-write within this in-memory prototype; it is "
                "not cryptographically tamper-proof or durable storage."
            ),
            "reference_markers": {key: asdict(value) for key, value in state.markers.items()},
            "contexts": {key: asdict(value) for key, value in state.contexts.items()},
            "active_context_id": state.active_context_id,
            "context_relations": {key: asdict(value) for key, value in state.relations.items()},
            "current_relation_assessments": {key: current_relation_assessment(state, key) for key in state.relations},
            "feedback_requests": {key: asdict(value) for key, value in state.feedback_requests.items()},
            "event_integrity": {"valid": not EventStore.validate_chain(state),
                                "errors": EventStore.validate_chain(state),
                                "tamper_proof": False},
        }

    def export_json(self, indent: int = 2) -> str:
        return json.dumps(self.snapshot(), indent=indent, ensure_ascii=False, default=str)

    def validate_integrity(self) -> dict[str, Any]:
        state = self._state
        errors = EventStore.validate_chain(state)
        replayed_claims = {}
        event_objects = {"transformation": {}, "conflict": {}, "marker": {},
                         "context": {}, "relation": {}, "feedback_request": {}}
        active_context = None
        terminal_feedback = set()
        for event in state.events:
            payload = event.get("payload", {}) if isinstance(event, dict) else {}
            if not isinstance(payload, dict):
                errors.append("An event payload is not a dictionary.")
                payload = {}
            event_type = event.get("event_type") if isinstance(event, dict) else None
            subject = event.get("subject_ref", {}) if isinstance(event, dict) else {}
            subject_id = subject.get("id") if isinstance(subject, dict) else None
            if event_type == "CLAIM_REGISTERED":
                replayed_claims[subject_id] = {
                    "status": payload.get("status"),
                    "source_ref": payload.get("source_ref"),
                    "verification_attestation": payload.get("verification_attestation"),
                }
            elif event_type == "STATE_TRANSITION":
                if subject_id not in replayed_claims:
                    errors.append(f"Transition event {event.get('event_id')} has no prior claim registration.")
                else:
                    replayed_claims[subject_id] = {
                        "status": payload.get("to_status"),
                        "source_ref": payload.get("current_source_ref"),
                        "verification_attestation": payload.get("current_verification_attestation"),
                    }
            elif event_type in {"TRANSFORMATION_RECORDED", "CONFLICT_RECORDED",
                                "REFERENCE_MARKER_RECORDED", "CONTEXT_CREATED",
                                "CONTEXT_RELATION_RECORDED", "FEEDBACK_REQUESTED"}:
                object_type = subject.get("type") if isinstance(subject, dict) else None
                if object_type in event_objects:
                    event_objects[object_type][subject_id] = payload
                else:
                    errors.append(f"Event {event.get('event_id')} has an unsupported subject type.")
            elif event_type == "CONTEXT_ACTIVATED":
                active_context = payload.get("context_id")
            elif event_type in {"FEEDBACK_RESOLVED", "FEEDBACK_CANCELLED"}:
                request_id = payload.get("request_id")
                if request_id in terminal_feedback:
                    errors.append(f"Feedback request {request_id!r} has multiple terminal events.")
                terminal_feedback.add(request_id)

        for cid, claim in state.registry.items():
            actual = {"status": claim.status, "source_ref": claim.source_ref,
                      "verification_attestation": self._verification_dict(claim.verification)}
            if replayed_claims.get(cid) != actual:
                errors.append(f"Current claim state for {cid!r} does not match its event history.")
        if set(event_objects["transformation"]) != set(state.transformations):
            errors.append("Transformation registry does not match recorded transformation events.")
        else:
            for key, value in state.transformations.items():
                if event_objects["transformation"].get(key) != value:
                    errors.append(f"Transformation {key!r} diverges from its recorded event payload.")
        conflict_map = {x.get("conflict_id"): x for x in state.conflicts}
        if set(event_objects["conflict"]) != set(conflict_map):
            errors.append("Conflict registry does not match recorded conflict events.")
        else:
            for key, value in conflict_map.items():
                if event_objects["conflict"].get(key) != value:
                    errors.append(f"Conflict {key!r} diverges from its recorded event payload.")
        if set(event_objects["marker"]) != set(state.markers):
            errors.append("Reference marker registry does not match marker events.")
        else:
            for key, marker in state.markers.items():
                if event_objects["marker"].get(key) != {"marker_id": marker.marker_id,
                        "target_type": marker.target_type, "target_id": marker.target_id,
                        "locator": marker.locator, "created_at": marker.created_at,
                        "created_by": marker.created_by}:
                    errors.append(f"Reference marker {key!r} diverges from its recorded event payload.")
        if set(event_objects["context"]) != set(state.contexts):
            errors.append("Context registry does not match context creation events.")
        else:
            for key, frame in state.contexts.items():
                if event_objects["context"].get(key) != {"context_id": frame.context_id,
                        "topic_label": frame.topic_label, "parent_context_id": frame.parent_context_id,
                        "created_at": frame.created_at, "created_by": frame.created_by}:
                    errors.append(f"Context {key!r} diverges from its creation event.")
        if set(event_objects["relation"]) != set(state.relations):
            errors.append("Context relation registry does not match relation events.")
        else:
            for key, relation in state.relations.items():
                if event_objects["relation"].get(key) != {"relation_id": relation.relation_id,
                        "claim_id": relation.claim_id, "context_id": relation.context_id,
                        "relation_type": relation.relation_type, "domain_label": relation.domain_label,
                        "basis_marker_ids": list(relation.basis_marker_ids), "assigned_by": relation.assigned_by,
                        "initial_assessment_status": relation.initial_assessment_status,
                        "supersedes_relation_id": relation.supersedes_relation_id,
                        "created_at": relation.created_at}:
                    errors.append(f"Context relation {key!r} diverges from its creation event.")
        if set(event_objects["feedback_request"]) != set(state.feedback_requests):
            errors.append("Feedback request registry does not match feedback request events.")
        else:
            for key, request in state.feedback_requests.items():
                if event_objects["feedback_request"].get(key) != {"request_id": request.request_id,
                        "target_type": request.target_type, "target_id": request.target_id,
                        "question": request.question, "reason": request.reason,
                        "supersedes_request_id": request.supersedes_request_id,
                        "created_at": request.created_at, "created_by": request.created_by}:
                    errors.append(f"Feedback request {key!r} diverges from its creation event.")
        if active_context != state.active_context_id:
            errors.append("Active context state does not match context activation history.")
        for relation_id, relation in state.relations.items():
            creation = event_objects["relation"].get(relation_id, {})
            if creation.get("initial_assessment_status") != relation.initial_assessment_status:
                errors.append(f"Initial assessment for relation {relation_id!r} diverges from its creation event.")
        return {"valid": not errors, "errors": errors, "tamper_proof": False}
