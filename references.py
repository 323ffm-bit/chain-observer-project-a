"""Stable reference markers with separate resolution and source-availability states."""
from __future__ import annotations
from typing import Optional
from .models import ReferenceMarker
from .events import EventStore, utc_now

VALID_TARGET_TYPES = {"claim", "event", "external_source"}


def register_marker(state, marker_id: str, target_type: str, target_id: str,
                    created_by: str, locator: Optional[str] = None) -> ReferenceMarker:
    for value, field in ((marker_id, "marker_id"), (target_type, "target_type"),
                         (target_id, "target_id"), (created_by, "created_by")):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string.")
    if marker_id in state.markers:
        raise ValueError(f"Marker ID {marker_id!r} already exists.")
    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"Unsupported marker target_type: {target_type!r}.")
    if target_type == "external_source":
        if not isinstance(locator, str) or not locator.strip():
            raise ValueError("External-source markers require a non-empty locator.")
    elif target_type == "claim" and target_id not in state.registry:
        raise KeyError(f"Referenced claim {target_id!r} does not exist.")
    elif target_type == "event" and not any(e["event_id"] == target_id for e in state.events):
        raise KeyError(f"Referenced event {target_id!r} does not exist.")
    marker = ReferenceMarker(marker_id, target_type, target_id, locator.strip() if isinstance(locator, str) and locator.strip() else None,
                             utc_now(), created_by.strip())
    state.markers[marker_id] = marker
    EventStore.append(state, "REFERENCE_MARKER_RECORDED", {"type": "marker", "id": marker_id},
                      {"marker_id": marker_id, "target_type": target_type, "target_id": target_id,
                       "locator": marker.locator, "created_at": marker.created_at,
                       "created_by": marker.created_by}, "human" if created_by != "system" else "system", created_by,
                      references=([{"type": target_type, "id": target_id}] if target_type != "external_source" else []))
    return marker


def resolve_marker(state, marker_id: str, source_available: Optional[bool] = None) -> str:
    if source_available is not None and not isinstance(source_available, bool):
        raise ValueError("source_available must be True, False or None.")
    marker = state.markers.get(marker_id)
    if marker is None:
        raise KeyError(f"Marker {marker_id!r} not found.")
    if marker.target_type == "claim":
        return "RESOLVED" if marker.target_id in state.registry else "UNKNOWN"
    if marker.target_type == "event":
        return "RESOLVED" if any(e["event_id"] == marker.target_id for e in state.events) else "UNKNOWN"
    if source_available is True:
        return "RESOLVED"
    if source_available is False:
        return "UNAVAILABLE"
    return "UNKNOWN"
