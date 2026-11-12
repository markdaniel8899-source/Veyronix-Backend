"""
OASIS Agent Profile generater
Convert of entity in local graph for OASIS simulation platform required of Agent Profile format

Optimization and improvements：
1. use local SQLite Rich node information
2. Optimize hint word generate very detailed of persona
3. Distinguish between human entity and abstract group entity
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from openai import OpenAI
from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, get_locale, set_locale, t
from ..utils.openai_chat_compat import create_chat_completion, extract_chat_completion_text
from .entity_utils import (
    entity_attributes,
    entity_name,
    entity_summary,
    entity_type_of,
    entity_uuid,
)
from .local_memory import LocalMemory

logger = get_logger('Veyronix.oasis_profile')


def _coerce_to_str(value: Any) -> str:
    """Coerce a value to a plain string.

    Handles dict, list, and other non-string types that may be returned
    by LLM JSON parsing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('text', 'value', 'description', 'content', 'summary', 'name'):
            if key in value:
                candidate = _coerce_to_str(value[key])
                if candidate:
                    return candidate
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        str_items = [_coerce_to_str(item) for item in value]
        str_items = [item for item in str_items if item]
        return ', '.join(str_items)
    return str(value)


def _coerce_to_str_list(value: Any) -> List[str]:
    """Coerce a value to a list of strings.

    Handles nested structures that may be returned by LLM JSON parsing.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                result.extend(_coerce_to_str_list(item))
            else:
                text = _coerce_to_str(item)
                if text:
                    result.append(text)
        return result
    text = _coerce_to_str(value)
    return [text] if text else []


@dataclass
class OasisAgentProfile:
    """OASIS Agent Profile data structure"""
    # general field
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str

    # optional field - Reddit style
    karma: int = 1000
    
    # optional field - Twitter style
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # additional persona information
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)

    # ===== Simulation behaviour =====
    # Stance towards the simulated topic: supportive / opposing / neutral / observer
    stance: str = "neutral"
    # Preferred platform for this persona: twitter / reddit / linkedin / facebook / blog
    platform: str = "twitter"
    # Typical tone of voice, used to keep the writing style consistent
    tone: str = "neutral"
    # The point of view this persona brings to the discussion
    perspective: str = ""
    # Region / location the persona is based in
    region: str = ""
    
    # Source entity information
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def __post_init__(self):
        """Normalize structured LLM fields once at the profile boundary."""
        self.bio = _coerce_to_str(self.bio) or self.name
        self.persona = _coerce_to_str(self.persona) or (
            f"{self.name} is a participant in social discussions."
        )
        self.country = _coerce_to_str(self.country) or None
        self.profession = _coerce_to_str(self.profession) or None
        self.gender = _coerce_to_str(self.gender) or None
        self.mbti = _coerce_to_str(self.mbti) or None
        self.interested_topics = _coerce_to_str_list(self.interested_topics)
        self.stance = _coerce_to_str(self.stance) or "neutral"
        self.platform = _coerce_to_str(self.platform) or "twitter"
        self.tone = _coerce_to_str(self.tone) or "neutral"
        self.perspective = _coerce_to_str(self.perspective) or ""
        self.region = _coerce_to_str(self.region) or ""

    def to_reddit_format(self) -> Dict[str, Any]:
        """convert for Reddit platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS The library requires field name for username（none underscore）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # add additional persona information（if there is）
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        # Simulation behaviour fields (stance, platform, tone, perspective)
        profile["stance"] = self.stance
        profile["platform"] = self.platform
        profile["tone"] = self.tone
        if self.perspective:
            profile["perspective"] = self.perspective
        if self.region:
            profile["region"] = self.region
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """convert for Twitter platform format"""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # OASIS The library requires field name for username（none underscore）
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # add additional persona information
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        # Simulation behaviour fields (stance, platform, tone, perspective)
        profile["stance"] = self.stance
        profile["platform"] = self.platform
        profile["tone"] = self.tone
        if self.perspective:
            profile["perspective"] = self.perspective
        if self.region:
            profile["region"] = self.region
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """convert for characters complete format"""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS Profile generater
    
    Will Zep of entity convert for in graph OASIS simulation required of Agent Profile
    
    Optimization features：
    1. call Zep graph retrieve function get richer of context
    2. generate very detailed of persona（Include basic information、Career experience、Character traits、social media behavior etc.）
    3. Distinguish between human entity and abstract group entity
    """
    
    # MBTI type list
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Common countries list
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]
    
    # person type entity（need generate specific persona）
    INDIVIDUAL_ENTITY_TYPES = [
        "student", "alumni", "professor", "person", "publicfigure", 
        "expert", "faculty", "official", "journalist", "activist"
    ]
    
    # group/organization type entity（need generate group representative persona）
    GROUP_ENTITY_TYPES = [
        "university", "governmentagency", "organization", "ngo", 
        "mediaoutlet", "company", "institution", "group", "community"
    ]
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        zep_api_key: Optional[str] = None,  # kept for backwards compatibility, unused
        graph_id: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        # Local SQLite memory provides context instead of Zep Cloud.
        self.memory = LocalMemory(Config.DATABASE_PATH)
        self.graph_id = graph_id
    
    def generate_profile_from_entity(
        self, 
        entity: Dict[str, Any], 
        user_id: int,
        use_llm: bool = True
    ) -> OasisAgentProfile:
        """
        from local entity generate OASIS Agent Profile
        
        Args:
            entity: Local entity characters dictionary（LocalMemory / entity_utils normalized format）
            user_id: user ID（used for OASIS）
            use_llm: whether to use LLM generate detailed persona
            
        Returns:
            OasisAgentProfile
        """
        entity_type = entity_type_of(entity) or "Entity"
        
        # Basic information
        name = entity_name(entity)
        summary = entity_summary(entity)
        attributes = entity_attributes(entity)
        user_name = self._generate_username(name)
        
        # build context information
        context = self._build_entity_context(entity)
        
        if use_llm:
            # use LLM generate detailed persona
            profile_data = self._generate_profile_with_llm(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=summary,
                entity_attributes=attributes,
                context=context
            )
        else:
            # use rule generate base persona
            profile_data = self._generate_profile_rule_based(
                entity_name=name,
                entity_type=entity_type,
                entity_summary=summary,
                entity_attributes=attributes
            )
        
        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity_uuid(entity),
            source_entity_type=entity_type,
            stance=str(attributes.get("stance") or entity.get("stance") or "neutral"),
            platform=str(attributes.get("platform") or "twitter"),
            tone=str(attributes.get("tone") or "neutral"),
            perspective=str(attributes.get("perspective") or ""),
            region=str(attributes.get("region") or ""),
        )
    
    def _simulation_behaviour(self, entity_attributes: Dict[str, Any]) -> Dict[str, str]:
        """Resolve the simulation behaviour values of a cast member."""
        attributes = entity_attributes or {}
        return {
            "stance": str(attributes.get("stance") or "neutral"),
            "platform": str(attributes.get("platform") or "twitter"),
            "tone": str(attributes.get("tone") or "neutral"),
            "profession": str(attributes.get("profession") or "participant"),
            "region": str(attributes.get("region") or "unspecified"),
            "age_band": str(attributes.get("age_band") or "25-34"),
            "perspective": str(
                attributes.get("perspective")
                or "A regular participant who reacts to what the others say."
            ),
        }

    def _generate_username(self, name: str) -> str:
        """generate username"""
        # remove special characters，convert for lowercase
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # add random suffix to avoid duplication
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_local_memory_for_entity(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """
        use local SQLite memory store get entity related of rich information。
        
        Args:
            entity: entity characters dictionary
            
        Returns:
            include facts, node_summaries, context of characters dictionary
        """
        results: Dict[str, Any] = {
            "facts": [],
            "node_summaries": [],
            "context": "",
        }
        
        name = entity_name(entity)
        if not name:
            return results
        
        try:
            related_agents = self.memory.search_agents(name, limit=10)
            interactions = self.memory.search_interactions(name, limit=20)
        except Exception as exc:
            logger.warning(f"local memory retrieval failed ({name}): {exc}")
            return results
        
        agent_summaries = []
        for agent in related_agents:
            persona = agent.get("persona") or {}
            summary = (
                persona.get("persona")
                or persona.get("bio")
                or persona.get("description")
                or ""
            )
            if summary:
                agent_summaries.append(str(summary)[:300])
        
        facts = []
        for interaction in interactions:
            content = str(interaction.get("content") or "").strip()
            if content:
                facts.append(content[:300])
        
        results["facts"] = facts
        results["node_summaries"] = agent_summaries
        
        context_parts = []
        if facts:
            context_parts.append("Facts:\n" + "\n".join(f"- {f}" for f in facts[:20]))
        if agent_summaries:
            context_parts.append("Related entities:\n" + "\n".join(f"- {s}" for s in agent_summaries[:10]))
        results["context"] = "\n\n".join(context_parts)
        
        logger.debug(
            "local memory retrieve completed: %s, get %d fact, %d Related nodes",
            name, len(facts), len(agent_summaries),
        )
        return results
    
    def _build_entity_context(self, entity: Dict[str, Any]) -> str:
        """
        build entity of complete context information
        
        include：
        1. entity itself of attribute information
        2. Related edge information（fact/relationship，If stored in）
        3. associated node of detailed information（If stored in）
        4. local memory store retrieve to of rich information
        """
        context_parts = []
        
        # 1. add entity attribute information
        attributes = entity_attributes(entity)
        if attributes:
            attrs = []
            for key, value in attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Entity attributes\n" + "\n".join(attrs))
        
        # 2. add related edge information（fact/relationship）
        existing_facts = set()
        related_edges = entity.get("related_edges") or []
        if isinstance(related_edges, list):
            relationships = []
            for edge in related_edges:
                if not isinstance(edge, dict):
                    continue
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity_name(entity)} --[{edge_name}]--> (related entity)")
                    else:
                        relationships.append(f"- (related entity) --[{edge_name}]--> {entity_name(entity)}")
            
            if relationships:
                context_parts.append("### Related facts and relationships\n" + "\n".join(relationships))
        
        # 3. add association node of detailed information
        related_nodes = entity.get("related_nodes") or []
        if isinstance(related_nodes, list):
            related_info = []
            for node in related_nodes:
                if not isinstance(node, dict):
                    continue
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # filter out default label
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### Related entity information\n" + "\n".join(related_info))
        
        # 4. use local memory store retrieve get richer of information
        local_results = self._search_local_memory_for_entity(entity)
        
        if local_results.get("facts"):
            # Remove duplicates：Exclude existing in of fact
            new_facts = [f for f in local_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Facts from local memory\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if local_results.get("node_summaries"):
            context_parts.append("### Related nodes from local memory\n" + "\n".join(f"- {s}" for s in local_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _is_individual_entity(self, entity_type: str) -> bool:
        """Determine whether it is a person type entity"""
        return entity_type.lower() in self.INDIVIDUAL_ENTITY_TYPES
    
    def _is_group_entity(self, entity_type: str) -> bool:
        """Determine whether it is a group/organization type entity"""
        return entity_type.lower() in self.GROUP_ENTITY_TYPES
    
    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> Dict[str, Any]:
        """
        use LLM generate very detailed of persona
        
        Distinguish based on entity type：
        - person entity：generate specific of character setting
        - group/organization entity：generate representative account settings
        """
        
        is_individual = self._is_individual_entity(entity_type)
        
        if is_individual:
            prompt = self._build_individual_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )
        else:
            prompt = self._build_group_persona_prompt(
                entity_name, entity_type, entity_summary, entity_attributes, context
            )

        # Try multiple times generate，to success or to maximum retry times count
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = create_chat_completion(
                    self.client,
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt(is_individual)},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1),  # Retry every times to lower the temperature
                    # No settings max_tokens，let LLM free play
                )
                
                content = extract_chat_completion_text(response)
                
                # check whether it is truncated（finish_reason no'stop'）
                finish_reason = response.choices[0].finish_reason
                if finish_reason == 'length':
                    logger.warning(f"LLM output is truncated (attempt {attempt+1}), try to fix...")
                    content = self._fix_truncated_json(content)
                
                # try parse JSON
                try:
                    result = json.loads(content)
                    
                    # validate required field
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} is a {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON parse failed (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # try to fix JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    if result.get("_fixed"):
                        del result["_fixed"]
                        return result
                    
                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(1 * (attempt + 1))  # Refers to count backoff
        
        logger.warning(f"LLM generate persona failed（{max_attempts}times try）: {last_error}, use rule generate")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Fix truncated of JSON（output is max_tokens Limit truncation）"""
        import re
        
        # if JSON truncated，try to close it
        content = content.strip()
        
        # calculate unclosed of brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check whether there is an unclosed character string
        # Simple check：if most after the quotation mark no comma or closing bracket，possibly is characters string truncated
        if content and content[-1] not in '",}]':
            # Try to close the characters string
            content += '"'
        
        # closing bracket
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Try to repair the damage of JSON"""
        import re
        
        # 1. First try to fix the truncated of situation
        content = self._fix_truncated_json(content)
        
        # 2. Try to extract JSON partial
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. Processing the of character in the characters string question
            # Find the string value to all characters and replace where of replaces the line character
            def fix_string_newlines(match):
                s = match.group(0)
                # replace characters within the string of the actual line replacement character for space
                s = s.replace('\n', ' ').replace('\r', ' ')
                # replace extra spaces
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # match JSON characters string value
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. try parse
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. if or failed，Try a more aggressive fix
                try:
                    # remove all control characters
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # replace all consecutive blanks
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. Try to extract partial information from content
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # possibly truncated
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} is a {entity_type}.")
        
        # if extract to meaningful of content，Mark for fixed
        if bio_match or persona_match:
            logger.info(f"from damage of JSON Extract partial information from")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. completely failed，return infrastructure
        logger.warning(f"JSON Repair failed，return infrastructure")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} is a {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Get the system prompt for profile generation."""
        base_prompt = (
            "You are an expert at generating social media personas. Create detailed, "
            "realistic personas for opinion simulations while staying faithful to the "
            "provided source material. Return valid JSON only. All string values must "
            "be single-line and properly escaped."
        )
        return f"{base_prompt}\n\n{get_language_instruction()}"
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """build person entity of detailed persona hint word"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "No additional context"
        behaviour = self._simulation_behaviour(entity_attributes)
        
        return f"""Generate a detailed social media persona for the entity below, staying faithful to the available source material.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

