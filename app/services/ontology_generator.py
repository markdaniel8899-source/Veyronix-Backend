"""
ontology generate service
API 1：Analyze text content，generate suitable for society simulation of entity and relationship type definition
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction
from ..utils.file_parser import split_text_into_chunks
from ..utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)

logger = logging.getLogger(__name__)


def _to_pascal_case(name: str) -> str:
    """convert any format of name for PascalCase（like 'works_for' -> 'WorksFor', 'person' -> 'Person'）"""
    # by non-characters parent count characters characters character split
    parts = re.split(r'[^a-zA-Z0-9]+', name)
    # again by camelCase edge boundary split（like 'camelCase' -> ['camel', 'Case']）
    words = []
    for part in parts:
        words.extend(re.sub(r'([a-z])([A-Z])', r'\1_\2', part).split('_'))
    # Capitalize the characters at the beginning of each word，filter empty string
    result = ''.join(word.capitalize() for word in words if word)
    return result if result else 'Unknown'


def _to_upper_snake_case(name: str) -> str:
    """Convert free-form or camelCase names to SCREAMING_SNAKE_CASE."""

    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name.strip())
    normalized = re.sub(r'[^a-zA-Z0-9]+', '_', separated).strip('_').upper()
    if not normalized:
        return "UNKNOWN"
    if normalized[0].isdigit():
        normalized = f"REL_{normalized}"
    return normalized


# ontology generate of system hint word
ONTOLOGY_SYSTEM_PROMPT = """You are a knowledge-graph architect for a social-media public-opinion simulation.

Your job is to read the supplied document and return ONE valid JSON object that defines:
1. the entity TYPES needed to model the actors in this specific document;
2. a key-entity INVENTORY of the named people, organizations, platforms, agencies, companies, media outlets, and role groups that actually appear in the document;
3. the relationship TYPES that connect them.

Return JSON only. No markdown, no commentary.

## Output schema

{
  "entity_types": [
    {
      "name": "PascalCase type name",
      "description": "short description, max 100 characters",
      "attributes": [
        {"name": "snake_case", "type": "text", "description": "property description"}
      ],
      "examples": ["named example from the document"]
    }
  ],
  "key_entities": [
    {
      "name": "exact name used in the document",
      "type": "one of entity_types",
      "role": "their role or title in this document",
      "aliases": ["other names, short names, or handles used in the document"],
      "evidence": "short phrase from the document showing they exist",
      "summary": "one factual sentence",
      "inferred": false
    }
  ],
  "edge_types": [
    {
      "name": "UPPER_SNAKE_CASE relationship name",
      "description": "short description, max 100 characters",
      "source_targets": [{"source": "source entity type", "target": "target entity type"}],
      "attributes": []
    }
  ],
  "analysis_summary": "brief analysis of the document"
}

## Entity type rules

- Return 8-12 entity types. This is a limit on TYPES, not on entities.
- Always include the fallbacks `Person` and `Organization`.
- The other 6-10 types must be specific to THIS document. Examples:
  Regulator, Ministry, PoliticalParty, MediaOutlet, Platform, University,
  Hospital, Union, NGO, Company, ActivistGroup, Whistleblower, Journalist,
  Executive, Official, Researcher, Student, Investor, CommunityGroup.
- Never return only generic types. If the document is about a crisis, include
  the document's actual actor categories.
- A type must be a real actor that can speak or be spoken about on social media.
  Do not create types for abstract topics, moods, or opinions.

## Key entity rules (critical)

- Extract 40-80 key entities from the document. If the document is short,
  extract every named actor; do not pad with invented names.
- Include every named person, organization, government body, company, platform,
  media outlet, university, hospital, union, NGO, named policy/program, and
  named role group.
- Include actors that are central but only described by role (for example
  "the regulator", "the striking workers"). Set `"inferred": true` and use the
  role as the name ONLY when the document gives no proper name. Never fabricate
  a proper name.
- Include aliases, short names, handles, and translations used in the document.
- `evidence` must be a short quote or close paraphrase from the document.
  Do not invent evidence.
- Every key entity's `type` must be one of the `entity_types` you returned.
- Prefer specific named actors over generic labels like "Person" or
  "Organization". Use the fallback types only when no specific type fits.

## Relationship rules

- Return 6-12 edge types.
- Relationships must describe real social-media-era connections:
  WORKS_FOR, REPRESENTS, REGULATES, REPORTS_ON, CRITICIZES, SUPPORTS,
  OPPOSES, INVESTIGATES, PARTNERS_WITH, COMPETES_WITH, FUNDS, EMPLOYS,
  MEMBER_OF, RESPONDS_TO.
- `source_targets` must cover the entity types you defined.
- Each entity type gets 1-3 attributes.
- Never use reserved attribute names: uuid, name, group_id, graph_id,
  created_at, summary, name_embedding.

