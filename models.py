"""Shared immutable models and the aggregate state for Chain Observer Project A."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from datetime import datetime, timezone


@dataclass(frozen=True)
class VerificationAttestation:
    source_ref: str
    checked_by: str
    method: str
    checked_at: str

    @classmethod
    def create(cls, source_ref: str, checked_by: str, method: str,
               checked_at: Optional[str] = None) -> "VerificationAttestation":
        values = (source_ref, checked_by, method)
        if not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("Verification attestation requires source_ref, checked_by and method.")
        return cls(source_ref.strip(), checked_by.strip(), method.strip(),
                   (checked_at.strip() if isinstance(checked_at, str) and checked_at.strip() else
                    datetime.now(timezone.utc).isoformat(timespec="seconds")))


@dataclass(frozen=True)
class EpistemicClaim:
    claim_id: str
    content: str
    status: str
    source_ref: Optional[str]
    verification: Optional[VerificationAttestation]
    properties: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ReferenceMarker:
    marker_id: str
    target_type: str
    target_id: str
    locator: Optional[str]
    created_at: str
    created_by: str


@dataclass(frozen=True)
class ContextFrame:
    context_id: str
    topic_label: str
    parent_context_id: Optional[str]
    created_at: str
    created_by: str


@dataclass(frozen=True)
class ContextRelation:
    relation_id: str
    claim_id: str
    context_id: str
    relation_type: str
    domain_label: Optional[str]
    basis_marker_ids: tuple[str, ...]
    assigned_by: str
    created_at: str
    initial_assessment_status: str
    supersedes_relation_id: Optional[str] = None


@dataclass(frozen=True)
class FeedbackRequest:
    request_id: str
    target_type: str
    target_id: str
    question: str
    reason: str
    created_at: str
    created_by: str
    supersedes_request_id: Optional[str] = None


@dataclass
class ObserverState:
    """One copy-on-write aggregate; each successful operation swaps this once."""
    registry: dict[str, EpistemicClaim] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    transformations: dict[str, dict[str, Any]] = field(default_factory=dict)
    markers: dict[str, ReferenceMarker] = field(default_factory=dict)
    contexts: dict[str, ContextFrame] = field(default_factory=dict)
    relations: dict[str, ContextRelation] = field(default_factory=dict)
    feedback_requests: dict[str, FeedbackRequest] = field(default_factory=dict)
    active_context_id: Optional[str] = None
