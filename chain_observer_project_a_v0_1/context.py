"""Bounded context frames, contextual relations, and human-feedback lifecycle."""
from __future__ import annotations
from typing import Optional
from .models import ContextFrame, ContextRelation, FeedbackRequest
from .events import EventStore, utc_now

RELATION_TYPES = {"primary", "analogy", "example", "contrast", "transfer", "topic_shift"}
ASSESSMENT_STATUSES = {"PROVISIONAL", "CONFIRMED", "REJECTED", "UNRESOLVED"}
FEEDBACK_TARGET_TYPES = {"claim", "relation", "marker"}


def create_context(state, context_id: str, topic_label: str, created_by: str,
                   parent_context_id: Optional[str] = None) -> ContextFrame:
    for value, field in ((context_id, "context_id"), (topic_label, "topic_label"), (created_by, "created_by")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    if context_id in state.contexts:
        raise ValueError(f"Context ID {context_id!r} already exists.")
    if parent_context_id is not None and parent_context_id not in state.contexts:
        raise KeyError(f"Parent context {parent_context_id!r} does not exist.")
    # Existing-only parent references plus immutable creation make cycles impossible
    # through this API: a new context cannot be its own ancestor.
    frame = ContextFrame(context_id, topic_label.strip(), parent_context_id, utc_now(), created_by.strip())
    state.contexts[context_id] = frame
    refs = ([{"type": "context", "id": parent_context_id}] if parent_context_id else [])
    EventStore.append(state, "CONTEXT_CREATED", {"type": "context", "id": context_id},
                      {"context_id": context_id, "topic_label": frame.topic_label,
                       "parent_context_id": parent_context_id, "created_at": frame.created_at,
                       "created_by": frame.created_by}, "human", created_by.strip(), refs)
    return frame


def activate_context(state, context_id: str, actor_id: str) -> None:
    if context_id not in state.contexts:
        raise KeyError(f"Context {context_id!r} not found.")
    previous = state.active_context_id
    EventStore.append(state, "CONTEXT_ACTIVATED", {"type": "context", "id": context_id},
                      {"context_id": context_id, "previous_context_id": previous}, "human", actor_id,
                      references=([{"type": "context", "id": previous}] if previous else []))
    state.active_context_id = context_id


def record_relation(state, relation_id: str, claim_id: str, context_id: str,
                    relation_type: str, assigned_by: str, domain_label: Optional[str] = None,
                    basis_marker_ids: Optional[list[str]] = None,
                    initial_assessment_status: str = "PROVISIONAL",
                    supersedes_relation_id: Optional[str] = None) -> ContextRelation:
    for value, field in ((relation_id, "relation_id"), (claim_id, "claim_id"),
                         (context_id, "context_id"), (relation_type, "relation_type"),
                         (assigned_by, "assigned_by")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    if relation_id in state.relations:
        raise ValueError(f"Relation ID {relation_id!r} already exists.")
    if claim_id not in state.registry:
        raise KeyError(f"Claim {claim_id!r} not found.")
    if context_id not in state.contexts:
        raise KeyError(f"Context {context_id!r} not found.")
    if relation_type not in RELATION_TYPES:
        raise ValueError(f"Unsupported relation_type: {relation_type!r}.")
    if initial_assessment_status not in ASSESSMENT_STATUSES:
        raise ValueError(f"Unsupported assessment status: {initial_assessment_status!r}.")
    if domain_label is not None and not isinstance(domain_label, str):
        raise ValueError("domain_label must be a string or None.")
    markers = tuple(basis_marker_ids or [])
    for marker_id in markers:
        if marker_id not in state.markers:
            raise KeyError(f"Basis marker {marker_id!r} not found.")
    if supersedes_relation_id is not None and supersedes_relation_id not in state.relations:
        raise KeyError(f"Superseded relation {supersedes_relation_id!r} not found.")
    relation = ContextRelation(relation_id, claim_id, context_id, relation_type,
                               domain_label.strip() if isinstance(domain_label, str) and domain_label.strip() else None,
                               markers, assigned_by.strip(), utc_now(), initial_assessment_status,
                               supersedes_relation_id)
    state.relations[relation_id] = relation
    refs = [{"type": "claim", "id": claim_id}, {"type": "context", "id": context_id}]
    refs.extend({"type": "marker", "id": mid} for mid in markers)
    if supersedes_relation_id:
        refs.append({"type": "relation", "id": supersedes_relation_id})
    EventStore.append(state, "CONTEXT_RELATION_RECORDED", {"type": "relation", "id": relation_id},
                      {"relation_id": relation_id, "claim_id": claim_id, "context_id": context_id,
                       "relation_type": relation_type, "domain_label": relation.domain_label,
                       "basis_marker_ids": list(markers), "assigned_by": assigned_by.strip(),
                       "initial_assessment_status": initial_assessment_status,
                       "supersedes_relation_id": supersedes_relation_id,
                       "created_at": relation.created_at}, "human", assigned_by.strip(), refs)
    return relation


def assess_relation(state, relation_id: str, assessment_status: str, reason: str, actor_id: str) -> None:
    if relation_id not in state.relations:
        raise KeyError(f"Relation {relation_id!r} not found.")
    if assessment_status not in ASSESSMENT_STATUSES:
        raise ValueError(f"Unsupported assessment status: {assessment_status!r}.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string.")
    EventStore.append(state, "CONTEXT_RELATION_ASSESSED", {"type": "relation", "id": relation_id},
                      {"relation_id": relation_id, "assessment_status": assessment_status,
                       "reason": reason.strip()}, "human", actor_id,
                      references=[{"type": "relation", "id": relation_id}])


def current_relation_assessment(state, relation_id: str) -> str:
    relation = state.relations.get(relation_id)
    if relation is None:
        raise KeyError(f"Relation {relation_id!r} not found.")
    current = relation.initial_assessment_status
    for event in state.events:
        if event["event_type"] == "CONTEXT_RELATION_ASSESSED" and event["payload"].get("relation_id") == relation_id:
            current = event["payload"]["assessment_status"]
    return current


def request_feedback(state, request_id: str, target_type: str, target_id: str,
                     question: str, reason: str, created_by: str,
                     supersedes_request_id: Optional[str] = None) -> FeedbackRequest:
    for value, field in ((request_id, "request_id"), (target_type, "target_type"),
                         (target_id, "target_id"), (question, "question"),
                         (reason, "reason"), (created_by, "created_by")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    if request_id in state.feedback_requests:
        raise ValueError(f"Feedback request {request_id!r} already exists.")
    if target_type not in FEEDBACK_TARGET_TYPES:
        raise ValueError(f"Unsupported feedback target_type: {target_type!r}.")
    targets = {"claim": state.registry, "relation": state.relations, "marker": state.markers}
    if target_id not in targets[target_type]:
        raise KeyError(f"Feedback target {target_id!r} not found.")
    if supersedes_request_id is not None and supersedes_request_id not in state.feedback_requests:
        raise KeyError(f"Superseded feedback request {supersedes_request_id!r} not found.")
    req = FeedbackRequest(request_id, target_type, target_id, question.strip(), reason.strip(),
                          utc_now(), created_by.strip(), supersedes_request_id)
    state.feedback_requests[request_id] = req
    refs = [{"type": target_type, "id": target_id}]
    if supersedes_request_id:
        refs.append({"type": "feedback_request", "id": supersedes_request_id})
    EventStore.append(state, "FEEDBACK_REQUESTED", {"type": "feedback_request", "id": request_id},
                      {"request_id": request_id, "target_type": target_type, "target_id": target_id,
                       "question": question.strip(), "reason": reason.strip(),
                       "supersedes_request_id": supersedes_request_id,
                       "created_at": req.created_at, "created_by": req.created_by}, "human", created_by.strip(), refs)
    return req


def _feedback_is_terminal(state, request_id: str) -> bool:
    return any(e["event_type"] in {"FEEDBACK_RESOLVED", "FEEDBACK_CANCELLED"}
               and e["payload"].get("request_id") == request_id for e in state.events)


def resolve_feedback(state, request_id: str, resolution_marker_id: str, actor_id: str) -> None:
    if request_id not in state.feedback_requests:
        raise KeyError(f"Feedback request {request_id!r} not found.")
    if resolution_marker_id not in state.markers:
        raise KeyError(f"Resolution marker {resolution_marker_id!r} not found.")
    if state.markers[resolution_marker_id].target_type != "claim":
        raise ValueError("Feedback resolution must point to a marker for a human-clarification claim.")
    if _feedback_is_terminal(state, request_id):
        raise ValueError("Feedback request already has a terminal resolution or cancellation.")
    EventStore.append(state, "FEEDBACK_RESOLVED", {"type": "feedback_request", "id": request_id},
                      {"request_id": request_id, "resolution_marker_id": resolution_marker_id},
                      "human", actor_id, [{"type": "feedback_request", "id": request_id},
                                           {"type": "marker", "id": resolution_marker_id}])


def cancel_feedback(state, request_id: str, reason: str, actor_id: str) -> None:
    if request_id not in state.feedback_requests:
        raise KeyError(f"Feedback request {request_id!r} not found.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string.")
    if _feedback_is_terminal(state, request_id):
        raise ValueError("Feedback request already has a terminal resolution or cancellation.")
    EventStore.append(state, "FEEDBACK_CANCELLED", {"type": "feedback_request", "id": request_id},
                      {"request_id": request_id, "reason": reason.strip()}, "human", actor_id,
                      [{"type": "feedback_request", "id": request_id}])
