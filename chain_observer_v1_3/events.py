"""Append-only logical event stream with a V1.3-compatible event projection."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional
import uuid

EVENT_TYPES = {
    "CLAIM_REGISTERED", "TRANSITION_BLOCKED", "STATE_TRANSITION",
    "TRANSFORMATION_RECORDED", "CONFLICT_RECORDED",
    "CONTEXT_CREATED", "CONTEXT_ACTIVATED", "CONTEXT_RELATION_RECORDED",
    "CONTEXT_RELATION_ASSESSED", "FEEDBACK_REQUESTED", "FEEDBACK_RESOLVED",
    "FEEDBACK_CANCELLED", "REFERENCE_MARKER_RECORDED",
}

REQUIRED_PAYLOAD_FIELDS = {
    "CLAIM_REGISTERED": {"content", "status"},
    "TRANSITION_BLOCKED": {"from_status", "to_status", "reason"},
    "STATE_TRANSITION": {"from_status", "to_status", "reason", "validation_result"},
    "TRANSFORMATION_RECORDED": {"transformation_id", "input_claim_id", "output_claim_id", "overall_check"},
    "CONFLICT_RECORDED": {"conflict_id", "claim_a", "claim_b", "note"},
    "CONTEXT_CREATED": {"context_id", "topic_label", "parent_context_id", "created_at", "created_by"},
    "CONTEXT_ACTIVATED": {"context_id", "previous_context_id"},
    "CONTEXT_RELATION_RECORDED": {"relation_id", "claim_id", "context_id", "relation_type", "initial_assessment_status", "created_at"},
    "CONTEXT_RELATION_ASSESSED": {"relation_id", "assessment_status", "reason"},
    "FEEDBACK_REQUESTED": {"request_id", "target_type", "target_id", "question", "reason", "created_at", "created_by"},
    "FEEDBACK_RESOLVED": {"request_id", "resolution_marker_id"},
    "FEEDBACK_CANCELLED": {"request_id", "reason"},
    "REFERENCE_MARKER_RECORDED": {"marker_id", "target_type", "target_id", "created_at", "created_by"},
}

ALLOWED_PAYLOAD_FIELDS = {
    "CLAIM_REGISTERED": {"content", "status", "source_ref", "properties", "verification_attestation"},
    "TRANSITION_BLOCKED": {"from_status", "to_status", "reason", "from", "to", "blocking_transformations"},
    "STATE_TRANSITION": {"from_status", "to_status", "from", "to", "reason", "previous_source_ref",
                         "previous_verification_attestation", "current_source_ref",
                         "current_verification_attestation", "override_review", "trigger_marker_ids",
                         "evidence_marker_ids", "reasoning_marker_id", "validation_result",
                         "previous_transition_event_id"},
    "TRANSFORMATION_RECORDED": {"transformation_id", "input_claim_id", "output_claim_id", "operation",
                                 "conditions", "actor", "timestamp", "input_properties", "output_properties",
                                 "property_checks", "overall_check", "disposition"},
    "CONFLICT_RECORDED": {"conflict_id", "timestamp", "claim_a", "claim_b", "status", "reported_by", "note"},
    "CONTEXT_CREATED": {"context_id", "topic_label", "parent_context_id", "created_at", "created_by"},
    "CONTEXT_ACTIVATED": {"context_id", "previous_context_id"},
    "CONTEXT_RELATION_RECORDED": {"relation_id", "claim_id", "context_id", "relation_type", "domain_label",
                                   "basis_marker_ids", "assigned_by", "initial_assessment_status",
                                   "supersedes_relation_id", "created_at"},
    "CONTEXT_RELATION_ASSESSED": {"relation_id", "assessment_status", "reason"},
    "FEEDBACK_REQUESTED": {"request_id", "target_type", "target_id", "question", "reason",
                            "supersedes_request_id", "created_at", "created_by"},
    "FEEDBACK_RESOLVED": {"request_id", "resolution_marker_id"},
    "FEEDBACK_CANCELLED": {"request_id", "reason"},
    "REFERENCE_MARKER_RECORDED": {"marker_id", "target_type", "target_id", "locator", "created_at", "created_by"},
}

VALID_EPISTEMIC_STATUSES = {"[R]", "[K]", "[I]", "[H]", "[U]"}
VALID_CHECK_RESULTS = {"PASS", "VIOLATION", "UNDETERMINED"}
VALID_ASSESSMENTS = {"PROVISIONAL", "CONFIRMED", "REJECTED", "UNRESOLVED"}
VALID_RELATIONS = {"primary", "analogy", "example", "contrast", "transfer", "topic_shift"}
REFERENCE_TYPES = {"claim", "event", "external_source", "context", "relation", "marker",
                    "feedback_request", "transformation", "conflict"}



def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventValidationError(ValueError):
    """Raised when an event envelope or its typed payload is invalid."""


class EventStore:
    @staticmethod
    def append(state, event_type: str, subject_ref: dict[str, str],
               payload: dict[str, Any], actor_type: str = "system",
               actor_id: Optional[str] = None,
               references: Optional[list[dict[str, str]]] = None,
               timestamp: Optional[str] = None, legacy_event_name: Optional[str] = None) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise EventValidationError(f"Unsupported event type: {event_type!r}.")
        if not isinstance(payload, dict):
            raise EventValidationError("Event payload must be a dictionary.")
        missing = REQUIRED_PAYLOAD_FIELDS[event_type] - set(payload)
        if missing:
            raise EventValidationError(f"{event_type} payload missing required fields: {sorted(missing)}")
        unknown = set(payload) - ALLOWED_PAYLOAD_FIELDS[event_type]
        if unknown:
            raise EventValidationError(f"{event_type} payload contains unsupported fields: {sorted(unknown)}")
        EventStore._validate_payload(event_type, payload)
        if actor_type not in {"human", "assistant", "system", "import"}:
            raise EventValidationError(f"Invalid actor_type: {actor_type!r}.")
        if not isinstance(subject_ref, dict) or set(subject_ref) != {"type", "id"}:
            raise EventValidationError("subject_ref must contain exactly 'type' and 'id'.")
        if (subject_ref.get("type") not in REFERENCE_TYPES
                or not isinstance(subject_ref.get("id"), str) or not subject_ref["id"].strip()):
            raise EventValidationError("subject_ref type and id must be valid non-empty values.")
        if actor_id is not None and (not isinstance(actor_id, str) or not actor_id.strip()):
            raise EventValidationError("actor_id must be a non-empty string or None.")
        refs = deepcopy(references or [])
        for ref in refs:
            if (not isinstance(ref, dict) or set(ref) != {"type", "id"}
                    or ref.get("type") not in REFERENCE_TYPES
                    or not isinstance(ref.get("id"), str) or not ref["id"].strip()):
                raise EventValidationError("Each event reference must contain a valid type and non-empty id.")
        previous = state.events[-1]["event_id"] if state.events else None
        event_id = str(uuid.uuid4())
        sequence = len(state.events) + 1
        # Canonical envelope plus explicit legacy aliases. Keeping both avoids
        # forcing V1.3 consumers to migrate immediately.
        event = {
            "event_id": event_id,
            "event_type": event_type,
            "sequence": sequence,
            "timestamp": timestamp or utc_now(),
            "actor_type": actor_type,
            "actor_id": actor_id,
            "subject_ref": deepcopy(subject_ref),
            "references": refs,
            "payload": deepcopy(payload),
            "previous_event_id": previous,
            # V1.3 compatibility projection:
            "event": legacy_event_name or event_type,
            "claim_id": subject_ref["id"] if subject_ref["type"] == "claim" else None,
            "details": deepcopy(payload),
        }
        state.events.append(event)
        return deepcopy(event)

    @staticmethod
    def _validate_payload(event_type: str, payload: dict[str, Any]) -> None:
        string_fields = {
            "CLAIM_REGISTERED": {"content", "status"},
            "TRANSITION_BLOCKED": {"from_status", "to_status", "reason"},
            "STATE_TRANSITION": {"from_status", "to_status", "reason"},
            "TRANSFORMATION_RECORDED": {"transformation_id", "input_claim_id", "output_claim_id", "operation", "actor", "timestamp", "disposition"},
            "CONFLICT_RECORDED": {"conflict_id", "timestamp", "claim_a", "claim_b", "status", "reported_by", "note"},
            "CONTEXT_CREATED": {"context_id", "topic_label", "created_at", "created_by"},
            "CONTEXT_ACTIVATED": {"context_id"},
            "CONTEXT_RELATION_RECORDED": {"relation_id", "claim_id", "context_id", "relation_type", "assigned_by", "initial_assessment_status", "created_at"},
            "CONTEXT_RELATION_ASSESSED": {"relation_id", "assessment_status", "reason"},
            "FEEDBACK_REQUESTED": {"request_id", "target_type", "target_id", "question", "reason", "created_at", "created_by"},
            "FEEDBACK_RESOLVED": {"request_id", "resolution_marker_id"},
            "FEEDBACK_CANCELLED": {"request_id", "reason"},
            "REFERENCE_MARKER_RECORDED": {"marker_id", "target_type", "target_id", "created_at", "created_by"},
        }[event_type]
        for field in string_fields:
            if not isinstance(payload.get(field), str) or not payload[field].strip():
                raise EventValidationError(f"{event_type}.{field} must be a non-empty string.")
        nullable_strings = {
            "CLAIM_REGISTERED": {"source_ref"},
            "STATE_TRANSITION": {"previous_source_ref", "current_source_ref", "reasoning_marker_id", "previous_transition_event_id"},
            "CONTEXT_CREATED": {"parent_context_id"},
            "CONTEXT_ACTIVATED": {"previous_context_id"},
            "CONTEXT_RELATION_RECORDED": {"domain_label", "supersedes_relation_id"},
            "FEEDBACK_REQUESTED": {"supersedes_request_id"},
            "REFERENCE_MARKER_RECORDED": {"locator"},
        }.get(event_type, set())
        for field in nullable_strings:
            value = payload.get(field)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise EventValidationError(f"{event_type}.{field} must be a non-empty string or None.")
        if event_type == "CLAIM_REGISTERED" and payload["status"] not in VALID_EPISTEMIC_STATUSES:
            raise EventValidationError("CLAIM_REGISTERED.status is invalid.")
        if event_type in {"TRANSITION_BLOCKED", "STATE_TRANSITION"}:
            if payload["from_status"] not in VALID_EPISTEMIC_STATUSES or payload["to_status"] not in VALID_EPISTEMIC_STATUSES:
                raise EventValidationError(f"{event_type} contains an invalid epistemic status.")
        if event_type == "STATE_TRANSITION":
            if payload["validation_result"] not in VALID_CHECK_RESULTS:
                raise EventValidationError("STATE_TRANSITION.validation_result is invalid.")
            if not isinstance(payload["override_review"], bool):
                raise EventValidationError("STATE_TRANSITION.override_review must be boolean.")
            for field in ("trigger_marker_ids", "evidence_marker_ids"):
                if not isinstance(payload[field], list) or not all(isinstance(x, str) and x.strip() for x in payload[field]):
                    raise EventValidationError(f"STATE_TRANSITION.{field} must be a list of non-empty strings.")
        dict_fields = {
            "CLAIM_REGISTERED": {"properties"},
            "TRANSFORMATION_RECORDED": {"conditions", "input_properties", "output_properties"},
        }.get(event_type, set())
        for field in dict_fields:
            if not isinstance(payload[field], dict):
                raise EventValidationError(f"{event_type}.{field} must be a dictionary.")
        if event_type == "CLAIM_REGISTERED":
            attestation = payload["verification_attestation"]
            if attestation is not None and not isinstance(attestation, dict):
                raise EventValidationError("CLAIM_REGISTERED.verification_attestation must be a dictionary or None.")
        if event_type == "TRANSFORMATION_RECORDED":
            if not isinstance(payload["property_checks"], list) or payload["overall_check"] not in VALID_CHECK_RESULTS:
                raise EventValidationError("TRANSFORMATION_RECORDED check fields are invalid.")
        if event_type == "CONTEXT_RELATION_RECORDED":
            if payload["relation_type"] not in VALID_RELATIONS or payload["initial_assessment_status"] not in VALID_ASSESSMENTS:
                raise EventValidationError("CONTEXT_RELATION_RECORDED relation or assessment type is invalid.")
            if not isinstance(payload["basis_marker_ids"], list) or not all(isinstance(x, str) and x.strip() for x in payload["basis_marker_ids"]):
                raise EventValidationError("CONTEXT_RELATION_RECORDED.basis_marker_ids must be a list of non-empty strings.")
        if event_type == "CONTEXT_RELATION_ASSESSED" and payload["assessment_status"] not in VALID_ASSESSMENTS:
            raise EventValidationError("CONTEXT_RELATION_ASSESSED.assessment_status is invalid.")
        if event_type == "FEEDBACK_REQUESTED" and payload["target_type"] not in {"claim", "relation", "marker"}:
            raise EventValidationError("FEEDBACK_REQUESTED.target_type is invalid.")
        if event_type == "REFERENCE_MARKER_RECORDED" and payload["target_type"] not in {"claim", "event", "external_source"}:
            raise EventValidationError("REFERENCE_MARKER_RECORDED.target_type is invalid.")

    @staticmethod
    def export_events(state) -> list[dict[str, Any]]:
        return deepcopy(state.events)

    @staticmethod
    def validate_chain(state) -> list[str]:
        errors = []
        previous = None
        seen_event_ids = set()
        known = {"claim": set(), "context": set(), "relation": set(), "marker": set(),
                 "feedback_request": set(), "transformation": set(), "conflict": set(), "event": set()}
        creation_types = {"CLAIM_REGISTERED", "CONTEXT_CREATED", "CONTEXT_RELATION_RECORDED",
                          "REFERENCE_MARKER_RECORDED", "FEEDBACK_REQUESTED",
                          "TRANSFORMATION_RECORDED", "CONFLICT_RECORDED"}
        for index, event in enumerate(state.events, start=1):
            if not isinstance(event, dict):
                errors.append(f"Event at sequence position {index} is not a dictionary.")
                continue
            event_id = event.get("event_id")
            if event.get("event_type") not in EVENT_TYPES:
                errors.append(f"Event {event_id} has an unsupported event_type.")
            if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_event_ids:
                errors.append(f"Event at sequence position {index} has a missing or duplicate event_id.")
            if isinstance(event_id, str):
                seen_event_ids.add(event_id)
            if event.get("sequence") != index:
                errors.append(f"Event {event_id} has non-contiguous sequence.")
            if event.get("previous_event_id") != previous:
                errors.append(f"Event {event_id} has invalid predecessor link.")
            if event.get("details") != event.get("payload"):
                errors.append(f"Event {event_id} legacy projection diverges from canonical fields.")
            refs = event.get("references", [])
            if not isinstance(refs, list):
                errors.append(f"Event {event_id} references field is not a list.")
                refs = []
            for ref in refs:
                if not isinstance(ref, dict):
                    errors.append(f"Event {event_id} contains a malformed reference.")
                    continue
                kind, identifier = ref.get("type"), ref.get("id")
                if kind == "external_source":
                    # External source identifiers are locators, not internally
                    # registered objects; validate their shape but do not resolve them.
                    if not isinstance(identifier, str) or not identifier.strip():
                        errors.append(f"Event {event_id} contains an invalid external_source reference.")
                elif kind not in known or identifier not in known[kind]:
                    errors.append(f"Event {event_id} contains a forward, dangling or unknown reference {kind}:{identifier}.")
            payload = event.get("payload", {})
            if not isinstance(payload, dict):
                errors.append(f"Event {event_id} payload is not a dictionary.")
            subject = event.get("subject_ref", {})
            if not isinstance(subject, dict):
                errors.append(f"Event {event_id} has malformed subject_ref.")
                subject = {}
            if event.get("event_type") in creation_types and subject.get("type") in known:
                known[subject["type"]].add(subject.get("id"))
            if isinstance(event_id, str):
                known["event"].add(event_id)
            previous = event_id
        return errors
