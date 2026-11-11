"""
simulation configuration intelligent generator
use LLM According to simulation requirement、Document content、graph information automatically generates meticulous of simulation parameter
Automate the entire process，none requires labor settings parameter

Adopt a step-by-step generate strategy，Avoid failures caused by one-time generation of excessively long content：
1. generate time configuration
2. generate event configuration
3. Batch Generate Agent configuration
4. generate platform configuration
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, t
from ..utils.openai_chat_compat import create_chat_completion, extract_chat_completion_text
from .entity_utils import (
    entity_name as get_entity_name,
    entity_summary as get_entity_summary,
    entity_type_of,
    entity_uuid as get_entity_uuid,
)

logger = get_logger('Veyronix.simulation_config')

# China Rest Time Configuration（Beijing time）
CHINA_TIMEZONE_CONFIG = {
    # Late Night（Almost none people active）
    "dead_hours": [0, 1, 2, 3, 4, 5],
    # Morning Session（Gradually waking up）
    "morning_hours": [6, 7, 8],
    # Opening Hours
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    # Evening High Peak（most active）
    "peak_hours": [19, 20, 21, 22],
    # Night time（Activity decline）
    "night_hours": [23],
    # Activity Scale count
    "activity_multipliers": {
        "dead": 0.05,      # Almost none in the early hours of the morning
        "morning": 0.4,    # Gradually active in the morning
        "work": 0.7,       # Working hours etc.
        "peak": 1.5,       # Evening High Peak
        "night": 0.5       # Late Night decline
    }
}


@dataclass
class AgentActivityConfig:
    """Unit Agent of activity configuration"""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # Activity configuration (0.0-1.0)
    activity_level: float = 0.5  # Overall Activity
    
    # speaking frequency（Expected speeches per hour times count）
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # Active time slots（24 hours system，0-23）
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # response Speed（Delayed response to thermal events of，Module：simulation minutes）
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # Emotional tendencies (-1.0 to 1.0，Negative to Positive)
    sentiment_bias: float = 0.0
    
    # stance（Attitude towards a specific topic of）
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # influence weight（Decides that its statement is to be made by other Agent See to of Probability）
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """time simulation configuration（Based on Chinese working habits）"""
    # total simulation duration（simulation hours count）
    total_simulation_hours: int = 72  # default simulation 72 hours（3 day）
    
    # Each rounds represents of time（simulation minutes）- default 60%d minute（1 hours），Increase the fast time flow rate
    minutes_per_round: int = 60
    
    # Activate of every hour Agent count range
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # high peak hours（Evening 19-22 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT，Chinese are most active of time）
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # low valley period（Tengah Malam 0-5 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT，Almost none people active）
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05  # Very low activity in the morning
    
    # Morning Session
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_activity_multiplier: float = 0.4
    
    # Opening Hours
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """event configuration"""
    # Init Events（trigger event at simulation start of）
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # Cron events（of event triggered at a specific time）
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Hot topic keyword
    hot_topics: List[str] = field(default_factory=list)
    
    # public opinion guidance direction
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """platform-specific configuration"""
    platform: str  # twitter or reddit
    
    # recommended algorithm weight
    recency_weight: float = 0.4  # time new freshness
    popularity_weight: float = 0.3  # hot topic
    relevance_weight: float = 0.3  # correlation
    
    # Virus Transmission Threshold（Trigger diffusion after reaching the number of interactions）
    viral_threshold: int = 10
    
    # Echo chamber effect intensity（Degree of convergence of similar views）
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """complete of simulation parameter configuration"""
    # Basic information
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # time configuration
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # Agent configuration list
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # event configuration
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # platform configuration
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None
    
    # LLM configuration
    llm_model: str = ""
    llm_base_url: str = ""
    
    # generate metadata
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""  # LLM of Inference description
    
    def to_dict(self) -> Dict[str, Any]:
        """convert for characters dictionary"""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """convert for JSON characters string"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    simulation configuration intelligent generator
    
    use LLM Analysis simulation requirement、Document content、graph entity information，
    Automatically generate the most optimal of simulation parameter configuration
    
    Adopt a step-by-step generate strategy：
    1. generate time configuration and event configuration（Lightweighting）
    2. Batch Generate Agent configuration（Each batch 10-20 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT）
    3. generate platform configuration
    """
    
    # Context maximum characters count
    MAX_CONTEXT_LENGTH = 50000
    # Each batch generates of Agent count
    AGENTS_PER_BATCH = 15
    
    # Each step of context truncation length（characters character count）
    TIME_CONFIG_CONTEXT_LENGTH = 10000   # time configuration
    EVENT_CONFIG_CONTEXT_LENGTH = 8000   # event configuration
    ENTITY_SUMMARY_LENGTH = 300          # entity summary
    AGENT_SUMMARY_LENGTH = 300           # Agent of entity summary in configuration
    ENTITIES_PER_TYPE_DISPLAY = 20       # Entity show count per class
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
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
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[Dict[str, Any]],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        Smart generate complete of simulation configuration（Step-by-step generate）
        
        Args:
            simulation_id: simulation ID
            project_id: project ID
            graph_id: graph ID
            simulation_requirement: simulation requirement description
            document_text: Original document content
            entities: after filterof entity list
            enable_twitter: enabled Twitter
            enable_reddit: enabled Reddit
            progress_callback: progress callback function count(current_step, total_steps, message)
            
        Returns:
            SimulationParameters: complete of simulation parameter
        """
        logger.info(f"start Smart generate simulation configuration: simulation_id={simulation_id}, entity count={len(entities)}")
        
        # calculate total step count
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # time configuration + event configuration + N Approval Agent + platform configuration
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. build base context information
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== step 1: generate time configuration ==========
        report_progress(1, t('progress.generatingTimeConfig'))
        num_entities = len(entities)
        time_config_result = self._generate_time_config(context, num_entities)
        time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"{t('progress.timeConfigLabel')}: {time_config_result.get('reasoning', t('common.success'))}")
        
        # ========== step 2: generate event configuration ==========
        report_progress(2, t('progress.generatingEventConfig'))
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"{t('progress.eventConfigLabel')}: {event_config_result.get('reasoning', t('common.success'))}")
        
        # ========== step 3-N: Batch Generate Agent configuration ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                t('progress.generatingAgentConfig', start=start_idx + 1, end=end_idx, total=len(entities))
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(t('progress.agentConfigResult', count=len(all_agent_configs)))
        
        # ========== for initial post assign publisher Agent ==========
        logger.info("for initial post assignment appropriate of publisher Agent...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(t('progress.postAssignResult', count=assigned_count))
        
        # ========== mostLast step: generate platform configuration ==========
        report_progress(total_steps, t('progress.generatingPlatformConfig'))
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # build most final parameter
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"simulation configuration generate completed: {len(params.agent_configs)} NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT Agent configuration")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[Dict[str, Any]]
    ) -> str:
        """build LLM Contextual，Truncate to maximum length"""
        
        # entity summary
        entity_summary = self._summarize_entities(entities)
        
        # build context
        context_parts = [
            f"## Simulation requirement\n{simulation_requirement}",
            f"\n## Entity information ({len(entities)} entities)\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # 留 500 characters character margin
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(document truncated)"
            context_parts.append(f"\n## Source document\n{doc_text}")
        
        return "\n".join(context_parts)
    
    def _summarize_entities(self, entities: List[Dict[str, Any]]) -> str:
        """generate entity summary"""
        lines = []
        
        # group by type
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for e in entities:
            t = entity_type_of(e) or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)})")
            # use configuration of show count and summary length
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary = get_entity_summary(e)
                name = get_entity_name(e)
                summary_preview = (summary[:summary_len] + "...") if len(summary) > summary_len else summary
                lines.append(f"- {name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... and {len(type_entities) - display_count} more")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """with retry of LLM call，include JSON Repair Logic"""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = create_chat_completion(
                    self.client,
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1),  # Retry every times to lower the temperature
                    # No settings max_tokens，let LLM free play
                )
                
                content = extract_chat_completion_text(response)
                finish_reason = response.choices[0].finish_reason
                
                # check whether it is truncated
                if finish_reason == 'length':
                    logger.warning(f"LLM output is truncated (attempt {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # try parse JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON parse failed (attempt {attempt+1}): {str(e)[:80]}")
                    
                    # try to fix JSON
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("LLM call failed")
    
    def _fix_truncated_json(self, content: str) -> str:
        """Fix truncated of JSON"""
        content = content.strip()
        
        # calculate unclosed of brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check whether there is an unclosed character string
        if content and content[-1] not in '",}]':
            content += '"'
        
        # closing bracket
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Try repairing the configuration JSON"""
        import re
        
        # Fix truncated of cases
        content = self._fix_truncated_json(content)
        
        # Ekstrak JSON partial
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # of swap line character in remove characters string
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # Try removing all control characters
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """generate time configuration"""
        # use configuration of context truncation length
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # calculate maximum allowable value（80%of agent Returns the number of elements in var, which is typically an array (since anything else will have one element).）
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""Generate a time simulation configuration based on the requirements below.

{context_truncated}

## Task
Return a time configuration as JSON.

### General principles (reference only; adapt to the event and audience):
- Infer the target audience's timezone and daily routine from the scenario. The example below uses UTC+8.
- Almost no activity from 00:00-05:00 (activity multiplier 0.05)
- Activity rises from 06:00-08:00 (multiplier 0.4)
- Moderate activity during work hours 09:00-18:00 (multiplier 0.7)
- Peak activity from 19:00-22:00 (multiplier 1.5)
- Activity declines after 23:00 (multiplier 0.5)
- General pattern: low overnight, rising in the morning, moderate during work, peak in the evening
- **Important**: these values are examples. Adjust the windows to the event and audience.
  - Example: students may peak at 21:00-23:00; media may be active all day; agencies only during work hours
  - Example: breaking news may cause late-night discussion; off_peak_hours may be shorter

### Return JSON only (no markdown)

Example:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [19, 20, 21, 22],
    "off_peak_hours": [0, 1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    "reasoning": "Why this time configuration fits the event"
}}

Field notes:
- total_simulation_hours (int): total simulated hours, 24-168. Breaking events are shorter; ongoing topics are longer.
- minutes_per_round (int): minutes per round, 30-120. 60 is recommended.
- agents_per_hour_min (int): minimum agents activated per hour (range: 1-{max_agents_allowed})
- agents_per_hour_max (int): maximum agents activated per hour (range: 1-{max_agents_allowed})
- peak_hours (array of int): peak hours, adapted to the audience
- off_peak_hours (array of int): low-activity hours, usually late night
- morning_hours (array of int): morning hours
- work_hours (array of int): work hours
- reasoning (string): brief explanation of the configuration"""

        system_prompt = "You are a social media simulation expert. Return pure JSON. The time configuration must match the target audience's daily routine."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"time configuration LLM generate failed: {e}, use default configuration")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """get default time configuration（Chinese work and rest）"""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # Per rounds 1 hours，Increase the fast time flow rate
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [19, 20, 21, 22],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "Default daily routine (60 minutes per round)"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """parse time configuration result，and validate agents_per_hour Value does not exceed total agent Returns the number of elements in var, which is typically an array (since anything else will have one element)."""
        # get original value
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # validate and correct：ensure no more than total agent Returns the number of elements in var, which is typically an array (since anything else will have one element).
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) More than total Agent Returns the number of elements in var, which is typically an array (since anything else will have one element). ({num_entities})，Remediated")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) More than total Agent Returns the number of elements in var, which is typically an array (since anything else will have one element). ({num_entities})，Remediated")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # ensure min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max，Fixed for {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # default per rounds 1 hours
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # Almost none in the early hours of the morning
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """generate event configuration"""
        
        # get available of entity type list，for LLM REFERENCE
        entity_types_available = list(set(
            entity_type_of(e) or "Unknown" for e in entities
        ))
        
        # for Representative entity name for each type column
        type_examples = {}
        for e in entities:
            etype = entity_type_of(e) or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(get_entity_name(e))
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # use configuration of context truncation length
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""Generate an event configuration for the simulation below.

