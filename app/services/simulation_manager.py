"""
OASIS simulation manager
Managing Twitter and Reddit Dual platform and line simulation
use preset script + LLM Smart generate configuration parameter
"""

import os
import json
import shutil
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.logger import get_logger
from .local_memory import LocalMemory
from .oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile
from .entity_utils import entity_type_of as get_entity_type
from .simulation_config_generator import SimulationConfigGenerator, SimulationParameters
from ..utils.locale import t

logger = get_logger('Veyronix.simulation')


class SimulationStatus(str, Enum):
    """simulation status"""
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    STOPPING = "stopping"
    PAUSED = "paused"
    STOPPED = "stopped"      # simulation is manually stopped
    COMPLETED = "completed"  # simulation natural completed
    FAILED = "failed"


class PlatformType(str, Enum):
    """platform type"""
    TWITTER = "twitter"
    REDDIT = "reddit"


@dataclass
class SimulationState:
    """simulation status"""
    simulation_id: str
    project_id: str
    graph_id: str
    
    # platform enable status
    enable_twitter: bool = True
    enable_reddit: bool = True
    
    # status
    status: SimulationStatus = SimulationStatus.CREATED
    
    # Prepare stage data
    entities_count: int = 0
    profiles_count: int = 0
    entity_types: List[str] = field(default_factory=list)
    
    # configuration generate information
    profiles_generated: bool = False
    config_generated: bool = False
    config_reasoning: str = ""
    
    # Data at the time of line shipment
    current_round: int = 0
    twitter_status: str = "not_started"
    reddit_status: str = "not_started"
    
    # time stamp
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    
    # error information
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """complete status characters dictionary（internal use）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "enable_twitter": self.enable_twitter,
            "enable_reddit": self.enable_reddit,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "profiles_generated": self.profiles_generated,
            "config_generated": self.config_generated,
            "config_reasoning": self.config_reasoning,
            "current_round": self.current_round,
            "twitter_status": self.twitter_status,
            "reddit_status": self.reddit_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }
    
    def get_default_platform(self) -> str:
        """Return default platform based on enabled status"""
        if self.enable_twitter and self.enable_reddit:
            return "reddit"  # stable original default when both are enabled
        elif self.enable_twitter:
            return "twitter"
        else:
            return "reddit"

    def to_simple_dict(self) -> Dict[str, Any]:
        """Simplified status characters dictionary（API return use）"""
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "status": self.status.value,
            "entities_count": self.entities_count,
            "profiles_count": self.profiles_count,
            "entity_types": self.entity_types,
            "profiles_generated": self.profiles_generated,
            "config_generated": self.config_generated,
            "error": self.error,
        }


class SimulationManager:
    """
    simulation manager
    
    Core function：
    1. from Zep graph read entity and filter
    2. generate OASIS Agent Profile
    3. use LLM Intelligent generate simulation configuration parameter
    4. Required to prepare the preset script of all file
    """
    
    # simulation data storage directory
    SIMULATION_DATA_DIR = os.path.join(
        os.path.dirname(__file__), 
        '../../uploads/simulations'
    )
    
    def __init__(self):
        # ensure directory is stored in
        os.makedirs(self.SIMULATION_DATA_DIR, exist_ok=True)
        
        # memoryof simulation status cache
        self._simulations: Dict[str, SimulationState] = {}
    
    def _get_simulation_dir(self, simulation_id: str) -> str:
        """get simulation data directory"""
        sim_dir = os.path.join(self.SIMULATION_DATA_DIR, simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        return sim_dir
    
    def _save_simulation_state(self, state: SimulationState):
        """save simulation status to file"""
        sim_dir = self._get_simulation_dir(state.simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        state.updated_at = datetime.now().isoformat()
        
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        
        self._simulations[state.simulation_id] = state
    
    def _load_simulation_state(self, simulation_id: str) -> Optional[SimulationState]:
        """from file load simulation status"""
        if simulation_id in self._simulations:
            return self._simulations[simulation_id]
        
        sim_dir = self._get_simulation_dir(simulation_id)
        state_file = os.path.join(sim_dir, "state.json")
        
        if not os.path.exists(state_file):
            return None
        
        with open(state_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=data.get("project_id", ""),
            graph_id=data.get("graph_id", ""),
            enable_twitter=data.get("enable_twitter", True),
            enable_reddit=data.get("enable_reddit", True),
            status=SimulationStatus(data.get("status", "created")),
            entities_count=data.get("entities_count", 0),
            profiles_count=data.get("profiles_count", 0),
            entity_types=data.get("entity_types", []),
            profiles_generated=data.get("profiles_generated", False),
            config_generated=data.get("config_generated", False),
            config_reasoning=data.get("config_reasoning", ""),
            current_round=data.get("current_round", 0),
            twitter_status=data.get("twitter_status", "not_started"),
            reddit_status=data.get("reddit_status", "not_started"),
            created_at=data.get("created_at", datetime.now().isoformat()),
            updated_at=data.get("updated_at", datetime.now().isoformat()),
            error=data.get("error"),
        )
        
        self._simulations[simulation_id] = state
        return state
    
    def create_simulation(
        self,
        project_id: str,
        graph_id: str,
        enable_twitter: bool = True,
        enable_reddit: bool = True,
    ) -> SimulationState:
        """
        create new of simulation
        
        Args:
            project_id: project ID
            graph_id: Zep graph ID
            enable_twitter: enabled Twitter simulation
            enable_reddit: enabled Reddit simulation
            
        Returns:
            SimulationState
        """
        import uuid
        simulation_id = f"sim_{uuid.uuid4().hex[:12]}"
        
        state = SimulationState(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=enable_twitter,
            enable_reddit=enable_reddit,
            status=SimulationStatus.CREATED,
        )
        
        self._save_simulation_state(state)
        logger.info(f"create simulation: {simulation_id}, project={project_id}, graph={graph_id}")
        
        return state
    
    def prepare_simulation(
        self,
        simulation_id: str,
        simulation_requirement: str,
        document_text: str,
        defined_entity_types: Optional[List[str]] = None,
        use_llm_for_profiles: bool = True,
        progress_callback: Optional[callable] = None,
        parallel_profile_count: int = 3
    ) -> SimulationState:
        """
        Prepare simulation environment（Full Automation）
        
        step：
        1. from Zep graph read and filter entity
        2. for every entity generate OASIS Agent Profile（optional LLM Tingkatkan，support and line）
        3. use LLM Intelligent generate simulation configuration parameter（time、Activity、Speaking Frequency etc.）
        4. save configuration file and Profile file
        5. copy preset script to simulation directory
        
        Args:
            simulation_id: simulation ID
            simulation_requirement: simulation requirement description（used for LLM generate configuration）
            document_text: Original document content（used for LLM Understand the context）
            defined_entity_types: Predefined of entity type（optional）
            use_llm_for_profiles: whether to use LLM generate detailed persona
            progress_callback: progress callback function count (stage, progress, message)
            parallel_profile_count: and line generate persona of count，default 3
            
        Returns:
            SimulationState
        """
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"Simulation not found: {simulation_id}")
        
        try:
            state.status = SimulationStatus.PREPARING
            state.error = None
            state.profiles_generated = False
            state.config_generated = False
            state.config_reasoning = ""
            self._save_simulation_state(state)
            
            sim_dir = self._get_simulation_dir(simulation_id)
            
            # ========== stage 1: read and filter entity ==========
            if progress_callback:
                progress_callback("reading", 0, t('progress.connectingZepGraph'))
            
            memory = LocalMemory(Config.DATABASE_PATH)
            try:
                agent_records = memory.list_agents(state.graph_id)
            except Exception as exc:
                logger.warning("Graph-scoped agent lookup failed, loading all agents: %s", exc)
                agent_records = []
            if not agent_records:
                # Older graphs were stored without a graph_id; fall back to all.
                agent_records = memory.list_agents()
            
            if progress_callback:
                progress_callback("reading", 30, t('progress.readingNodeData'))
            
            entities: List[Dict[str, Any]] = []
            for record in agent_records:
                persona = record.get("persona") or {}
                if not isinstance(persona, dict):
                    persona = {"content": str(persona)}
                entity = dict(persona)
                entity.setdefault("uuid", record.get("id"))
                entity.setdefault("graph_id", state.graph_id)
                entities.append(entity)
            
            if defined_entity_types:
                allowed = {str(item).strip().lower() for item in defined_entity_types}
                filtered_entities = [
                    entity for entity in entities
                    if get_entity_type(entity).lower() in allowed
                ]
                # Never end up with an empty simulation just because the
                # ontology type names differ from the stored ones.
                if filtered_entities:
                    entities = filtered_entities

            # Keep the most connected entities, then cap the cast.
            entities.sort(
                key=lambda entity: len(
                    entity.get("related_edges") or []
                ),
                reverse=True,
            )
            entities = entities[:Config.SIMULATION_MAX_ENTITIES]

            entity_types = sorted({get_entity_type(e) for e in entities})
            
            # The guard stays before the cast expansion: an empty graph must
            # fail fast instead of silently inventing a cast.
            if not entities:
                state.status = SimulationStatus.FAILED
                state.error = "No matching entities found; check that the graph was built correctly"
                self._save_simulation_state(state)
                raise ValueError(state.error)
            
            # A graph normally yields only a handful of nodes. Expand them into
            # a diverse cast of personas so the simulation behaves like a real
            # community instead of a few isolated speakers.
            profile_generator = OasisProfileGenerator(graph_id=state.graph_id)
            entities = profile_generator.expand_entity_cast(
                entities,
                target_count=Config.SIMULATION_MIN_ENTITIES,
            )
            
            state.entities_count = len(entities)
            state.entity_types = entity_types
            
            if progress_callback:
                progress_callback(
                    "reading", 100,
                    t('progress.readingComplete', count=len(entities)),
                    current=len(entities),
                    total=len(entities)
                )
            
            # ========== stage 2: generate Agent Profile ==========
            total_entities = len(entities)
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 0,
                    t('progress.startGenerating'),
                    current=0,
                    total=total_entities
                )
            
            # The cast was already expanded above, so this generator only has
            # to turn the personas into profiles.
            generator = profile_generator
            
            def profile_progress(current, total, msg):
                if progress_callback:
                    progress_callback(
                        "generating_profiles", 
                        int(current / total * 100), 
                        msg,
                        current=current,
                        total=total,
                        item_name=msg
                    )
            
            # settings real-time save of file path（Priority use Reddit JSON format）
            realtime_output_path = None
            realtime_platform = "reddit"
            if state.enable_reddit:
                realtime_output_path = os.path.join(sim_dir, "reddit_profiles.json")
                realtime_platform = "reddit"
            elif state.enable_twitter:
                realtime_output_path = os.path.join(sim_dir, "twitter_profiles.csv")
                realtime_platform = "twitter"
            
            profiles = generator.generate_profiles_from_entities(
                entities=entities,
                use_llm=use_llm_for_profiles,
                progress_callback=profile_progress,
                graph_id=state.graph_id,  # Incoming graph_id used for Zep retrieve
                parallel_count=parallel_profile_count,  # and line generate count
                realtime_output_path=realtime_output_path,  # Real-time save path
                output_platform=realtime_platform  # output format
            )
            
            state.profiles_count = len(profiles)
            state.profiles_generated = len(profiles) > 0
            self._save_simulation_state(state)
            
            # save Profile file（note：Twitter use CSV format，Reddit use JSON format）
            # Reddit real-time save during already in generate process，Save one more time here to ensure completeness
            if progress_callback:
                progress_callback(
                    "generating_profiles", 95,
                    t('progress.savingProfiles'),
                    current=total_entities,
                    total=total_entities
                )
            
            if state.enable_reddit:
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "reddit_profiles.json"),
                    platform="reddit"
                )
            
            if state.enable_twitter:
                # Twitter use CSV format！This is OASIS of Requirements
                generator.save_profiles(
                    profiles=profiles,
                    file_path=os.path.join(sim_dir, "twitter_profiles.csv"),
                    platform="twitter"
                )
            
            if progress_callback:
                progress_callback(
                    "generating_profiles", 100,
                    t('progress.profilesComplete', count=len(profiles)),
                    current=len(profiles),
                    total=len(profiles)
                )
            
            # ========== stage 3: LLM Intelligent generate simulation configuration ==========
            if progress_callback:
                progress_callback(
                    "generating_config", 0,
                    t('progress.analyzingRequirements'),
                    current=0,
                    total=3
                )
            
            config_generator = SimulationConfigGenerator()
            
            if progress_callback:
                progress_callback(
                    "generating_config", 30,
                    t('progress.callingLLMConfig'),
                    current=1,
                    total=3
                )
            
            sim_params = config_generator.generate_config(
                simulation_id=simulation_id,
                project_id=state.project_id,
                graph_id=state.graph_id,
                simulation_requirement=simulation_requirement,
                document_text=document_text,
                entities=entities,
                enable_twitter=state.enable_twitter,
                enable_reddit=state.enable_reddit
            )
            
            if progress_callback:
                progress_callback(
                    "generating_config", 70,
                    t('progress.savingConfigFiles'),
                    current=2,
                    total=3
                )
            
            # save configuration file
            config_path = os.path.join(sim_dir, "simulation_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(sim_params.to_json())
            
            state.config_generated = True
            state.config_reasoning = sim_params.generation_reasoning
            
            if progress_callback:
                progress_callback(
                    "generating_config", 100,
                    t('progress.configComplete'),
                    current=3,
                    total=3
                )
            
            # note：Run line script reserved in backend/scripts/ directory，No more copy to simulation directory
            # When starting simulation，simulation_runner Meetings from scripts/ directory operation line script
            
            # update status
            state.status = SimulationStatus.READY
            self._save_simulation_state(state)
            
            logger.info(f"simulation preparation completed: {simulation_id}, "
                       f"entities={state.entities_count}, profiles={state.profiles_count}")
            
            return state
            
        except Exception as e:
            logger.error(f"simulation preparation failed: {simulation_id}, error={str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            state.status = SimulationStatus.FAILED
            state.error = str(e)
            self._save_simulation_state(state)
            raise
    
    def get_simulation(self, simulation_id: str) -> Optional[SimulationState]:
        """get simulation status"""
        return self._load_simulation_state(simulation_id)
    
    def list_simulations(self, project_id: Optional[str] = None) -> List[SimulationState]:
        """column out all simulation"""
        simulations = []
        
        if os.path.exists(self.SIMULATION_DATA_DIR):
            for sim_id in os.listdir(self.SIMULATION_DATA_DIR):
                # Skip hide file（like .DS_Store）and non-directory file
                sim_path = os.path.join(self.SIMULATION_DATA_DIR, sim_id)
                if sim_id.startswith('.') or not os.path.isdir(sim_path):
                    continue
                
                state = self._load_simulation_state(sim_id)
                if state:
                    if project_id is None or state.project_id == project_id:
                        simulations.append(state)
        
        return simulations
    
    def get_profiles(self, simulation_id: str, platform: str = None) -> List[Dict[str, Any]]:
        """get simulation of Agent Profile"""
        state = self._load_simulation_state(simulation_id)
        if not state:
            raise ValueError(f"Simulation not found: {simulation_id}")

        if platform is None:
            platform = state.get_default_platform()

        if platform not in {"twitter", "reddit"}:
            raise ValueError(f"Unsupported platform: {platform}")

        sim_dir = self._get_simulation_dir(simulation_id)
        profile_path = os.path.join(
            sim_dir,
            "twitter_profiles.csv" if platform == "twitter" else "reddit_profiles.json",
        )
        
        if not os.path.exists(profile_path):
            return []

        if platform == "twitter":
            import csv

            with open(profile_path, 'r', encoding='utf-8', newline='') as f:
                return list(csv.DictReader(f))

        with open(profile_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_simulation_config(self, simulation_id: str) -> Optional[Dict[str, Any]]:
        """get simulation configuration"""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return None
        
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def get_run_instructions(self, simulation_id: str) -> Dict[str, Any]:
        """Return in-process runner information (no external scripts)."""
        sim_dir = self._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")

        return {
            "simulation_dir": sim_dir,
            "config_file": config_path,
            "runner": "in-process asyncio",
            "commands": {
                "start": (
                    "POST /api/simulation/start with "
                    f'{{"simulation_id": "{simulation_id}", "platform": "parallel"}}'
                ),
            },
            "instructions": (
                "The simulation runs inside the Flask backend using the asyncio "
                "runner. Start it from the UI or POST to /api/simulation/start. "
                "No external process is required."
            ),
        }