SIMULATION BEHAVIOUR (the simulation engine uses these, keep them consistent):
- Stance towards the topic: {behaviour['stance']}
- Home platform: {behaviour['platform']}
- Typical tone of voice: {behaviour['tone']}
- Profession: {behaviour['profession']}
- Region: {behaviour['region']}
- Age group: {behaviour['age_band']}
- Unique perspective: {behaviour['perspective']}

Return JSON with these fields:

1. bio: short social media bio (max 200 characters)
2. persona: detailed persona description (plain text, about 2000 characters), covering:
   - basics (age, profession, education, location)
   - background (key experiences, connection to the event, social relationships)
   - personality (MBTI type, core traits, emotional expression)
   - social media behavior (posting frequency, content preferences, interaction style, language habits)
   - views (stance on the topic, what may provoke or move them)
   - distinctive traits (catchphrases, unusual experiences, hobbies)
   - personal memory (the entity's connection to the event and previous actions/reactions)
3. age: integer age
4. gender: exactly "male" or "female"
5. mbti: MBTI type (e.g. INTJ, ENFP)
6. country: country name in English
7. profession: profession
8. interested_topics: array of topics

Important:
- All field values must be strings or numbers, with no raw newlines.
- persona must be one coherent paragraph.
- {get_language_instruction()} (the gender field must stay in English: male/female)
- Keep the content consistent with the entity information.
- age must be a valid integer; gender must be "male" or "female".
- The persona must sound like one specific person, not a generic archetype.
- Stay inside the assigned stance, platform, tone and perspective: the
  simulation depends on every persona being clearly distinct from the others.
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str
    ) -> str:
        """build group/organization entity of detailed persona hint word"""
        
        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "No additional context"
        behaviour = self._simulation_behaviour(entity_attributes)
        
        return f"""Generate a detailed social media account persona for the organization or group below, staying faithful to the available source material.

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

SIMULATION BEHAVIOUR (the simulation engine uses these, keep them consistent):
- Stance towards the topic: {behaviour['stance']}
- Home platform: {behaviour['platform']}
- Typical tone of voice: {behaviour['tone']}
- Organisation focus: {behaviour['profession']}
- Region: {behaviour['region']}
- Unique perspective: {behaviour['perspective']}

Return JSON with these fields:

1. bio: official account bio (max 200 characters), professional and polished
2. persona: detailed account description (plain text, about 2000 characters), covering:
   - organization basics (official name, nature, founding background, main functions)
   - account positioning (account type, target audience, core purpose)
   - voice and style (language habits, common expressions, sensitive topics)
   - content profile (content types, posting frequency, active hours)
   - official stance (position on the core topic, handling of controversy)
   - special notes (represented community, operating habits)
   - organizational memory (the organization's connection to the event and previous actions/reactions)
3. age: fixed value 30 (virtual age for an organization account)
4. gender: fixed value "other" (organizations are not individuals)
5. mbti: MBTI type describing the account style, e.g. ISTJ for formal and conservative
6. country: country name in English
7. profession: description of the organization's function
8. interested_topics: array of focus areas

Important:
- All field values must be strings or numbers. Null values are not allowed.
- persona must be one coherent paragraph without raw newlines.
- {get_language_instruction()} (the gender field must stay in English: "other")
- age must be the integer 30; gender must be the string "other".
- The account's voice must match its identity.
- Stay inside the assigned stance, platform, tone and perspective: the
  simulation depends on every account being clearly distinct from the others."""
    
    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """use rule generate base persona"""
        
        # Different according to entity type generate of persona
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # virtual age for organization accounts
                "gender": "other",  # organizations use "other"
                "mbti": "ISTJ",  # formal and conservative account style
                "country": "China",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # virtual age for organization accounts
                "gender": "other",  # organizations use "other"
                "mbti": "ISTJ",  # formal and conservative account style
                "country": "China",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # default persona
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def set_graph_id(self, graph_id: str):
        """settings graph ID used for Zep retrieve"""
        self.graph_id = graph_id

    # ------------------------------------------------------------------
    # Cast expansion
    # ------------------------------------------------------------------
    # A knowledge graph usually yields only a handful of nodes. A believable
    # social ecosystem needs a full cast, so the graph entities are turned
    # into a diverse set of personas: different professions, demographics,
    # stances, platforms and tones. Everything here is project agnostic, it
    # only depends on the ontology types and the uploaded document.

    CAST_FIRST_NAMES = [
        "Amina", "Bruno", "Chen", "Dalia", "Elif", "Farid", "Grace", "Hassan",
        "Ines", "Jonas", "Kaito", "Leila", "Mateo", "Nadia", "Omar", "Priya",
        "Quinn", "Rosa", "Sven", "Tara", "Umar", "Vera", "Wei", "Ximena",
        "Yara", "Zane", "Anika", "Bilal", "Carmen", "Diego", "Esra", "Felix",
    ]

    CAST_LAST_NAMES = [
        "Ahmed", "Bergman", "Costa", "Dubois", "Evans", "Fontaine", "Garcia",
        "Haddad", "Ibrahim", "Jansen", "Kowalski", "Lindqvist", "Moreau",
        "Nakamura", "Okafor", "Petrov", "Quintero", "Rossi", "Silva", "Tanaka",
        "Ubeda", "Vasquez", "Weber", "Xu", "Yilmaz", "Zhang", "Andersen", "Baker",
        "Cruz", "Duarte", "Engel", "Fischer",
    ]

    CAST_PROFESSIONS = [
        "student", "postgraduate researcher", "teacher", "journalist",
        "policy analyst", "investor", "regulator", "industry analyst",
        "software engineer", "product manager", "marketing lead", "NGO coordinator",
        "union representative", "small business owner", "parent", "retiree",
        "content creator", "independent blogger", "legal consultant",
        "public health specialist", "economist", "urban planner", "activist",
        "community organiser", "freelance designer", "supply chain manager",
    ]

    CAST_REGIONS = [
        "Europe", "North America", "South America", "Middle East",
        "South Asia", "East Asia", "Southeast Asia", "Africa",
        "Northern Europe", "Southern Europe", "the Pacific", "the Balkans",
    ]

    CAST_AGE_BANDS = [
        "18-24", "25-34", "35-44", "45-54", "55-64", "65+",
    ]

    CAST_TONES = [
        "analytical", "emotional", "sarcastic", "measured", "outspoken",
        "cautious", "enthusiastic", "dry and factual", "passionate",
        "ironic", "reassuring", "blunt",
    ]

    CAST_PLATFORMS = ["twitter", "reddit", "linkedin", "facebook", "blog"]

    # 30% supportive, 40% critical, 30% neutral/observer
    CAST_STANCE_MIX = (
        ["supportive"] * 30
        + ["opposing"] * 40
        + ["neutral"] * 30
    )

    @classmethod
    def _stance_for_index(cls, index: int) -> str:
        """Return the stance of a cast member (default mix)."""
        mix = cls._shuffled_stance_mix()
        return mix[index % len(mix)]

    @classmethod
    def _stance_mix_for_size(cls, size: int) -> List[str]:
        """Build a stance mix with the exact 30/40/30 ratio for a cast size.

        The mix is shuffled so the cast is not blocked by stance, but the
        ratio stays exact whatever the number of personas is.
        """
        size = max(int(size), 0)
        supportive = int(round(size * 0.3))
        opposing = int(round(size * 0.4))
        neutral = max(size - supportive - opposing, 0)
        mix = (
            ["supportive"] * supportive
            + ["opposing"] * opposing
            + ["neutral"] * neutral
        )
        random.Random(20260101).shuffle(mix)
        return mix

    @classmethod
    def _shuffled_stance_mix(cls) -> List[str]:
        """Deterministically shuffled stance mix."""
        cached = getattr(cls, "_stance_mix_cache", None)
        if cached is None:
            cached = list(cls.CAST_STANCE_MIX)
            random.Random(20260101).shuffle(cached)
            cls._stance_mix_cache = cached
        return cached

    def expand_entity_cast(
        self,
        entities: List[Dict[str, Any]],
        target_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Expand a small entity list into a large, diverse persona cast.

        The original entities are kept (they carry the graph context) and
        additional personas are derived from their types so the simulation
        behaves like a real community instead of a handful of isolated
        speakers.

        Args:
            entities: the entities extracted from the document.
            target_count: how many personas the simulation should have.

        Returns:
            A new list of entities, each one annotated with a stance,
            platform, tone, profession and perspective.
        """
        target = int(
            target_count
            if target_count is not None
            else Config.SIMULATION_MIN_ENTITIES
        )
        # Never drop real entities, but never exceed the hard cap.
        target = max(len(entities), target)
        target = min(target, Config.SIMULATION_MAX_ENTITIES)
        source_entities = entities[:target]

        cast: List[Dict[str, Any]] = []
        used_names: set[str] = set()
        # Exact 30/40/30 stance ratio for the size of the cast.
        stance_mix = self._stance_mix_for_size(target)

        for entity in source_entities:
            cast.append(
                self._annotate_cast_member(
                    entity, len(cast), used_names, stance_mix
                )
            )

        if not entities:
            # No graph nodes at all: build the cast from generic roles.
            for _ in range(target):
                cast.append(
                    self._synthetic_cast_member(
                        len(cast), used_names, None, stance_mix
                    )
                )
            return cast

        index = 0
        while len(cast) < target:
            base = source_entities[index % len(source_entities)]
            cast.append(
                self._synthetic_cast_member(
                    len(cast), used_names, base, stance_mix
                )
            )
            index += 1

        return cast

    def _annotate_cast_member(
        self,
        entity: Dict[str, Any],
        index: int,
        used_names: set[str],
        stance_mix: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Add the simulation behaviour fields to an existing entity."""
        annotated = dict(entity)
        attributes = dict(entity_attributes(entity) or {})
        stance = (
            stance_mix[index % len(stance_mix)]
            if stance_mix
            else self._stance_for_index(index)
        )

        attributes.setdefault("stance", stance)
        attributes.setdefault("platform", self.CAST_PLATFORMS[index % len(self.CAST_PLATFORMS)])
        attributes.setdefault("tone", self.CAST_TONES[index % len(self.CAST_TONES)])
        attributes.setdefault("profession", self.CAST_PROFESSIONS[index % len(self.CAST_PROFESSIONS)])
        attributes.setdefault("region", self.CAST_REGIONS[index % len(self.CAST_REGIONS)])
        attributes.setdefault("age_band", self.CAST_AGE_BANDS[index % len(self.CAST_AGE_BANDS)])
        attributes.setdefault(
            "perspective",
            f"A {attributes.get('profession', 'participant')} from "
            f"{attributes.get('region', 'unspecified')} who reacts to what the "
            f"others say.",
        )
        annotated["attributes"] = attributes
        annotated["stance"] = stance
        return annotated

    def _synthetic_cast_member(
        self,
        index: int,
        used_names: set[str],
        base: Optional[Dict[str, Any]] = None,
        stance_mix: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create one additional persona derived from an entity type."""
        base_type = entity_type_of(base) if base else "Person"
        base_summary = entity_summary(base) if base else ""

        name = self._unique_cast_name(index, used_names)
        profession = self.CAST_PROFESSIONS[index % len(self.CAST_PROFESSIONS)]
        region = self.CAST_REGIONS[index % len(self.CAST_REGIONS)]
        age_band = self.CAST_AGE_BANDS[index % len(self.CAST_AGE_BANDS)]
        stance = (
            stance_mix[index % len(stance_mix)]
            if stance_mix
            else self._stance_for_index(index)
        )
        platform = self.CAST_PLATFORMS[index % len(self.CAST_PLATFORMS)]
        tone = self.CAST_TONES[index % len(self.CAST_TONES)]
        perspective = (
            f"A {profession} from {region} who looks at the topic from the "
            f"point of view of a {age_band} {profession}."
        )

        attributes = {
            "profession": profession,
            "region": region,
            "age_band": age_band,
            "stance": stance,
            "platform": platform,
            "tone": tone,
            "perspective": perspective,
            "expanded": True,
        }

        summary = (
            f"{name} is a {profession} based in {region} ({age_band}). "
            f"{perspective}"
        )
        if base_summary:
            summary = f"{summary} Connected to the topic through: {base_summary[:180]}"

        return {
            "uuid": f"cast_{name.lower().replace(' ', '_')}_{index}",
            "name": name,
            "type": base_type or "Person",
            "labels": [base_type or "Person"],
            "summary": summary,
            "attributes": attributes,
            "stance": stance,
            "graph_id": (base or {}).get("graph_id"),
        }

    def _unique_cast_name(self, index: int, used_names: set[str]) -> str:
        """Build a stable, unique display name for a cast member."""
        first = self.CAST_FIRST_NAMES[index % len(self.CAST_FIRST_NAMES)]
        last = self.CAST_LAST_NAMES[
            (index // len(self.CAST_FIRST_NAMES)) % len(self.CAST_LAST_NAMES)
        ]
        name = f"{first} {last}"
        suffix = 2
        while name in used_names:
            name = f"{first} {last} {suffix}"
            suffix += 1
        used_names.add(name)
        return name

    def generate_profiles_from_entities(
        self,
        entities: List[Dict[str, Any]],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        batch from entity generate Agent Profile（support and line generate）
        
        Args:
            entities: entity list
            use_llm: whether to use LLM generate detailed persona
            progress_callback: progress callback function count (current, total, message)
            graph_id: graph ID，used for Zep retrieve get richer context
            parallel_count: and line generate count，default 5
            realtime_output_path: real-time writing of file path（if provides，Write times every time generate）
            output_platform: output platform format ("reddit" or "twitter")
            
        Returns:
            Agent Profile list
        """
        import concurrent.futures
        from threading import Lock
        
        # settings graph_id used for Zep retrieve
        if graph_id:
            self.graph_id = graph_id
        
        total = len(entities)
        profiles = [None] * total  # pre-allocated list stable order
        completed_count = [0]  # use list to modify in closure
        lock = Lock()
        
        # Real-time writing to file of auxiliary function count
        def save_profiles_realtime():
            """real-time save has generated of profiles to file"""
            if not realtime_output_path:
                return
            
            with lock:
                # filter out has generated of profiles
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Reddit JSON format
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV format
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"real time save profiles failed: {e}")
        
        # Capture locale before spawning thread pool workers
        current_locale = get_locale()

        def generate_single_profile(idx: int, entity: Dict[str, Any]) -> tuple:
            """generate single profile of work letter count"""
            set_locale(current_locale)
            entity_type = entity_type_of(entity)
            name = entity_name(entity)
            
            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=idx,
                    use_llm=use_llm
                )
                
                # Real-time output generate of persona to console and log
                self._print_generated_profile(name, entity_type, profile)
                
                return idx, profile, None
                
            except Exception as e:
                logger.error(f"generate entity {name} of persona failed: {str(e)}")
                # create a base profile
                fallback_profile = OasisAgentProfile(
                    user_id=idx,
                    user_name=self._generate_username(name),
                    name=name,
                    bio=f"{entity_type}: {name}",
                    persona=entity_summary(entity) or f"A participant in social discussions.",
                    source_entity_uuid=entity_uuid(entity),
                    source_entity_type=entity_type,
                )
                return idx, fallback_profile, str(e)
        
        logger.info(f"start and line generate {total} NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT Agent persona（and line count: {parallel_count}）...")
        print(f"\n{'='*60}")
        print(f"start generate Agent persona - common {total} entity，and line count: {parallel_count}")
        print(f"{'='*60}\n")
        
        # use thread pool and execute line
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # submit all tasks
            future_to_entity = {
                executor.submit(generate_single_profile, idx, entity): (idx, entity)
                for idx, entity in enumerate(entities)
            }
            
            # Collect results
            for future in concurrent.futures.as_completed(future_to_entity):
                idx, entity = future_to_entity[future]
                entity_type = entity_type_of(entity)
                name = entity_name(entity)
                
                try:
                    result_idx, profile, error = future.result()
                    profiles[result_idx] = profile
                    
                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Write to file in real time
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"Completed {current}/{total}: {name}（{entity_type}）"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {name} use alternate persona: {error}")
                    else:
                        logger.info(f"[{current}/{total}] success generate persona: {name} ({entity_type})")
                        
                except Exception as e:
                    logger.error(f"Process entity {name} exception occurs: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    profiles[idx] = OasisAgentProfile(
                        user_id=idx,
                        user_name=self._generate_username(name),
                        name=name,
                        bio=f"{entity_type}: {name}",
                        persona=entity_summary(entity) or "A participant in social discussions.",
                        source_entity_uuid=entity_uuid(entity),
                        source_entity_type=entity_type,
                    )
                    # Write to file in real time（Even a backup persona）
                    save_profiles_realtime()
        
        print(f"\n{'='*60}")
        print(f"persona generate completed！Total generate {len([p for p in profiles if p])} NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT Agent")
        print(f"{'='*60}\n")
        
        return profiles
    
    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Real-time output generate of persona to console（complete content，Do not truncate）"""
        separator = "-" * 70
        
        # build complete output content（Do not truncate）
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'none'
        
        output_lines = [
            f"\n{separator}",
            t('progress.profileGenerated', name=entity_name, type=entity_type),
            f"{separator}",
            f"username: {profile.user_name}",
            f"",
            f"【Introduction】",
            f"{profile.bio}",
            f"",
            f"【detailed persona】",
            f"{profile.persona}",
            f"",
            f"【Basic properties】",
            f"age: {profile.age} | gender: {profile.gender} | MBTI: {profile.mbti}",
            f"Profession: {profile.profession} | nation: {profile.country}",
            f"interest topic: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Only output to console（avoid duplication，logger no longer output complete content）
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        save Profile to file（Choose the correct format based on platform）
        
        OASIS platform format requirements：
        - Twitter: CSV format
        - Reddit: JSON format
        
        Args:
            profiles: Profile list
            file_path: file path
            platform: platform type ("reddit" or "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        save Twitter Profile for CSV format（conform to OASIS official request）
        
        OASIS Twitter Requirements of CSV field：
        - user_id: user ID（according to CSV order from 0 start）
        - name: user real name
        - username: of username in the system
        - user_char: detailed persona description（inject to LLM In system hint，guide Agent behavior）
        - description: Short of Public Profile（show in user profile pages）
        
        user_char vs description the difference：
        - user_char: internal use，LLM System hint，Decide Agent how to think and line move
        - description: external show，other user visible of Introduction
        """
        import csv
        
        # ensure file extension is.csv
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # write OASIS Requirements of header
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # write data line
            for idx, profile in enumerate(profiles):
                # user_char: complete persona（bio + persona），used for LLM System hint
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # Handle line breaks（CSV Replace with spaces in）
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: short introduction，for external show
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: from 0 start of order ID
                    profile.name,           # name: real name
                    profile.user_name,      # username: username
                    user_char,              # user_char: complete persona（internal LLM use）
                    description             # description: short introduction（external show）
                ]
                writer.writerow(row)
        
        logger.info(f"saved {len(profiles)} NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT Twitter Profile to {file_path} (OASIS CSV format)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        standardization gender field for OASIS Requirements of English format
        
        OASIS Require: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # Chinese mapping
        gender_map = {
            "male": "male",
            "female": "female",
            "organization": "other",
            "other": "other",
            # English Already
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        save Reddit Profile for JSON format
        
        use and to_reddit_format() consistent of format，ensure OASIS can read correctly。
        must include user_id field，This is OASIS agent_graph.get_agent() match of key！
        
        required field：
        - user_id: user ID（whole count，used to match initial_posts in of poster_agent_id）
        - username: username
        - name: show name
        - bio: Introduction
        - persona: detailed persona
        - age: age（whole count）
        - gender: "male", "female", or "other"
        - mbti: MBTI type
        - country: nation
        """
        data = []
        for idx, profile in enumerate(profiles):
            # use and to_reddit_format() consistent of format
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # key：must include user_id
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150],
                "persona": profile.persona,
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # OASIS required field - ensure has default value
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                "country": profile.country if profile.country else "China",
            }
            
            # optional field
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"saved {len(profiles)} NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT Reddit Profile to {file_path} (JSON format，include user_id field)")
    
    # Keep old method name as for alias，stable backward compatible
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[Deprecated] Please use save_profiles() method"""
        logger.warning("save_profiles_to_json Deprecated，Please use save_profiles method")
        self.save_profiles(profiles, file_path, platform)