Simulation requirement: {simulation_requirement}

{context_truncated}

## Available entity types and examples
{type_info}

## Task
Return an event configuration as JSON:
- Extract hot topic keywords
- Describe the direction of public discussion
- Draft initial posts. **Every post must specify poster_type (the publisher's entity type).**

**Important**: poster_type must be chosen from the "Available entity types" above so each initial post can be assigned to a suitable agent.
For example: official statements should come from an Official/University type, news from MediaOutlet, and student views from Student.

Return JSON only (no markdown):
{{
    "hot_topics": ["keyword1", "keyword2", ...],
    "narrative_direction": "<description of the discussion direction>",
    "initial_posts": [
        {{"content": "post content", "poster_type": "entity type (must be one of the available types)"}},
        ...
    ],
    "reasoning": "<brief explanation>"
}}"""

        system_prompt = "You are a public opinion analyst. Return pure JSON. poster_type must exactly match an available entity type."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}\nIMPORTANT: The 'poster_type' field value MUST be in English PascalCase exactly matching the available entity types. Only 'content', 'narrative_direction', 'hot_topics' and 'reasoning' fields should use the specified language."

        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"event configuration LLM generate failed: {e}, use default configuration")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "Using default configuration"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """parse event configuration result"""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        for initial post assignment appropriate of publisher Agent
        
        According to each post of poster_type match most Fit of agent_id
        """
        if not event_config.initial_posts:
            return event_config
        
        # created by entity type agent index
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # type mapping table（<g id=Bold>Medical Treatment:</g> LLM possibly output of different formats）
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media"],
            "student": ["student", "person"],
            "professor": ["professor", "expert", "teacher"],
            "alumni": ["alumni", "person"],
            "organization": ["organization", "ngo", "company", "group"],
            "person": ["person", "student", "alumni"],
        }
        
        # record each type used of agent index，Avoid duplicate use of the same agent
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # Try to find to match of agent
            matched_agent_id = None
            
            # 1. Direct match
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 2. use alias match
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            # 3. if still not found to，use influence most high of agent
            if matched_agent_id is None:
                logger.warning(f"Not found to type '{poster_type}' of match Agent，use influence most high of Agent")
                if agent_configs:
                    # by influence sort，Select influence most high of
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            logger.info(f"Initial post assignment: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[Dict[str, Any]],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        """Batch Generate Agent configuration"""
        
        # build entity information（use configuration of summary length）
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            summary = get_entity_summary(e)
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": get_entity_name(e),
                "entity_type": entity_type_of(e) or "Unknown",
                "summary": summary[:summary_len] if summary else ""
            })
        
        prompt = f"""Generate a social media activity configuration for each entity below.

Simulation requirement: {simulation_requirement}

## Entity list
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## Task
Generate an activity configuration for every entity. Notes:
- **Timing must fit the target audience's routine**. The values below are UTC+8 references; adapt them to the scenario.
- **Official organizations** (University/GovernmentAgency): low activity (0.1-0.3), active during work hours (9-17), slow response (60-240 minutes), high influence (2.5-3.0)
- **Media** (MediaOutlet): medium activity (0.4-0.6), active all day (8-23), fast response (5-30 minutes), high influence (2.0-2.5)
- **Individuals** (Student/Person/Alumni): high activity (0.6-0.9), mainly active in the evening (18-23), fast response (1-15 minutes), low influence (0.8-1.2)
- **Public figures and experts**: medium activity (0.4-0.6), medium-high influence (1.5-2.0)

Return JSON only (no markdown):
{{
    "agent_configs": [
        {{
            "agent_id": <must match the input>,
            "activity_level": <0.0-1.0>,
            "posts_per_hour": <posting frequency>,
            "comments_per_hour": <commenting frequency>,
            "active_hours": [<list of active hours that fit the audience routine>],
            "response_delay_min": <minimum response delay in minutes>,
            "response_delay_max": <maximum response delay in minutes>,
            "sentiment_bias": <-1.0 to 1.0>,
            "stance": "<supportive/opposing/neutral/observer>",
            "influence_weight": <influence weight>
        }},
        ...
    ]
}}"""

        system_prompt = "You are a social media behaviour analyst. Return pure JSON. The configuration must fit the target audience's daily routine."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}\nIMPORTANT: The 'stance' field value MUST be one of the English strings: 'supportive', 'opposing', 'neutral', 'observer'. All JSON field names and numeric values must remain unchanged. Only natural language text fields should use the specified language."

        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            logger.warning(f"Agent configuration batch times LLM generate failed: {e}, use rule generate")
            llm_configs = {}
        
        # build AgentActivityConfig object
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            # if LLM no generate，use rule generate
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=get_entity_uuid(entity),
                entity_name=get_entity_name(entity),
                entity_type=entity_type_of(entity) or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: Dict[str, Any]) -> Dict[str, Any]:
        """Rule-based generate order Agent configuration（Chinese work and rest）"""
        entity_type = (entity_type_of(entity) or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # official organisation：Working Time Activity，low frequency，high influence
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # media：All-day Events，Medium etc. frequency，high influence
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # expert/professor：Jobs+Evening activities，Medium etc. frequency
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # student：Evening for Lord，high frequency
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Morning+Evening
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # Alumnus：Evening for Lord
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # Siesta+Evening
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # regular person：Evening High Peak
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Daytime+Evening
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    