## Quality bar

- The inventory must be specific enough that a reader can tell which document
  it came from.
- Do not return a generic ontology that could fit any document.
- Do not stop at the first 5-10 names. Scan the whole supplied text for named
  actors.
"""


class OntologyGenerator:
    """
    ontology generator
    Analyze text content，generate entity and relationship type definition
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        generate ontology definition
        
        Args:
            document_texts: Document text list
            simulation_requirement: simulation requirement description
            additional_context: extra context
            
        Returns:
            ontology definition（entity_types, edge_types etc.）
        """
        # build user message
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        lang_instruction = get_language_instruction()
        system_prompt = f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\nIMPORTANT: Entity type names MUST be in English PascalCase (e.g., 'PersonEntity', 'MediaOrganization'). Relationship type names MUST be in English UPPER_SNAKE_CASE (e.g., 'WORKS_FOR'). Attribute names MUST be in English snake_case. Only description fields and analysis_summary should use the specified language above."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # call LLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            # Structured ontology responses can exceed 4096 completion tokens,
            # especially when a compatible provider counts hidden reasoning in
            # the same budget. Let the provider use its model-specific limit.
            max_tokens=None,
            max_attempts=2,
        )
        
        # validate and post-process
        result = self._validate_and_process(result)
        
        return result
    
    # Maximum document window passed to the ontology LLM.
    MAX_TEXT_LENGTH_FOR_LLM = 100000
    LONG_TEXT_CHUNK_SIZE = 10000
    LONG_TEXT_CHUNK_OVERLAP = 200
    MAX_LONG_TEXT_CHUNKS = 60
    MIN_LONG_TEXT_EXCERPT = 800
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """build user message"""
        
        combined_text = self._build_document_context(document_texts)
        
        message = f"""## simulation requirement

{simulation_requirement}

## Document content

{combined_text}
"""
        
        if additional_context:
            message += f"""
## additional description

{additional_context}
"""
        
        message += """
Return the JSON object described in the system prompt.

