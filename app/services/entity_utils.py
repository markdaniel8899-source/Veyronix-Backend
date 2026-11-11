"""Helpers for normalizing entity records coming from the local SQLite store.

The backend used to receive rich ``EntityNode`` objects from Zep Cloud.  The
local implementation stores plain dictionaries, so all consumers go through
these helpers to keep the rest of the code base agnostic about the exact
shape of an entity record.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _persona(entity: Dict[str, Any]) -> Dict[str, Any]:
    persona = entity.get("persona")
    if isinstance(persona, dict):
        return persona
    if isinstance(persona, str):
        try:
            decoded = json.loads(persona)
            return decoded if isinstance(decoded, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def entity_uuid(entity: Dict[str, Any]) -> str:
    return str(
        entity.get("uuid")
        or entity.get("id")
        or entity.get("agent_id")
        or ""
    )


def entity_name(entity: Dict[str, Any]) -> str:
    persona = _persona(entity)
    name = entity.get("name") or persona.get("name") or persona.get("title")
    if name:
        return str(name)
    entity_type = entity_type_of(entity)
    suffix = entity_uuid(entity)[-6:]
    if entity_type.lower() == "document_chunk":
        index = persona.get("chunk_index")
        if isinstance(index, int):
            return f"Document Segment {index + 1}"
    return f"{entity_type} {suffix}".strip()


def entity_type_of(entity: Dict[str, Any]) -> str:
    persona = _persona(entity)
    value = (
        entity.get("type")
        or entity.get("entity_type")
        or persona.get("type")
        or persona.get("entity_type")
        or "Entity"
    )
    return str(value)


def entity_summary(entity: Dict[str, Any]) -> str:
    persona = _persona(entity)
    value = (
        entity.get("summary")
        or persona.get("summary")
        or persona.get("description")
        or persona.get("content")
        or ""
    )
    return str(value)


def entity_attributes(entity: Dict[str, Any]) -> Dict[str, Any]:
    persona = _persona(entity)
    attributes = entity.get("attributes")
    if isinstance(attributes, dict) and attributes:
        return attributes
    # Everything except bulky internal keys is useful context.
    return {
        key: value
        for key, value in persona.items()
        if key not in {"content", "graph_id", "batch_id", "operation_id"}
    }


def entity_labels(entity: Dict[str, Any]) -> List[str]:
    persona = _persona(entity)
    labels = entity.get("labels") or persona.get("labels")
    if isinstance(labels, list) and labels:
        return [str(label) for label in labels]
    return [entity_type_of(entity)]


def normalize_entity(entity: Dict[str, Any]) -> Dict[str, Any]:
    """Return a compact, serializable entity dict used across services."""

    return {
        "uuid": entity_uuid(entity),
        "name": entity_name(entity),
        "type": entity_type_of(entity),
        "summary": entity_summary(entity),
        "attributes": entity_attributes(entity),
        "labels": entity_labels(entity),
        "state": entity.get("state"),
    }


def normalize_entities(entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_entity(entity) for entity in entities if isinstance(entity, dict)]


__all__ = [
    "entity_uuid",
    "entity_name",
    "entity_type_of",
    "entity_summary",
    "entity_attributes",
    "entity_labels",
    "normalize_entity",
    "normalize_entities",
]
