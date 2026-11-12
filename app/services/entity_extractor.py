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
from ..utils.llm_client import LLMClient, LLMResponseError
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
        chunk_size: int = 4000,
        chunk_overlap: int = 200,
        max_chunks: int = 24,
        max_entities: int = 150,
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

        # The ontology pass already saw up to 100k characters. Reuse its
        # named-entity inventory as a high-priority seed.
        self._seed_entities_from_ontology(
            ontology, graph_id, entity_types, entities, edges
        )

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
                logger.warning(
                    "Entity extraction failed for chunk %s: %s",
                    chunk_index,
                    exc,
                )
                payload = {}

            self._merge_payload(
                payload,
                chunk,
                graph_id,
                entity_types,
                entities,
                edges,
            )

        if not entities:
            # Heuristic fallback keeps the pipeline usable when the model is
            # temporarily unavailable, while still producing named entities.
            self._heuristic_entities(text, entity_types, graph_id, entities)

        if entities and max_entities > 0 and len(entities) > max_entities:
            ranked = sorted(
                entities.values(),
                key=lambda entity: (
                    1 if entity.get("is_seed") else 0,
                    len(entity.get("related_edges") or []),
                    len(entity.get("summary") or ""),
                ),
                reverse=True,
            )
            kept_uuids = {entity["uuid"] for entity in ranked[:max_entities]}
            entities = {
                key: value
                for key, value in entities.items()
                if value["uuid"] in kept_uuids
            }
            edges = {
                key: value
                for key, value in edges.items()
                if value["source_node_uuid"] in kept_uuids
                and value["target_node_uuid"] in kept_uuids
            }

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

    # Token budget for one extraction call. The first attempt allows a rich
    # answer, the compact retry is used when the model hits the limit.
    EXTRACT_MAX_TOKENS = 7000
    EXTRACT_RETRY_MAX_TOKENS = 3500

    def _seed_entities_from_ontology(
        self,
        ontology: Dict[str, Any],
        graph_id: str,
        allowed_types: List[str],
        entities: Dict[str, Dict[str, Any]],
        edges: Dict[str, Dict[str, Any]],
    ) -> None:
        key_entities = (ontology or {}).get("key_entities")
        if not isinstance(key_entities, list) or not key_entities:
            return

        payload_entities = []
        for item in key_entities:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            payload_entities.append({
                "name": name,
                "type": item.get("type") or "Entity",
                "summary": (
                    item.get("summary")
                    or item.get("evidence")
                    or item.get("role")
                    or ""
                ),
                "attributes": {
                    "role": item.get("role") or "",
                    "aliases": item.get("aliases") or [],
                    "evidence": item.get("evidence") or "",
                    "inferred": bool(item.get("inferred", False)),
                },
            })

        self._merge_payload(
            {"entities": payload_entities, "relationships": []},
            "",
            graph_id,
            allowed_types,
            entities,
            edges,
            seed=True,
        )

    def _extract_chunk(
        self, chunk: str, entity_types: List[str]
    ) -> Dict[str, Any]:
        """Extract entities and relationships from one chunk.

        A truncated answer used to be dropped completely, which left the
        graph without a single relationship. When the model hits the token
        limit the extraction is retried with a compact prompt that asks for
        fewer but shorter entities.
        """
        try:
            return self._extract_chunk_once(chunk, entity_types, compact=False)
        except LLMResponseError as exc:
            if not getattr(exc, "finish_reason", None) == "length":
                raise
            logger.warning(
                "Entity extraction hit the token limit, retrying compact: %s", exc
            )
        return self._extract_chunk_once(chunk, entity_types, compact=True)

    def _extract_chunk_once(
        self, chunk: str, entity_types: List[str], compact: bool = False
    ) -> Dict[str, Any]:
        type_list = ", ".join(entity_types)
        entity_budget = "10-20" if compact else "15-30"
        summary_budget = "30 words" if compact else "60 words"

        prompt = f"""Extract EVERY named entity and relationship from this text.

Allowed entity types: {type_list}

Return strict JSON with this shape:
{{
  "entities": [
    {{
      "name": "exact entity name from the text",
      "type": "one allowed entity type",
      "summary": "1-2 factual sentences",
      "attributes": {{"role": "...", "affiliation": "...", "platform": "...", "aliases": "..."}}
    }}
  ],
  "relationships": [
    {{
      "source": "source entity name",
      "target": "target entity name",
      "relation": "UPPER_SNAKE_CASE label",
      "fact": "one sentence describing the relationship"
    }}
  ]
}}

Rules:
- Extract {entity_budget} entities. Do not stop at the first few names.
- Include every named person, organization, government body, company,
  platform, media outlet, university, hospital, union, NGO, named
  policy/program, and named role group.
- Include central actors that are only described by role (for example
  "the regulator"). Use the role as the name and set
  attributes.inferred to true.
- Include aliases, handles, and short names when the text provides them.
- Use only facts that appear in the text. Do not invent names or facts.
- Every relationship must connect two entities that appear in the text.
- Keep summaries under {summary_budget}.
- If the text truly contains no named actors, return empty arrays.

TEXT:
{chunk[:6000]}
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
            max_tokens=self.EXTRACT_RETRY_MAX_TOKENS if compact else self.EXTRACT_MAX_TOKENS,
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
        if not raw:
            return "Person" if "Person" in allowed else (
                allowed[0] if allowed else "Entity"
            )

        for allowed_type in allowed:
            if raw.lower() == allowed_type.lower():
                return allowed_type

        for allowed_type in allowed:
            if (
                allowed_type.lower() in raw.lower()
                or raw.lower() in allowed_type.lower()
            ):
                return allowed_type

        org_hints = (
            "org", "company", "agency", "ministry", "university", "media",
            "platform", "group", "team", "committee", "department", "ngo",
            "bank", "hospital", "school", "union", "party", "council",
        )
        if any(hint in raw.lower() for hint in org_hints):
            for candidate in (
                "Organization", "Company", "GovernmentAgency",
                "MediaOutlet", "Platform",
            ):
                if candidate in allowed:
                    return candidate

        for candidate in ("Person", "Organization"):
            if candidate in allowed:
                return candidate

        return allowed[0] if allowed else "Entity"

    def _merge_payload(
        self,
        payload: Dict[str, Any],
        chunk: str,
        graph_id: str,
        allowed_types: List[str],
        entities: Dict[str, Dict[str, Any]],
        edges: Dict[str, Dict[str, Any]],
        seed: bool = False,
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
                existing["is_seed"] = existing.get("is_seed", False) or seed
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
                "is_seed": seed,
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
                        "is_seed": False,
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
            text[:100000],
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

        ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)[:40]
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