Rules:
1. 8-12 entity types, including the `Person` and `Organization` fallbacks.
2. 40-80 key_entities copied from the document, each with evidence.
3. 6-12 edge types whose source_targets cover your entity types.
4. Entity type names must be English PascalCase.
5. Relationship type names must be English UPPER_SNAKE_CASE.
6. Attribute names must be English snake_case.
"""
        
        return message

    def _build_document_context(self, document_texts: List[str]) -> str:
        """build for ontology analysis of document context，Long text by global chunk sampling instead of just intercepting the beginning。"""

        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        if original_length <= self.MAX_TEXT_LENGTH_FOR_LLM:
            return combined_text

        chunks = self._collect_document_chunks(document_texts)
        if not chunks:
            return ""

        selected_chunks = self._select_representative_chunks(chunks)
        excerpt_budget = self._calculate_excerpt_budget(len(selected_chunks))
        context = self._render_chunked_context(
            selected_chunks=selected_chunks,
            original_length=original_length,
            total_chunks=len(chunks),
            excerpt_limit=excerpt_budget,
        )

        while len(context) > self.MAX_TEXT_LENGTH_FOR_LLM and excerpt_budget > self.MIN_LONG_TEXT_EXCERPT:
            excerpt_budget = max(self.MIN_LONG_TEXT_EXCERPT, int(excerpt_budget * 0.85))
            context = self._render_chunked_context(
                selected_chunks=selected_chunks,
                original_length=original_length,
                total_chunks=len(chunks),
                excerpt_limit=excerpt_budget,
            )

        if len(context) > self.MAX_TEXT_LENGTH_FOR_LLM:
            marker = "\n\n...(The chunk context has been compressed to ontology within the analysis length limit.)..."
            context = context[:self.MAX_TEXT_LENGTH_FOR_LLM - len(marker)] + marker

        return context

    def _collect_document_chunks(self, document_texts: List[str]) -> List[Dict[str, Any]]:
        """by document collection chunk，Keep document and chunk numbers to facilitate hint word positioning。"""

        all_chunks: List[Dict[str, Any]] = []
        for doc_index, text in enumerate(document_texts, 1):
            doc_chunks = split_text_into_chunks(
                text,
                chunk_size=self.LONG_TEXT_CHUNK_SIZE,
                overlap=self.LONG_TEXT_CHUNK_OVERLAP,
            )
            total_doc_chunks = len(doc_chunks)
            for chunk_index, chunk in enumerate(doc_chunks, 1):
                all_chunks.append({
                    "document_index": doc_index,
                    "chunk_index": chunk_index,
                    "total_document_chunks": total_doc_chunks,
                    "text": chunk,
                })

        return all_chunks

    def _select_representative_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """from all chunk etc. distance sampling，Cover the beginning of long articles、middle and end。"""

        if len(chunks) <= self.MAX_LONG_TEXT_CHUNKS:
            return chunks

        if self.MAX_LONG_TEXT_CHUNKS <= 1:
            return [chunks[0]]

        last_index = len(chunks) - 1
        selected_indexes = {
            round(i * last_index / (self.MAX_LONG_TEXT_CHUNKS - 1))
            for i in range(self.MAX_LONG_TEXT_CHUNKS)
        }
        return [chunks[i] for i in sorted(selected_indexes)]

    def _calculate_excerpt_budget(self, selected_count: int) -> int:
        """Allocate characters budget for each chunk based on chunk count selected。"""

        header_budget = 600
        chunk_header_budget = 120 * selected_count
        available = max(
            self.MIN_LONG_TEXT_EXCERPT * selected_count,
            self.MAX_TEXT_LENGTH_FOR_LLM - header_budget - chunk_header_budget,
        )
        return max(self.MIN_LONG_TEXT_EXCERPT, available // max(selected_count, 1))

    def _render_chunked_context(
        self,
        selected_chunks: List[Dict[str, Any]],
        original_length: int,
        total_chunks: int,
        excerpt_limit: int,
    ) -> str:
        """Render long text chunk context。"""

        lines = [
            (
                f"【Long text automatic chunk summary】Total original text{original_length}characters，"
                f"divided for{total_chunks}text block for global coverage analysis。"
            ),
            (
                f"Shown below{len(selected_chunks)}representative text block of excerpt，"
                "Cover the beginning、middle and end；Please design ontology based on these cross-text clues，Don't just rely on the first paragraph of content。"
            ),
        ]

        for chunk in selected_chunks:
            excerpt = self._excerpt_text(chunk["text"], excerpt_limit)
            lines.append(
                "\n".join([
                    (
                        f"--- document {chunk['document_index']} / "
                        f"chunk {chunk['chunk_index']}/{chunk['total_document_chunks']} ---"
                    ),
                    excerpt,
                ])
            )

        return "\n\n".join(lines)

    @staticmethod
    def _excerpt_text(text: str, char_limit: int) -> str:
        """Keep the beginning and end of long chunks，Avoid changing times inside each chunk to only look at the beginning。"""

        text = text.strip()
        if len(text) <= char_limit:
            return text

        marker = "\n...(The middle content of this chunk is omitted)...\n"
        if char_limit <= len(marker) + 20:
            return text[:char_limit]

        remaining = char_limit - len(marker)
        head_len = remaining // 2
        tail_len = remaining - head_len
        return f"{text[:head_len].rstrip()}{marker}{text[-tail_len:].lstrip()}"
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """validate and post-process result"""
        if not isinstance(result, dict):
            raise ValueError("Ontology result must be an object")

        raw_entities = result.get("entity_types")
        raw_edges = result.get("edge_types")
        if not isinstance(raw_entities, list):
            raw_entities = []
        if not isinstance(raw_edges, list):
            raw_edges = []
        if not isinstance(result.get("analysis_summary"), str):
            result["analysis_summary"] = ""

        # Normalize entity entries before touching their fields. LLMs
        # occasionally emit a bare string, null, or another scalar.
        entity_name_map: Dict[str, str] = {}
        processed_entities: List[Dict[str, Any]] = []
        seen_entity_names = set()
        for raw_entity in raw_entities:
            if isinstance(raw_entity, str):
                entity = {"name": raw_entity}
            elif isinstance(raw_entity, dict):
                entity = dict(raw_entity)
            else:
                logger.warning("Ignoring non-object ontology entity entry")
                continue

            original_name = entity.get("name")
            if not isinstance(original_name, str) or not original_name.strip():
                logger.warning("Ignoring ontology entity without a usable name")
                continue
            original_name = original_name.strip()
            normalized_name = _to_pascal_case(original_name)
            if normalized_name == "Unknown":
                continue
            if normalized_name in seen_entity_names:
                logger.warning(f"Duplicate entity type '{normalized_name}' removed during validation")
                entity_name_map[original_name] = normalized_name
                entity_name_map[original_name.lower()] = normalized_name
                continue

            if normalized_name != original_name:
                logger.warning(
                    f"Entity type name '{original_name}' auto-converted to '{normalized_name}'"
                )
            entity["name"] = normalized_name
            entity["attributes"] = normalize_ontology_attributes(
                entity.get("attributes", [])
            )
            if not isinstance(entity.get("examples"), list):
                entity["examples"] = []
            description = entity.get("description")
            if not isinstance(description, str) or not description:
                description = f"A {normalized_name} entity."
            entity["description"] = (
                description[:97] + "..." if len(description) > 100 else description
            )

            seen_entity_names.add(normalized_name)
            processed_entities.append(entity)
            entity_name_map[original_name] = normalized_name
            entity_name_map[original_name.lower()] = normalized_name
            entity_name_map[normalized_name] = normalized_name
            entity_name_map[normalized_name.lower()] = normalized_name

        result["entity_types"] = processed_entities

        # fallback type definition
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # check if fallback type already exists
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # need add of fallback type
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # if add will exceed 10 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT，need remove some existing type
            if current_count + needed_slots > MAX_ONTOLOGY_TYPES:
                # calculate need remove how much
                to_remove = current_count + needed_slots - MAX_ONTOLOGY_TYPES
                # from end remove（Keep the previous more important of specific type）
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # add fallback type
            result["entity_types"].extend(fallbacks_to_add)
        
        # most final ensure does not exceed the limit（defensive programming）
        result["entity_types"] = result["entity_types"][:MAX_ONTOLOGY_TYPES]

        # Resolve edge endpoints only after entity fallback/capping, so an edge
        # cannot refer to a type that was removed to satisfy Zep's limits.
        valid_entity_names = {entity["name"] for entity in result["entity_types"]}
        for name in valid_entity_names:
            entity_name_map[name] = name
            entity_name_map[name.lower()] = name

        def resolve_entity_name(value: str) -> Optional[str]:
            stripped = value.strip()
            if stripped == "Entity":
                return stripped
            mapped = entity_name_map.get(stripped) or entity_name_map.get(stripped.lower())
            if mapped in valid_entity_names:
                return mapped
            pascal_name = _to_pascal_case(stripped)
            return pascal_name if pascal_name in valid_entity_names else None

        raw_key_entities = result.get("key_entities")
        if not isinstance(raw_key_entities, list):
            raw_key_entities = []

        processed_key_entities = []
        seen_key_entities = set()
        for raw in raw_key_entities[:200]:
            if isinstance(raw, str):
                item = {"name": raw}
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                continue

            name = str(item.get("name") or "").strip()
            if not name or name.lower() in seen_key_entities:
                continue

            entity_type = resolve_entity_name(str(item.get("type") or ""))
            if entity_type is None:
                entity_type = "Person"

            aliases = item.get("aliases")
            if not isinstance(aliases, list):
                aliases = []

            processed_key_entities.append({
                "name": name[:120],
                "type": entity_type,
                "role": str(item.get("role") or "")[:200],
                "aliases": [
                    str(alias)[:80]
                    for alias in aliases[:8]
                    if str(alias).strip()
                ],
                "evidence": str(item.get("evidence") or "")[:300],
                "summary": str(
                    item.get("summary")
                    or item.get("evidence")
                    or item.get("role")
                    or ""
                )[:400],
                "inferred": bool(item.get("inferred", False)),
            })
            seen_key_entities.add(name.lower())

        result["key_entities"] = processed_key_entities[:120]

        processed_edges: List[Dict[str, Any]] = []
        seen_edge_names = set()
        for raw_edge in raw_edges:
            if isinstance(raw_edge, str):
                # A bare edge name has no endpoints and cannot be installed in
                # Zep safely. Ignore it instead of inventing a relationship.
                logger.warning(f"Ignoring ontology edge without source_targets: {raw_edge}")
                continue
            elif isinstance(raw_edge, dict):
                edge = dict(raw_edge)
            else:
                logger.warning("Ignoring non-object ontology edge entry")
                continue

            original_name = edge.get("name")
            if not isinstance(original_name, str) or not original_name.strip():
                logger.warning("Ignoring ontology edge without a usable name")
                continue
            normalized_name = _to_upper_snake_case(original_name)
            if normalized_name == "UNKNOWN" or normalized_name in seen_edge_names:
                if normalized_name in seen_edge_names:
                    logger.warning(f"Duplicate edge type '{normalized_name}' removed during validation")
                continue
            if normalized_name != original_name:
                logger.warning(
                    f"Edge type name '{original_name}' auto-converted to '{normalized_name}'"
                )
            edge["name"] = normalized_name

            normalized_targets = []
            for source_target in normalize_ontology_source_targets(
                edge.get("source_targets", []),
                limit=None,
            ):
                source = resolve_entity_name(source_target["source"])
                target = resolve_entity_name(source_target["target"])
                if source and target:
                    normalized_targets.append({"source": source, "target": target})
            edge["source_targets"] = normalize_ontology_source_targets(
                normalized_targets
            )
            edge["attributes"] = normalize_ontology_attributes(
                edge.get("attributes", [])
            )
            description = edge.get("description")
            if not isinstance(description, str) or not description:
                description = f"A {normalized_name} relationship."
            edge["description"] = (
                description[:97] + "..." if len(description) > 100 else description
            )

            seen_edge_names.add(normalized_name)
            processed_edges.append(edge)
            if len(processed_edges) == MAX_ONTOLOGY_TYPES:
                break

        result["edge_types"] = processed_edges
        
        return result
