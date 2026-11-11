"""
LLM-driven entity and relationship extraction for the local SQLite graph.

The extractor reads a bounded number of representative text chunks, asks the
LLM for ontology-aligned entities and relationships, deduplicates them, and
returns normalized dictionaries that ``LocalMemory`` can store directly.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .text_processor import TextProcessor

logger = get_logger('Veyronix.entity_extractor')


class EntityExtractor:
    """Extract graph entities and relationships from document text."""

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm = llm_client or LLMClient()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_graph(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_id: str,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        max_chunks: int = 8,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
        """Return (entities, edges, total_chunks). Never raises on LLM errors."""
        chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
        total_chunks = len(chunks)
        if not chunks:
            return [], [], 0

        selected = self._select_chunks(chunks, max_chunks)
        entity_types = [
            str(item.get("name", "")).strip()
            for item in (ontology or {}).get("entity_types", [])
            if isinstance(item, dict) and item.get("name")
        ] or ["Person", "Organization", "Topic"]

        entities: Dict[str, Dict[str, Any]] = {}
        edges: Dict[str, Dict[str, Any]] = {}
        min_interval = 60.0 / max(1, Config.LLM_RPM)
        last_call = 0.0

        for index, (chunk_index, chunk) in enumerate(selected):
            if progress_callback:
                progress_callback(
                    f"Extracting entities {index + 1}/{len(selected)}",
                    (index + 1) / max(1, len(selected)),
                )

            wait = min_interval - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)

            try:
                payload = self._extract_chunk(chunk, entity_types)
                last_call = time.monotonic()
            except Exception as exc:
                last_call = time.monotonic()
                logger.warning("Entity extraction failed for chunk %s: %s", chunk_index, exc)
                payload = {}

            self._merge_payload(payload, chunk, graph_id, entity_types, entities, edges)

        if not entities:
            # Heuristic fallback keeps the pipeline usable when the model is
            # temporarily unavailable, while still producing named entities.
            self._heuristic_entities(text, entity_types, graph_id, entities)

        return list(entities.values()), list(edges.values()), total_chunks

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _select_chunks(
        chunks: List[str], max_chunks: int
    ) -> List[Tuple[int, str]]:
        max_chunks = max(1, min(max_chunks, len(chunks)))
        if len(chunks) <= max_chunks:
            return list(enumerate(chunks))
        step = (len(chunks) - 1) / max(1, max_chunks - 1)
        indices = sorted({int(round(i * step)) for i in range(max_chunks)})
        return [(index, chunks[index]) for index in indices]

    def _extract_chunk(
        self, chunk: str, entity_types: List[str]
    ) -> Dict[str, Any]:
        type_list = ", ".join(entity_types)
        prompt = f"""Extract the most important real entities and relationships from this text.

Allowed entity types: {type_list}

Return strict JSON with this shape:
{{
  "entities": [
    {{
      "name": "exact entity name",
      "type": "one allowed entity type",
      "summary": "1-2 sentence factual summary",
      "attributes": {{"key": "value"}}
    }}
  ],
  "relationships": [
    {{
      "source": "source entity name",
      "target": "target entity name",
      "relation": "short relationship label",
      "fact": "one sentence describing the relationship"
    }}
  ]
}}

Rules:
- Extract 3-12 concrete entities (people, organizations, places, products, events).
- Use only names that actually appear in the text.
- Do not invent facts.
- Keep summaries under 60 words.
- If no clear entities exist, return empty arrays.

