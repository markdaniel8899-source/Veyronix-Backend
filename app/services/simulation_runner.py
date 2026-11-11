"""
Async simulation runner for Veyronix backend.

Runs OASIS-style social simulations fully in-process using a background
thread with its own asyncio event loop. No subprocesses are spawned. LLM
calls are throttled by a token-bucket rate limiter (LLM_RPM) and a
semaphore (LLM_MAX_CONCURRENT), and processing happens in small chunks so
a 1 GB Oracle Cloud VM stays within memory limits.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import math
import os
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .local_memory import LocalMemory
from .rate_limiter import TokenBucketRateLimiter

logger = get_logger('Veyronix.simulation_runner')

_cleanup_registered = False


class RunnerStatus(str, Enum):
    """Transporter status"""
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"


class SimulationStopPending(TimeoutError):
    """Simulation stop is pending."""


@dataclass
class AgentAction:
    """Agent action record"""
    round_num: int
    timestamp: str
    platform: str  # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str  # CREATE_POST, CREATE_COMMENT, etc.
    action_args: Dict[str, Any] = field(default_factory=dict)
    result: Optional[str] = None
    success: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "timestamp": self.timestamp,
            "platform": self.platform,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action_type": self.action_type,
            "action_args": self.action_args,
            "result": self.result,
            "success": self.success,
        }


@dataclass
class RoundSummary:
    """per rounds summary"""
    round_num: int
    start_time: str
    end_time: Optional[str] = None
    simulated_hour: int = 0
    twitter_actions: int = 0
    reddit_actions: int = 0
    active_agents: List[int] = field(default_factory=list)
    actions: List[AgentAction] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_num": self.round_num,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "simulated_hour": self.simulated_hour,
            "twitter_actions": self.twitter_actions,
            "reddit_actions": self.reddit_actions,
            "active_agents": self.active_agents,
            "actions_count": len(self.actions),
        }


@dataclass
class SimulationRunState:
    """simulation haul line status（real time）"""
    simulation_id: str
    runner_status: RunnerStatus = RunnerStatus.IDLE

    # progress information
    current_round: int = 0
    total_rounds: int = 0
    simulated_hours: int = 0
    total_simulation_hours: int = 0

    # Independent round and simulation time for each platform（For dual platform and line show）
    twitter_current_round: int = 0
    reddit_current_round: int = 0
    twitter_simulated_hours: int = 0
    reddit_simulated_hours: int = 0

    # platform status
    twitter_running: bool = False
    reddit_running: bool = False
    twitter_actions_count: int = 0
    reddit_actions_count: int = 0

    # platform completed status
    twitter_completed: bool = False
    reddit_completed: bool = False

    # per rounds summary
    rounds: List[RoundSummary] = field(default_factory=list)

    # most near action（Used for front-end live presentations）
    recent_actions: List[AgentAction] = field(default_factory=list)
    max_recent_actions: int = 50

    # time stamp
    started_at: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None

    # error information
    error: Optional[str] = None

    def add_action(self, action: AgentAction):
        """add action to most near action list"""
        self.recent_actions.insert(0, action)
        if len(self.recent_actions) > self.max_recent_actions:
            self.recent_actions = self.recent_actions[:self.max_recent_actions]

        if action.platform == "twitter":
            self.twitter_actions_count += 1
        else:
            self.reddit_actions_count += 1

        self.updated_at = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "runner_status": self.runner_status.value,
            "current_round": self.current_round,
            "total_rounds": self.total_rounds,
            "simulated_hours": self.simulated_hours,
            "total_simulation_hours": self.total_simulation_hours,
            "progress_percent": round(
                self.current_round / max(self.total_rounds, 1) * 100, 1
            ),
            "twitter_current_round": self.twitter_current_round,
            "reddit_current_round": self.reddit_current_round,
            "twitter_simulated_hours": self.twitter_simulated_hours,
            "reddit_simulated_hours": self.reddit_simulated_hours,
            "twitter_running": self.twitter_running,
            "reddit_running": self.reddit_running,
            "twitter_completed": self.twitter_completed,
            "reddit_completed": self.reddit_completed,
            "twitter_actions_count": self.twitter_actions_count,
            "reddit_actions_count": self.reddit_actions_count,
            "total_actions_count": self.twitter_actions_count + self.reddit_actions_count,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }

    def to_detail_dict(self) -> Dict[str, Any]:
        """include most proximate action of detailed information"""
        result = self.to_dict()
        result["recent_actions"] = [a.to_dict() for a in self.recent_actions]
        result["rounds_count"] = len(self.rounds)
        return result


class SimulationRunner:
    """
    In-process simulation runner for Veyronix.

    Responsibilities:
    1. Run the simulation loop in a daemon thread with its own event loop
    2. Record every agent action to actions.jsonl
    3. Provide real-time status/actions/timeline queries
    4. Support graceful stop, cleanup and post-run interviews
    """

    RUN_STATE_DIR = Config.OASIS_SIMULATION_DATA_DIR

    _run_states: Dict[str, SimulationRunState] = {}
    _threads: Dict[str, threading.Thread] = {}
    _stop_requests: Dict[str, bool] = {}
    _manual_stop_requests: set[str] = set()
    _state_guard = threading.Lock()
    _active_loops: Dict[str, asyncio.AbstractEventLoop] = {}

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    @classmethod
    def _simulation_dir(cls, simulation_id: str) -> str:
        return os.path.join(cls.RUN_STATE_DIR, simulation_id)

    @classmethod
    def _run_state_file(cls, simulation_id: str) -> str:
        return os.path.join(cls._simulation_dir(simulation_id), "run_state.json")

    @classmethod
    def _actions_file(cls, simulation_id: str) -> str:
        return os.path.join(cls._simulation_dir(simulation_id), "actions.jsonl")

    @classmethod
    def _interviews_file(cls, simulation_id: str) -> str:
        return os.path.join(cls._simulation_dir(simulation_id), "interviews.json")

    # ------------------------------------------------------------------
    # Lifecycle / cleanup
    # ------------------------------------------------------------------

    @classmethod
    def register_cleanup(cls) -> None:
        """Register an atexit hook that stops interpreter-time workers."""
        global _cleanup_registered
        if _cleanup_registered:
            return
        atexit.register(cls._cleanup_all)
        _cleanup_registered = True

    @classmethod
    def _cleanup_all(cls) -> None:
        for simulation_id in list(cls._stop_requests):
            cls._stop_requests[simulation_id] = True
        for simulation_id, loop in list(cls._active_loops.items()):
            try:
                if loop.is_running():
                    loop.call_soon_threadsafe(lambda: None)
            except Exception:
                pass

    @classmethod
    def _finalization_lock(cls, simulation_id: str) -> threading.Lock:
        # Kept for compatibility with older callers; thread synchronization
        # is handled by _state_guard in this implementation.
        return cls._state_guard

    @classmethod
    def _sync_simulation_status(
        cls,
        simulation_id: str,
        runner_status: RunnerStatus,
        error: Optional[str] = None,
    ) -> None:
        """Keep persisted simulation metadata aligned with run_state.json."""
        from .simulation_manager import SimulationManager, SimulationStatus

        status_map = {
            RunnerStatus.IDLE: SimulationStatus.CREATED,
            RunnerStatus.STARTING: SimulationStatus.PREPARING,
            RunnerStatus.RUNNING: SimulationStatus.RUNNING,
            RunnerStatus.PAUSED: SimulationStatus.PAUSED,
            RunnerStatus.STOPPING: SimulationStatus.STOPPING,
            RunnerStatus.STOPPED: SimulationStatus.STOPPED,
            RunnerStatus.COMPLETED: SimulationStatus.COMPLETED,
            RunnerStatus.FAILED: SimulationStatus.FAILED,
        }
        status = status_map.get(runner_status)
        if status is None:
            return
        try:
            manager = SimulationManager()
            simulation = manager.get_simulation(simulation_id)
            if simulation is None:
                return
            simulation.status = status
            simulation.error = error
            manager._save_simulation_state(simulation)
        except Exception as sync_error:
            logger.error(
                "Failed to sync simulation status: simulation_id=%s, status=%s, error=%s",
                simulation_id,
                runner_status.value,
                sync_error,
            )

    # ------------------------------------------------------------------
    # Run state persistence
    # ------------------------------------------------------------------

    @classmethod
    def get_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        with cls._state_guard:
            if simulation_id in cls._run_states:
                return cls._run_states[simulation_id]

        state = cls._load_run_state(simulation_id)
        if state:
            with cls._state_guard:
                cls._run_states.setdefault(simulation_id, state)
                return cls._run_states[simulation_id]
        return state

    @classmethod
    def _load_run_state(cls, simulation_id: str) -> Optional[SimulationRunState]:
        state_file = cls._run_state_file(simulation_id)
        if not os.path.exists(state_file):
            return None

        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            state = SimulationRunState(
                simulation_id=simulation_id,
                runner_status=RunnerStatus(data.get("runner_status", "idle")),
                current_round=data.get("current_round", 0),
                total_rounds=data.get("total_rounds", 0),
                simulated_hours=data.get("simulated_hours", 0),
                total_simulation_hours=data.get("total_simulation_hours", 0),
                twitter_current_round=data.get("twitter_current_round", 0),
                reddit_current_round=data.get("reddit_current_round", 0),
                twitter_simulated_hours=data.get("twitter_simulated_hours", 0),
                reddit_simulated_hours=data.get("reddit_simulated_hours", 0),
                twitter_running=data.get("twitter_running", False),
                reddit_running=data.get("reddit_running", False),
                twitter_completed=data.get("twitter_completed", False),
                reddit_completed=data.get("reddit_completed", False),
                twitter_actions_count=data.get("twitter_actions_count", 0),
                reddit_actions_count=data.get("reddit_actions_count", 0),
                started_at=data.get("started_at"),
                updated_at=data.get("updated_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                error=data.get("error"),
            )

            for a in data.get("recent_actions", []):
                state.recent_actions.append(AgentAction(
                    round_num=a.get("round_num", 0),
                    timestamp=a.get("timestamp", ""),
                    platform=a.get("platform", ""),
                    agent_id=a.get("agent_id", 0),
                    agent_name=a.get("agent_name", ""),
                    action_type=a.get("action_type", ""),
                    action_args=a.get("action_args", {}),
                    result=a.get("result"),
                    success=a.get("success", True),
                ))
            return state
        except Exception as e:
            logger.error("load shipment line status failed: %s", e)
            return None

    @classmethod
    def _save_run_state(cls, state: SimulationRunState) -> None:
        sim_dir = cls._simulation_dir(state.simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        state_file = cls._run_state_file(state.simulation_id)
        data = state.to_dict()
        data["recent_actions"] = [a.to_dict() for a in state.recent_actions]
        data["rounds"] = [r.to_dict() for r in state.rounds]
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("save shipment line status failed: %s", e)

    @classmethod
    def _append_actions(cls, simulation_id: str, actions: List[AgentAction]) -> None:
        if not actions:
            return
        sim_dir = cls._simulation_dir(simulation_id)
        os.makedirs(sim_dir, exist_ok=True)
        try:
            with open(cls._actions_file(simulation_id), 'a', encoding='utf-8') as f:
                for action in actions:
                    f.write(json.dumps(action.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("save action log failed: %s", e)

    @classmethod
    def _load_actions_from_disk(cls, simulation_id: str) -> List[AgentAction]:
        path = cls._actions_file(simulation_id)
        if not os.path.exists(path):
            return []
        actions: List[AgentAction] = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    actions.append(AgentAction(
                        round_num=data.get("round_num", 0),
                        timestamp=data.get("timestamp", ""),
                        platform=data.get("platform", ""),
                        agent_id=data.get("agent_id", 0),
                        agent_name=data.get("agent_name", ""),
                        action_type=data.get("action_type", ""),
                        action_args=data.get("action_args", {}),
                        result=data.get("result"),
                        success=data.get("success", True),
                    ))
        except Exception as e:
            logger.error("read action log failed: %s", e)
        return actions

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @classmethod
    def stop_simulation(cls, simulation_id: str) -> SimulationRunState:
        """Request a stop and wait briefly for the worker thread to finish."""
        cls._stop_requests[simulation_id] = True
        cls._manual_stop_requests.add(simulation_id)

        state = cls.get_run_state(simulation_id)
        if state is None:
            state = SimulationRunState(simulation_id=simulation_id)
            with cls._state_guard:
                cls._run_states[simulation_id] = state

        state.runner_status = RunnerStatus.STOPPING
        cls._save_run_state(state)

        thread = cls._threads.get(simulation_id)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=20)

        if thread and thread.is_alive():
            raise SimulationStopPending(
                f"Simulation {simulation_id} is still finalizing its current LLM call"
            )

        state.runner_status = RunnerStatus.STOPPED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = state.completed_at or datetime.now().isoformat()
        cls._save_run_state(state)
        cls._sync_simulation_status(simulation_id, RunnerStatus.STOPPED)
        return state

    @classmethod
    def is_stop_requested(cls, simulation_id: str) -> bool:
        return cls._stop_requests.get(simulation_id, False)

    @classmethod
    def clear_stop_request(cls, simulation_id: str):
        cls._stop_requests.pop(simulation_id, None)
        cls._manual_stop_requests.discard(simulation_id)

    @classmethod
    def is_running(cls, simulation_id: str) -> bool:
        thread = cls._threads.get(simulation_id)
        return thread is not None and thread.is_alive()

    @classmethod
    def check_env_alive(cls, simulation_id: str) -> bool:
        """True while running or after a completed run (interviews allowed)."""
        if cls.is_running(simulation_id):
            return True
        state = cls.get_run_state(simulation_id)
        return bool(
            state
            and state.runner_status
            in {RunnerStatus.RUNNING, RunnerStatus.PAUSED, RunnerStatus.COMPLETED}
        )

    @classmethod
    def get_env_status_detail(cls, simulation_id: str) -> Dict[str, Any]:
        state = cls.get_run_state(simulation_id)
        if not state:
            return {
                "simulation_id": simulation_id,
                "env_alive": False,
                "twitter_available": False,
                "reddit_available": False,
                "runner_status": RunnerStatus.IDLE.value,
            }
        return {
            "simulation_id": simulation_id,
            "env_alive": cls.check_env_alive(simulation_id),
            "twitter_available": bool(state.twitter_running or state.twitter_completed),
            "reddit_available": bool(state.reddit_running or state.reddit_completed),
            "runner_status": state.runner_status.value,
            "current_round": state.current_round,
            "total_rounds": state.total_rounds,
        }

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    @classmethod
    def start_simulation(
        cls,
        simulation_id: str,
        platform: str = "parallel",
        max_rounds: Optional[int] = None,
        enable_graph_memory_update: bool = False,
        graph_id: Optional[str] = None,
    ) -> SimulationRunState:
        """Start a simulation in a daemon background thread."""
        from .simulation_manager import SimulationManager, SimulationStatus

        if cls.is_running(simulation_id):
            raise ValueError(f"Simulation {simulation_id} is already running")

        manager = SimulationManager()
        simulation = manager.get_simulation(simulation_id)
        if simulation is None:
            raise ValueError(f"Simulation not found: {simulation_id}")
        if simulation.status not in {
            SimulationStatus.READY,
            SimulationStatus.STOPPED,
            SimulationStatus.COMPLETED,
            SimulationStatus.FAILED,
        }:
            raise ValueError(
                f"Simulation is not ready to start (status={simulation.status.value})"
            )

        sim_dir = cls._simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        if not os.path.exists(config_path):
            raise ValueError("simulation_config.json not found; prepare the simulation first")

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        agents = cls._load_agents(simulation_id, config)
        if not agents:
            raise ValueError("No agent profiles found; prepare the simulation first")

        agents = cls._merge_agent_config(agents, config.get("agent_configs") or [])

        platforms = (
            ["twitter", "reddit"]
            if platform == "parallel"
            else [platform]
        )
        unknown = [p for p in platforms if p not in {"twitter", "reddit"}]
        if unknown:
            raise ValueError(f"Unsupported platform: {unknown[0]}")

        time_config = config.get("time_config") or {}
        minutes_per_round = int(time_config.get("minutes_per_round", 60) or 60)
        total_hours = int(time_config.get("total_simulation_hours", 72) or 72)
        max_possible = max(1, math.ceil(total_hours * 60 / max(minutes_per_round, 1)))
        effective_rounds = int(max_rounds or Config.OASIS_DEFAULT_MAX_ROUNDS or 10)
        effective_rounds = max(1, min(effective_rounds, max_possible))

        state = SimulationRunState(
            simulation_id=simulation_id,
            runner_status=RunnerStatus.STARTING,
            total_rounds=effective_rounds,
            total_simulation_hours=total_hours,
            twitter_running="twitter" in platforms,
            reddit_running="reddit" in platforms,
            started_at=datetime.now().isoformat(),
        )
        with cls._state_guard:
            cls._run_states[simulation_id] = state
        cls._stop_requests[simulation_id] = False
        cls._manual_stop_requests.discard(simulation_id)
        cls._save_run_state(state)

        thread = threading.Thread(
            target=cls._run_thread,
            args=(
                simulation_id,
                agents,
                effective_rounds,
                platforms,
                config,
                minutes_per_round,
                graph_id,
                enable_graph_memory_update,
            ),
            daemon=True,
            name=f"veyronix-sim-{simulation_id}",
        )
        with cls._state_guard:
            cls._threads[simulation_id] = thread
        thread.start()
        return state

    @classmethod
    def _run_thread(
        cls,
        simulation_id: str,
        agents: List[Dict[str, Any]],
        total_rounds: int,
        platforms: List[str],
        config: Dict[str, Any],
        minutes_per_round: int,
        graph_id: Optional[str],
        enable_graph_memory_update: bool,
    ) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with cls._state_guard:
            cls._active_loops[simulation_id] = loop
        try:
            loop.run_until_complete(
                cls._run_loop(
                    simulation_id=simulation_id,
                    agents=agents,
                    total_rounds=total_rounds,
                    platforms=platforms,
                    config=config,
                    minutes_per_round=minutes_per_round,
                    graph_id=graph_id,
                    enable_graph_memory_update=enable_graph_memory_update,
                )
            )
        except Exception as exc:
            logger.error("Simulation run failed: %s", exc, exc_info=True)
            state = cls.get_run_state(simulation_id)
            if state:
                state.runner_status = RunnerStatus.FAILED
                state.error = str(exc)
                state.completed_at = datetime.now().isoformat()
                cls._save_run_state(state)
            cls._sync_simulation_status(simulation_id, RunnerStatus.FAILED, str(exc))
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            loop.close()
            with cls._state_guard:
                cls._active_loops.pop(simulation_id, None)
                cls._threads.pop(simulation_id, None)
            cls._stop_requests.pop(simulation_id, None)

    @classmethod
    async def _run_loop(
        cls,
        simulation_id: str,
        agents: List[Dict[str, Any]],
        total_rounds: int,
        platforms: List[str],
        config: Dict[str, Any],
        minutes_per_round: int,
        graph_id: Optional[str],
        enable_graph_memory_update: bool,
    ) -> None:
        state = cls.get_run_state(simulation_id)
        if state is None:
            raise RuntimeError("Run state disappeared before simulation start")

        limiter = TokenBucketRateLimiter(
            rpm=Config.LLM_RPM,
            max_concurrent=Config.LLM_MAX_CONCURRENT,
        )
        semaphore = asyncio.Semaphore(Config.LLM_MAX_CONCURRENT)
        llm = LLMClient()
        memory = LocalMemory(Config.DATABASE_PATH)

        state.runner_status = RunnerStatus.RUNNING
        cls._save_run_state(state)
        cls._sync_simulation_status(simulation_id, RunnerStatus.RUNNING)

        time_config = config.get("time_config") or {}
        peak_hours = set(time_config.get("peak_hours") or [19, 20, 21, 22])
        off_peak_hours = set(time_config.get("off_peak_hours") or [0, 1, 2, 3, 4, 5])
        agents_per_hour_max = int(time_config.get("agents_per_hour_max", 20) or 20)
        # Keep each round bounded so a 10 RPM free tier can finish in
        # reasonable time while still exercising every agent over time.
        max_active = min(
            len(agents),
            max(Config.LLM_MAX_CONCURRENT * 3, Config.LLM_MAX_CONCURRENT),
            max(agents_per_hour_max, 1),
        )

        start_hour = 8
        for round_num in range(1, total_rounds + 1):
            if cls.is_stop_requested(simulation_id):
                state.runner_status = RunnerStatus.STOPPED
                state.completed_at = datetime.now().isoformat()
                cls._save_run_state(state)
                cls._sync_simulation_status(simulation_id, RunnerStatus.STOPPED)
                return

            round_start = datetime.now().isoformat()
            simulated_hour = (start_hour + (round_num - 1) * minutes_per_round // 60) % 24
            round_summary = RoundSummary(
                round_num=round_num,
                start_time=round_start,
                simulated_hour=simulated_hour,
            )

            # Market shock injection for this round
            shocks = await asyncio.to_thread(
                memory.get_pending_market_shocks, simulation_id, round_num
            )
            shock_text = ""
            if shocks:
                shock_lines = [str(s.get("event", "")) for s in shocks if s.get("event")]
                if shock_lines:
                    shock_text = (
                        "BREAKING MARKET SHOCK (react to this immediately):\n"
                        + "\n".join(f"- {line}" for line in shock_lines)
                    )
                for shock in shocks:
                    await asyncio.to_thread(
                        memory.consume_market_shock, int(shock["id"])
                    )

            active_agents = cls._select_active_agents(
                agents=agents,
                platform_list=platforms,
                simulated_hour=simulated_hour,
                peak_hours=peak_hours,
                off_peak_hours=off_peak_hours,
                max_active=max_active,
            )

            all_actions: List[AgentAction] = []
            chunk_size = max(1, Config.LLM_MAX_CONCURRENT)

            for chunk_start in range(0, len(active_agents), chunk_size):
                if cls.is_stop_requested(simulation_id):
                    break
                chunk = active_agents[chunk_start:chunk_start + chunk_size]

                async def run_one(agent: Dict[str, Any]) -> Optional[AgentAction]:
                    platform_name = agent["platform"]
                    await limiter.acquire()
                    async with semaphore:
                        if cls.is_stop_requested(simulation_id):
                            return None
                        action_type = (
                            "CREATE_POST" if platform_name == "twitter"
                            else random.choice(["CREATE_POST", "CREATE_COMMENT"])
                        )
                        prompt = cls._build_action_prompt(
                            agent=agent,
                            config=config,
                            platform=platform_name,
                            round_num=round_num,
                            simulated_hour=simulated_hour,
                            shock_text=shock_text,
                        )
                        try:
                            text = await asyncio.to_thread(
                                llm.chat,
                                messages=[
                                    {
                                        "role": "system",
                                        "content": (
                                            "You are a social media simulation agent. "
                                            "Stay fully in character. Write one short, "
                                            "realistic social media post (max 2 sentences). "
                                            "Reply in English or Roman Urdu only."
                                        ),
                                    },
                                    {"role": "user", "content": prompt},
                                ],
                                temperature=0.85,
                                max_tokens=160,
                            )
                            text = (text or "").strip()
                            if not text:
                                text = "(no response)"
                            return AgentAction(
                                round_num=round_num,
                                timestamp=datetime.now().isoformat(),
                                platform=platform_name,
                                agent_id=int(agent["id"]),
                                agent_name=str(agent["name"]),
                                action_type=action_type,
                                action_args={
                                    "content": text,
                                    "simulated_hour": simulated_hour,
                                },
                                result=text,
                                success=True,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Agent %s action failed on %s: %s",
                                agent.get("name"), platform_name, exc,
                            )
                            return AgentAction(
                                round_num=round_num,
                                timestamp=datetime.now().isoformat(),
                                platform=platform_name,
                                agent_id=int(agent["id"]),
                                agent_name=str(agent["name"]),
                                action_type=action_type,
                                action_args={},
                                result=f"Error: {exc}",
                                success=False,
                            )

                results = await asyncio.gather(*(run_one(a) for a in chunk))
                for action in results:
                    if action is None:
                        continue
                    all_actions.append(action)
                    state.add_action(action)
                    round_summary.actions.append(action)
                    round_summary.active_agents.append(action.agent_id)
                    await asyncio.to_thread(
                        memory.add_interaction,
                        action.round_num,
                        str(action.agent_id),
                        action.action_type,
                        action.result or "",
                    )

                # Chunk pacing keeps the free NVIDIA NIM tier happy.
                if chunk_start + chunk_size < len(active_agents):
                    await asyncio.sleep(6)

            round_summary.end_time = datetime.now().isoformat()
            round_summary.twitter_actions = sum(
                1 for a in round_summary.actions if a.platform == "twitter"
            )
            round_summary.reddit_actions = sum(
                1 for a in round_summary.actions if a.platform == "reddit"
            )
            state.rounds.append(round_summary)
            state.current_round = round_num
            state.simulated_hours = round_num * minutes_per_round // 60
            if "twitter" in platforms:
                state.twitter_current_round = round_num
                state.twitter_simulated_hours = state.simulated_hours
            if "reddit" in platforms:
                state.reddit_current_round = round_num
                state.reddit_simulated_hours = state.simulated_hours

            await asyncio.to_thread(cls._append_actions, simulation_id, all_actions)
            await asyncio.to_thread(cls._save_run_state, state)

            # Memory consolidation every N rounds
            interval = max(1, int(Config.MEMORY_CONSOLIDATION_INTERVAL or 5))
            if round_num % interval == 0:
                try:
                    await memory.consolidate_memory(
                        round_number=round_num,
                        llm_client=llm,
                        interval=interval,
                    )
                except Exception as exc:
                    logger.warning("Memory consolidation failed: %s", exc)

            if enable_graph_memory_update and graph_id:
                try:
                    await asyncio.to_thread(
                        cls._update_graph_memory,
                        graph_id,
                        round_summary,
                    )
                except Exception as exc:
                    logger.warning("Graph memory update failed: %s", exc)

        if cls.is_stop_requested(simulation_id):
            state.runner_status = RunnerStatus.STOPPED
        else:
            state.runner_status = RunnerStatus.COMPLETED
            state.twitter_completed = "twitter" in platforms
            state.reddit_completed = "reddit" in platforms
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = datetime.now().isoformat()
        cls._save_run_state(state)
        cls._sync_simulation_status(simulation_id, state.runner_status)

    # ------------------------------------------------------------------
    # Prompt / agent helpers
    # ------------------------------------------------------------------

    @classmethod
    def _load_agents(
        cls, simulation_id: str, config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        sim_dir = cls._simulation_dir(simulation_id)
        profiles: List[Dict[str, Any]] = []

        json_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    profiles = [p for p in data if isinstance(p, dict)]
            except Exception as exc:
                logger.warning("Failed to read reddit profiles: %s", exc)

        if not profiles:
            csv_path = os.path.join(sim_dir, "twitter_profiles.csv")
            if os.path.exists(csv_path):
                import csv
                try:
                    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
                        profiles = [
                            {
                                "user_id": int(row.get("user_id", idx)),
                                "name": row.get("name") or row.get("username") or f"Agent {idx}",
                                "username": row.get("username") or f"agent_{idx}",
                                "bio": row.get("description") or "",
                                "persona": row.get("user_char") or row.get("description") or "",
                                "age": 30,
                                "gender": "other",
                                "mbti": "ISTJ",
                                "country": "Global",
                            }
                            for idx, row in enumerate(csv.DictReader(f))
                        ]
                except Exception as exc:
                    logger.warning("Failed to read twitter profiles: %s", exc)

        agents = []
        for idx, profile in enumerate(profiles):
            agent_id = profile.get("user_id", profile.get("id", idx))
            try:
                agent_id = int(agent_id)
            except (TypeError, ValueError):
                agent_id = idx
            agents.append(
                {
                    "id": agent_id,
                    "name": (
                        profile.get("name")
                        or profile.get("username")
                        or f"Agent {agent_id}"
                    ),
                    "username": profile.get("username") or f"agent_{agent_id}",
                    "bio": profile.get("bio") or profile.get("description") or "",
                    "persona": profile.get("persona") or profile.get("bio") or "",
                    "platforms": ["twitter", "reddit"],
                }
            )
        return agents

    @classmethod
    def _merge_agent_config(
        cls,
        agents: List[Dict[str, Any]],
        agent_configs: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        by_id = {}
        for cfg in agent_configs:
            try:
                by_id[int(cfg.get("agent_id"))] = cfg
            except (TypeError, ValueError):
                continue
        merged = []
        for agent in agents:
            cfg = by_id.get(int(agent["id"]), {})
            item = dict(agent)
            item.update(
                {
                    "activity_level": float(cfg.get("activity_level", 0.5) or 0.5),
                    "active_hours": cfg.get("active_hours")
                    or list(range(8, 23)),
                    "influence_weight": float(cfg.get("influence_weight", 1.0) or 1.0),
                    "stance": cfg.get("stance", "neutral"),
                    "entity_type": cfg.get("entity_type", "Entity"),
                }
            )
            merged.append(item)
        return merged

    @classmethod
    def _select_active_agents(
        cls,
        agents: List[Dict[str, Any]],
        platform_list: List[str],
        simulated_hour: int,
        peak_hours: set,
        off_peak_hours: set,
        max_active: int,
    ) -> List[Dict[str, Any]]:
        if simulated_hour in off_peak_hours:
            time_multiplier = 0.05
        elif simulated_hour in peak_hours:
            time_multiplier = 1.5
        elif 6 <= simulated_hour <= 8:
            time_multiplier = 0.4
        elif 9 <= simulated_hour <= 18:
            time_multiplier = 0.7
        else:
            time_multiplier = 0.5

        selected: List[Dict[str, Any]] = []
        pool = list(agents)
        # Shuffle so the same high-activity agents do not always dominate.
        random.shuffle(pool)
        for agent in pool:
            if len(selected) >= max_active:
                break
            active_hours = agent.get("active_hours") or []
            if active_hours and simulated_hour not in active_hours:
                continue
            probability = min(
                1.0,
                max(0.0, float(agent.get("activity_level", 0.5))) * time_multiplier,
            )
            if random.random() > probability:
                continue
            for platform in platform_list:
                selected.append({"platform": platform, **agent})
                if len(selected) >= max_active:
                    break

        if not selected and agents:
            agent = random.choice(agents)
            for platform in platform_list[:1]:
                selected.append({"platform": platform, **agent})
        return selected

    @classmethod
    def _build_action_prompt(
        cls,
        agent: Dict[str, Any],
        config: Dict[str, Any],
        platform: str,
        round_num: int,
        simulated_hour: int,
        shock_text: str,
    ) -> str:
        persona = str(agent.get("persona") or agent.get("bio") or "")[:1500]
        stance = agent.get("stance", "neutral")
        requirement = config.get("simulation_requirement", "")
        hot_topics = (config.get("event_config") or {}).get("hot_topics") or []
        narrative = (config.get("event_config") or {}).get("narrative_direction") or ""

        lines = [
            f"Platform: {platform}",
            f"Round: {round_num} | Simulated hour: {simulated_hour}:00",
            f"Your name: {agent.get('name')}",
            f"Your stance on the topic: {stance}",
            f"Simulation topic: {requirement}",
        ]
        if hot_topics:
            lines.append(f"Hot topics: {', '.join(str(t) for t in hot_topics[:8])}")
        if narrative:
            lines.append(f"Narrative direction: {narrative}")
        if shock_text:
            lines.append(shock_text)
        lines.append(f"Your persona:\n{persona}")
        lines.append(
            "Write your next social media post as this character. "
            "Do not use hashtags more than once. Do not mention being an AI. "
            "Keep it under 60 words."
        )
        return "\n".join(lines)

    @classmethod
    def _update_graph_memory(cls, graph_id: str, round_summary: RoundSummary) -> None:
        """Append the round's activity to the local memory as interactions."""
        memory = LocalMemory(Config.DATABASE_PATH)
        for action in round_summary.actions:
            if action.success and action.result:
                memory.add_interaction(
                    round_num=action.round_num,
                    agent_id=str(action.agent_id),
                    action=action.action_type,
                    content=action.result,
                )

    # ------------------------------------------------------------------
    # Query APIs
    # ------------------------------------------------------------------

    @classmethod
    def get_all_actions(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        round_num: Optional[int] = None,
    ) -> List[AgentAction]:
        actions = cls._load_actions_from_disk(simulation_id)
        if platform:
            actions = [a for a in actions if a.platform == platform]
        if round_num is not None:
            actions = [a for a in actions if a.round_num == round_num]
        return actions

    @classmethod
    def get_actions(
        cls,
        simulation_id: str,
        limit: int = 100,
        offset: int = 0,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        round_num: Optional[int] = None,
    ) -> List[AgentAction]:
        actions = cls._load_actions_from_disk(simulation_id)
        if platform:
            actions = [a for a in actions if a.platform == platform]
        if agent_id is not None:
            actions = [a for a in actions if a.agent_id == agent_id]
        if round_num is not None:
            actions = [a for a in actions if a.round_num == round_num]
        actions.sort(key=lambda a: a.timestamp, reverse=True)
        return actions[offset:offset + limit]

    @classmethod
    def get_timeline(
        cls,
        simulation_id: str,
        start_round: int = 0,
        end_round: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        state = cls.get_run_state(simulation_id)
        summaries: Dict[int, Dict[str, Any]] = {}

        if state:
            for summary in state.rounds:
                summaries[summary.round_num] = summary.to_dict()

        if not summaries:
            actions = cls._load_actions_from_disk(simulation_id)
            for action in actions:
                entry = summaries.setdefault(
                    action.round_num,
                    {
                        "round_num": action.round_num,
                        "start_time": action.timestamp,
                        "end_time": action.timestamp,
                        "simulated_hour": 0,
                        "twitter_actions": 0,
                        "reddit_actions": 0,
                        "active_agents": [],
                        "actions_count": 0,
                    },
                )
                entry["actions_count"] += 1
                if action.platform == "twitter":
                    entry["twitter_actions"] += 1
                else:
                    entry["reddit_actions"] += 1
                if action.agent_id not in entry["active_agents"]:
                    entry["active_agents"].append(action.agent_id)

        rounds = sorted(summaries.values(), key=lambda item: item["round_num"])
        if start_round:
            rounds = [r for r in rounds if r["round_num"] >= start_round]
        if end_round is not None:
            rounds = [r for r in rounds if r["round_num"] <= end_round]
        return rounds

    @classmethod
    def get_agent_stats(cls, simulation_id: str) -> List[Dict[str, Any]]:
        actions = cls._load_actions_from_disk(simulation_id)
        stats: Dict[int, Dict[str, Any]] = {}
        for action in actions:
            item = stats.setdefault(
                action.agent_id,
                {
                    "agent_id": action.agent_id,
                    "agent_name": action.agent_name,
                    "total_actions": 0,
                    "twitter_actions": 0,
                    "reddit_actions": 0,
                    "successful_actions": 0,
                    "failed_actions": 0,
                    "action_types": {},
                },
            )
            item["total_actions"] += 1
            if action.platform == "twitter":
                item["twitter_actions"] += 1
            else:
                item["reddit_actions"] += 1
            if action.success:
                item["successful_actions"] += 1
            else:
                item["failed_actions"] += 1
            item["action_types"][action.action_type] = (
                item["action_types"].get(action.action_type, 0) + 1
            )
        return sorted(
            stats.values(),
            key=lambda item: item["total_actions"],
            reverse=True,
        )

    # ------------------------------------------------------------------
    # Interviews
    # ------------------------------------------------------------------

    @classmethod
    def _load_personas(cls, simulation_id: str) -> Dict[int, Dict[str, Any]]:
        state = cls.get_run_state(simulation_id)
        config: Dict[str, Any] = {}
        config_path = os.path.join(
            cls._simulation_dir(simulation_id), "simulation_config.json"
        )
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except Exception:
                config = {}
        agents = cls._load_agents(simulation_id, config)
        return {int(agent["id"]): agent for agent in agents}

    @classmethod
    def _interview_topics(cls, simulation_id: str) -> Dict[str, Any]:
        config_path = os.path.join(
            cls._simulation_dir(simulation_id), "simulation_config.json"
        )
        if not os.path.exists(config_path):
            return {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    @classmethod
    def _interview_platforms(
        cls, simulation_id: str, platform: Optional[str]
    ) -> List[str]:
        if platform:
            return [platform]
        state = cls.get_run_state(simulation_id)
        if not state:
            return ["twitter"]
        platforms = []
        if state.twitter_running or state.twitter_completed:
            platforms.append("twitter")
        if state.reddit_running or state.reddit_completed:
            platforms.append("reddit")
        return platforms or ["twitter"]

    @classmethod
    def _ask_agent(
        cls,
        agent: Dict[str, Any],
        platform: str,
        prompt: str,
        config: Dict[str, Any],
    ) -> str:
        llm = LLMClient()
        requirement = config.get("simulation_requirement", "")
        persona = str(agent.get("persona") or agent.get("bio") or "")[:2000]
        messages = [
            {
                "role": "system",
                "content": (
                    "You are being interviewed as a social media simulation "
                    "agent. Answer in first person, in character, in English or "
                    "Roman Urdu only. Keep the answer under 120 words."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Simulation topic: {requirement}\n"
                    f"Platform: {platform}\n"
                    f"Your name: {agent.get('name')}\n"
                    f"Your persona:\n{persona}\n\n"
                    f"Interview question: {prompt}"
                ),
            },
        ]
        return llm.chat(messages=messages, temperature=0.7, max_tokens=300)

    @classmethod
    def _save_interview(cls, simulation_id: str, record: Dict[str, Any]) -> None:
        path = cls._interviews_file(simulation_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        records: List[Dict[str, Any]] = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, list):
                    records = data
            except Exception:
                records = []
        records.append(record)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(records[-1000:], f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning("Failed to save interview history: %s", exc)

    @classmethod
    def interview_agent(
        cls,
        simulation_id: str,
        agent_id: int,
        prompt: str,
        platform: Optional[str] = None,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        if not prompt:
            raise ValueError("Interview prompt is required")
        personas = cls._load_personas(simulation_id)
        try:
            agent = personas[int(agent_id)]
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Agent not found: {agent_id}")

        config = cls._interview_topics(simulation_id)
        platforms = cls._interview_platforms(simulation_id, platform)
        timestamp = datetime.now().isoformat()

        results: Dict[str, Any] = {}
        for platform_name in platforms:
            try:
                answer = cls._ask_agent(agent, platform_name, prompt, config)
            except Exception as exc:
                answer = f"Interview failed: {exc}"
            record = {
                "agent_id": int(agent_id),
                "prompt": prompt,
                "response": answer,
                "platform": platform_name,
                "timestamp": timestamp,
            }
            cls._save_interview(simulation_id, record)
            results[platform_name] = record

        if platform:
            result = results[platform]
        elif len(results) == 1:
            result = next(iter(results.values()))
        else:
            result = {
                "agent_id": int(agent_id),
                "prompt": prompt,
                "platforms": results,
            }

        return {
            "success": True,
            "agent_id": int(agent_id),
            "prompt": prompt,
            "result": result,
            "timestamp": timestamp,
        }

    @classmethod
    def interview_agents_batch(
        cls,
        simulation_id: str,
        interviews: List[Dict[str, Any]],
        platform: Optional[str] = None,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        if not interviews:
            raise ValueError("interviews list is required")

        timestamp = datetime.now().isoformat()
        results: Dict[str, Any] = {}
        for item in interviews:
            agent_id = item.get("agent_id")
            prompt = item.get("prompt", "")
            item_platform = item.get("platform") or platform
            result = cls.interview_agent(
                simulation_id=simulation_id,
                agent_id=agent_id,
                prompt=prompt,
                platform=item_platform,
                timeout=timeout,
            )
            platforms = cls._interview_platforms(simulation_id, item_platform)
            for platform_name in platforms:
                record = None
                inner = result.get("result") or {}
                if platform_name in (inner.get("platforms") or {}):
                    record = inner["platforms"][platform_name]
                elif inner.get("platform") == platform_name:
                    record = inner
                if record:
                    results[f"{platform_name}_{agent_id}"] = record

        return {
            "success": True,
            "interviews_count": len(results),
            "results": results,
            "timestamp": timestamp,
        }

    @classmethod
    def interview_all_agents(
        cls,
        simulation_id: str,
        prompt: str,
        platform: Optional[str] = None,
        timeout: int = 180,
    ) -> Dict[str, Any]:
        personas = cls._load_personas(simulation_id)
        interviews = [
            {"agent_id": agent_id, "prompt": prompt, "platform": platform}
            for agent_id in sorted(personas)
        ]
        result = cls.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=interviews,
            platform=platform,
            timeout=timeout,
        )
        result["interviews_count"] = len(interviews)
        return result

    @classmethod
    def get_interview_history(
        cls,
        simulation_id: str,
        platform: Optional[str] = None,
        agent_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        path = cls._interviews_file(simulation_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except Exception:
            return []
        if not isinstance(records, list):
            return []
        if platform:
            records = [r for r in records if r.get("platform") == platform]
        if agent_id is not None:
            try:
                expected = int(agent_id)
                records = [r for r in records if int(r.get("agent_id", -1)) == expected]
            except (TypeError, ValueError):
                pass
        return list(reversed(records))[:max(1, int(limit))]

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    @classmethod
    def cleanup_simulation_logs(cls, simulation_id: str) -> Dict[str, Any]:
        """Remove run artifacts so a simulation can be restarted cleanly."""
        result: Dict[str, Any] = {
            "success": True,
            "cleaned": [],
            "errors": [],
        }

        thread = cls._threads.get(simulation_id)
        if thread and thread.is_alive():
            cls._stop_requests[simulation_id] = True
            thread.join(timeout=20)
        if thread and thread.is_alive():
            result["success"] = False
            result["errors"].append("Simulation worker is still running")
            return result

        for filename in ("run_state.json", "actions.jsonl", "interviews.json", "simulation.log"):
            path = os.path.join(cls._simulation_dir(simulation_id), filename)
            try:
                if os.path.exists(path):
                    os.remove(path)
                    result["cleaned"].append(filename)
            except Exception as exc:
                result["success"] = False
                result["errors"].append(f"{filename}: {exc}")

        with cls._state_guard:
            cls._run_states.pop(simulation_id, None)
            cls._threads.pop(simulation_id, None)
            cls._active_loops.pop(simulation_id, None)
        cls._stop_requests.pop(simulation_id, None)
        cls._manual_stop_requests.discard(simulation_id)
        return result

    @classmethod
    def close_simulation_env(
        cls, simulation_id: str, timeout: int = 30
    ) -> Dict[str, Any]:
        """Gracefully end the post-run interview environment."""
        state = cls.get_run_state(simulation_id)
        if state is None:
            raise ValueError(f"Simulation not found: {simulation_id}")

        if cls.is_running(simulation_id):
            state = cls.stop_simulation(simulation_id)

        state.runner_status = RunnerStatus.COMPLETED
        state.twitter_running = False
        state.reddit_running = False
        state.completed_at = state.completed_at or datetime.now().isoformat()
        cls._save_run_state(state)
        cls._sync_simulation_status(simulation_id, RunnerStatus.COMPLETED)

        return {
            "success": True,
            "message": "Simulation environment closed",
            "simulation_id": simulation_id,
            "runner_status": state.runner_status.value,
            "timestamp": datetime.now().isoformat(),
        }


__all__ = [
    "SimulationRunner",
    "RunnerStatus",
    "SimulationStopPending",
    "AgentAction",
    "RoundSummary",
    "SimulationRunState",
]