TEXT:
{chunk[:2400]}
"""
        result = self.llm.chat_json(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise information extraction engine. "
                        "Return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _entity_key(name: str) -> str:
        return re.sub(r"\s+", " ", name or "").strip().lower()

    @staticmethod
    def _entity_uuid(graph_id: str, name: str) -> str:
        digest = hashlib.sha256(
            f"{graph_id}:{name.lower()}".encode('utf-8')
        ).hexdigest()[:16]
        return f"ent_{digest}"

    def _normalize_type(self, raw_type: str, allowed: List[str]) -> str:
        raw = str(raw_type or "").strip()
        for allowed_type in allowed:
            if raw.lower() == allowed_type.lower():
                return allowed_type
        return allowed[0] if allowed else "Entity"

    def _merge_payload(
        self,
        payload: Dict[str, Any],
        chunk: str,
        graph_id: str,
        allowed_types: List[str],
        entities: Dict[str, Dict[str, Any]],
        edges: Dict[str, Dict[str, Any]],
    ) -> None:
        raw_entities = payload.get("entities") if isinstance(payload, dict) else None
        if not isinstance(raw_entities, list):
            raw_entities = []

        for raw in raw_entities:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            if len(name) < 2 or len(name) > 120:
                continue
            key = self._entity_key(name)
            if not key:
                continue

            raw_type = self._normalize_type(raw.get("type"), allowed_types)
            summary = str(raw.get("summary") or "").strip()[:600]
            attributes = raw.get("attributes")
            if not isinstance(attributes, dict):
                attributes = {}

            existing = entities.get(key)
            if existing:
                if summary and summary not in existing["summary"]:
                    existing["summary"] = (
                        existing["summary"] + " " + summary
                    ).strip()[:900]
                existing["attributes"].update(attributes)
                continue

            entities[key] = {
                "uuid": self._entity_uuid(graph_id, name),
                "name": name,
                "type": raw_type,
                "summary": summary or chunk[:300].strip(),
                "attributes": attributes,
                "labels": [raw_type],
                "graph_id": graph_id,
                "related_edges": [],
                "related_nodes": [],
            }

        raw_edges = payload.get("relationships") if isinstance(payload, dict) else None
        if not isinstance(raw_edges, list):
            raw_edges = []

        for raw in raw_edges:
            if not isinstance(raw, dict):
                continue
            source = str(raw.get("source") or "").strip()
            target = str(raw.get("target") or "").strip()
            relation = str(raw.get("relation") or "RELATED_TO").strip()
            fact = str(raw.get("fact") or "").strip()
            if not source or not target:
                continue

            source_key = self._entity_key(source)
            target_key = self._entity_key(target)
            for key, name in ((source_key, source), (target_key, target)):
                if key not in entities:
                    entities[key] = {
                        "uuid": self._entity_uuid(graph_id, name),
                        "name": name,
                        "type": "Entity",
                        "summary": "",
                        "attributes": {},
                        "labels": ["Entity"],
                        "graph_id": graph_id,
                        "related_edges": [],
                        "related_nodes": [],
                    }

            edge_key = f"{source_key}|{target_key}|{relation.lower()}"
            if edge_key in edges:
                continue
            source_entity = entities[source_key]
            target_entity = entities[target_key]
            edge = {
                "uuid": f"edge_{hashlib.sha256(edge_key.encode('utf-8')).hexdigest()[:16]}",
                "graph_id": graph_id,
                "source_node_uuid": source_entity["uuid"],
                "target_node_uuid": target_entity["uuid"],
                "source_name": source_entity["name"],
                "target_name": target_entity["name"],
                "name": relation,
                "fact": fact or f"{source_entity['name']} {relation} {target_entity['name']}",
            }
            edges[edge_key] = edge

            source_entity["related_edges"].append(
                {**edge, "direction": "outgoing"}
            )
            target_entity["related_edges"].append(
                {**edge, "direction": "incoming"}
            )
            source_entity["related_nodes"].append(
                {
                    "uuid": target_entity["uuid"],
                    "name": target_entity["name"],
                    "labels": [target_entity["type"]],
                    "summary": target_entity["summary"],
                }
            )
            target_entity["related_nodes"].append(
                {
                    "uuid": source_entity["uuid"],
                    "name": source_entity["name"],
                    "labels": [source_entity["type"]],
                    "summary": source_entity["summary"],
                }
            )

    def _heuristic_entities(
        self,
        text: str,
        entity_types: List[str],
        graph_id: str,
        entities: Dict[str, Dict[str, Any]],
    ) -> None:
        """Cheap fallback: capitalized multi-word phrases become entities."""
        stopwords = {
            "the", "this", "that", "these", "those", "chapter", "section",
            "figure", "table", "however", "therefore", "introduction",
            "conclusion", "summary", "report", "document",
        }
        candidates = re.findall(
            r"\b([A-Z][a-zA-Z0-9&'.-]*(?:\s+[A-Z][a-zA-Z0-9&'.-]*){0,3})\b",
            text[:20000],
        )
        counts: Dict[str, int] = {}
        display: Dict[str, str] = {}
        for candidate in candidates:
            name = re.sub(r"\s+", " ", candidate).strip()
            key = name.lower()
            if key in stopwords or len(name) < 3:
                continue
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, name)

        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:12]
        default_type = entity_types[0] if entity_types else "Entity"
        for key, _count in ranked:
            name = display[key]
            entities[key] = {
                "uuid": self._entity_uuid(graph_id, name),
                "name": name,
                "type": default_type,
                "summary": "",
                "attributes": {},
                "labels": [default_type],
                "graph_id": graph_id,
                "related_edges": [],
                "related_nodes": [],
            }


__all__ = ["EntityExtractor"]
