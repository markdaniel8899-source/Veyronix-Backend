"""
Report Agent Serve
use local SQLite accomplish ReACT mode of simulation report generate

Function：
1. According to simulation requirement and local graph information generate report
2. Plan the directory structure first，then segment generate
3. Each paragraph uses ReACT Multiple rounds of thinking and reflection mode
4. support and user dialogue，Call the retrieve tool autonomously in a conversation
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, t
from .local_memory import LocalMemory

logger = get_logger('Veyronix.report_agent')


@dataclass
class SearchResult:
    """local search result"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count,
        }

    def to_text(self) -> str:
        text_parts = [f"Search query: {self.query}", f"Found {self.total_count} related results"]
        if self.facts:
            text_parts.append("\n### Related facts:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        return "\n".join(text_parts)


@dataclass
class InsightForgeResult:
    """Deep Insight retrieve result"""
    facts: List[str]
    entities: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    query: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "entities": self.entities,
            "relationships": self.relationships,
            "query": self.query,
        }

    def to_text(self) -> str:
        parts = [f"Deep insight query: {self.query}"]
        if self.facts:
            parts.append(f"\n### Related facts ({len(self.facts)}):")
            for i, fact in enumerate(self.facts[:20], 1):
                parts.append(f"{i}. {fact}")
        if self.entities:
            parts.append(f"\n### Related entities ({len(self.entities)}):")
            for entity in self.entities[:20]:
                parts.append(
                    f"- {entity.get('name', '?')} "
                    f"({entity.get('type', 'Entity')}): "
                    f"{str(entity.get('summary', ''))[:200]}"
                )
        if self.relationships:
            parts.append(f"\n### Relationships ({len(self.relationships)}):")
            for rel in self.relationships[:20]:
                parts.append(f"- {rel.get('fact') or rel}")
        return "\n".join(parts)


@dataclass
class PanoramaResult:
    """Panorama search result"""
    active: List[Dict[str, Any]]
    historical: List[Dict[str, Any]]
    query: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active": self.active,
            "historical": self.historical,
            "query": self.query,
        }

    def to_text(self) -> str:
        parts = [f"Panorama search: {self.query}"]
        parts.append(f"Active/related entities: {len(self.active)}")
        parts.append(f"Historical records: {len(self.historical)}")
        if self.active:
            parts.append("\n### Entities:")
            for entity in self.active[:30]:
                parts.append(
                    f"- {entity.get('name', '?')} "
                    f"({entity.get('type', 'Entity')}): "
                    f"{str(entity.get('summary', ''))[:200]}"
                )
        if self.historical:
            parts.append("\n### Recent activity:")
            for record in self.historical[:30]:
                parts.append(
                    f"- Round {record.get('round_num', '?')} "
                    f"{record.get('agent_id', '?')}: "
                    f"{str(record.get('content', ''))[:200]}"
                )
        return "\n".join(parts)


@dataclass
class InterviewResult:
    """virtual interview result"""
    agent_id: str
    agent_name: str
    question: str
    answer: str
    platform: str = ""
    interviews: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "question": self.question,
            "answer": self.answer,
            "platform": self.platform,
            "interviews": self.interviews,
        }

    def to_text(self) -> str:
        if self.interviews:
            parts = [f"Interview question: {self.question}"]
            for item in self.interviews:
                parts.append(
                    f"\n[{item.get('platform', '')}] "
                    f"{item.get('agent_name', item.get('agent_id', '?'))}:\n"
                    f"{item.get('answer', '')}"
                )
            return "\n".join(parts)
        return (
            f"Interviewee: {self.agent_name} ({self.agent_id})\n"
            f"Question: {self.question}\nAnswer: {self.answer}"
        )


@dataclass
class EntityView:
    """exposed to report Agent of entity view。"""
    id: str
    name: str
    type: str
    summary: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    labels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.id,
            "name": self.name,
            "type": self.type,
            "summary": self.summary,
            "attributes": self.attributes,
            "labels": self.labels,
        }


class LocalToolsService:
    """Local memory retrieve tool service（substitute Zep Cloud）"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or Config.DATABASE_PATH
        self.memory = LocalMemory(self.db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _persona(entity: Dict[str, Any]) -> Dict[str, Any]:
        persona = entity.get("persona")
        return persona if isinstance(persona, dict) else {}

    @classmethod
    def _normalize(cls, entity: Dict[str, Any]) -> Dict[str, Any]:
        persona = cls._persona(entity)
        entity_id = str(entity.get("id") or persona.get("uuid") or "")
        name = (
            persona.get("name")
            or persona.get("title")
            or entity.get("name")
            or f"Entity {entity_id[-6:]}"
        )
        entity_type = (
            persona.get("type")
            or persona.get("entity_type")
            or entity.get("type")
            or "Entity"
        )
        summary = (
            persona.get("summary")
            or persona.get("description")
            or persona.get("content")
            or ""
        )
        return {
            "uuid": entity_id,
            "name": str(name),
            "type": str(entity_type),
            "summary": str(summary),
            "attributes": {
                key: value
                for key, value in persona.items()
                if key not in {"content", "graph_id", "batch_id", "operation_id"}
            },
            "labels": [str(entity_type)],
        }

    def _scoped_agents(self, graph_id: Optional[str]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        if graph_id:
            try:
                records = self.memory.list_agents(graph_id)
            except Exception:
                records = []
        if not records:
            records = self.memory.list_agents()
        return [self._normalize(record) for record in records]

    @staticmethod
    def _matches(entity: Dict[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            [
                str(entity.get("name", "")),
                str(entity.get("type", "")),
                str(entity.get("summary", "")),
                json.dumps(entity.get("attributes", {}), ensure_ascii=False),
            ]
        ).lower()
        return all(term in haystack for term in query.lower().split())

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def search_graph(
        self,
        graph_id: str,
        query: str,
        limit: int = 10,
    ) -> SearchResult:
        """local graph search"""
        entities = [
            entity for entity in self._scoped_agents(graph_id)
            if self._matches(entity, query)
        ][:limit]

        interactions = self.memory.search_interactions(query, limit=limit)
        facts = [
            f"Round {item['round_num']} | Agent {item['agent_id']}: "
            f"{str(item['content'])[:300]}"
            for item in interactions
        ]

        relationships: List[Dict[str, Any]] = []
        for entity in entities:
            for edge in entity.get("attributes", {}).get("related_edges", []) or []:
                if isinstance(edge, dict):
                    relationships.append(edge)

        return SearchResult(
            facts=facts,
            edges=relationships,
            nodes=entities,
            query=query,
            total_count=len(entities) + len(facts),
        )

    def quick_search(
        self, graph_id: str, query: str, limit: int = 10
    ) -> SearchResult:
        return self.search_graph(graph_id=graph_id, query=query, limit=limit)

    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str = "",
        report_context: str = "",
    ) -> InsightForgeResult:
        entities = self._scoped_agents(graph_id)
        matched = [e for e in entities if self._matches(e, query)]
        if not matched:
            matched = entities[:10]

        interactions = self.memory.search_interactions(query, limit=30)
        facts = [
            f"Round {item['round_num']} | Agent {item['agent_id']}: "
            f"{str(item['content'])[:300]}"
            for item in interactions
        ]

        relationships: List[Dict[str, Any]] = []
        for entity in matched:
            for edge in entity.get("attributes", {}).get("related_edges", []) or []:
                if isinstance(edge, dict):
                    relationships.append(edge)

        if simulation_requirement and not facts:
            facts.append(f"Simulation requirement: {simulation_requirement}")

        return InsightForgeResult(
            facts=facts[:20],
            entities=matched[:20],
            relationships=relationships[:20],
            query=query,
        )

    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
    ) -> PanoramaResult:
        entities = self._scoped_agents(graph_id)
        matched = [e for e in entities if self._matches(e, query)]
        if not matched:
            matched = entities

        history: List[Dict[str, Any]] = []
        interactions = self.memory.get_recent_interactions(limit=100)
        for item in interactions:
            content = str(item.get("content") or "")
            if query and query.lower() not in content.lower():
                continue
            history.append(item)

        if not include_expired:
            history = history[:20]

        return PanoramaResult(
            active=matched,
            historical=history,
            query=query,
        )

    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
    ) -> InterviewResult:
        from .simulation_runner import SimulationRunner

        try:
            personas = SimulationRunner._load_personas(simulation_id)
        except Exception as exc:
            logger.warning("Interview agent lookup failed: %s", exc)
            personas = {}

        agent_ids = sorted(personas)[:max(1, int(max_agents))]
        if not agent_ids:
            return InterviewResult(
                agent_id="",
                agent_name="",
                question=interview_requirement,
                answer="No simulation agents available for interview.",
            )

        interviews = [
            {"agent_id": agent_id, "prompt": interview_requirement}
            for agent_id in agent_ids
        ]
        try:
            result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews,
                timeout=120,
            )
        except Exception as exc:
            logger.warning("Interviews failed: %s", exc)
            return InterviewResult(
                agent_id="",
                agent_name="",
                question=interview_requirement,
                answer=f"Interview failed: {exc}",
            )

        collected: List[Dict[str, Any]] = []
        for key, record in (result.get("results") or {}).items():
            agent_id = record.get("agent_id")
            agent = personas.get(int(agent_id), {}) if agent_id is not None else {}
            collected.append(
                {
                    "agent_id": agent_id,
                    "agent_name": agent.get("name") or f"Agent {agent_id}",
                    "platform": record.get("platform", ""),
                    "answer": record.get("response", ""),
                }
            )

        return InterviewResult(
            agent_id="",
            agent_name=f"{len(collected)} agents",
            question=interview_requirement,
            answer="",
            interviews=collected,
        )

    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        entities = self._scoped_agents(graph_id)
        type_counts: Dict[str, int] = {}
        edge_count = 0
        for entity in entities:
            type_counts[entity["type"]] = type_counts.get(entity["type"], 0) + 1
            edge_count += len(
                entity.get("attributes", {}).get("related_edges", []) or []
            )
        return {
            "graph_id": graph_id,
            "agent_count": len(entities),
            "total_nodes": len(entities),
            "total_edges": edge_count,
            "entity_types": type_counts,
            "interaction_count": len(
                self.memory.get_recent_interactions(limit=10000)
            ),
        }

    def get_entity_summary(
        self, graph_id: str, entity_name: str
    ) -> Dict[str, Any]:
        entities = self._scoped_agents(graph_id)
        target = entity_name.strip().lower()
        for entity in entities:
            if entity["name"].strip().lower() == target:
                return entity
        for entity in entities:
            if target and target in entity["name"].lower():
                return entity
        return {
            "error": f"Entity not found: {entity_name}",
            "graph_id": graph_id,
        }

    def get_entities_by_type(
        self, graph_id: str, entity_type: str
    ) -> List[EntityView]:
        expected = (entity_type or "").strip().lower()
        entities = [
            entity for entity in self._scoped_agents(graph_id)
            if entity["type"].strip().lower() == expected
        ]
        return [
            EntityView(
                id=entity["uuid"],
                name=entity["name"],
                type=entity["type"],
                summary=entity["summary"],
                attributes=entity["attributes"],
                labels=entity["labels"],
            )
            for entity in entities
        ]

    def get_simulation_context(
        self,
        graph_id: str,
        simulation_requirement: str = "",
    ) -> Dict[str, Any]:
        statistics = self.get_graph_statistics(graph_id)
        entities = self._scoped_agents(graph_id)
        interactions = self.memory.get_recent_interactions(limit=20)

        related_facts = [
            {
                "round": item.get("round_num"),
                "agent_id": item.get("agent_id"),
                "action": item.get("action"),
                "fact": str(item.get("content") or "")[:400],
            }
            for item in interactions
        ]

        return {
            "graph_id": graph_id,
            "simulation_requirement": simulation_requirement,
            "graph_statistics": statistics,
            "total_entities": len(entities),
            "entities": entities[:50],
            "related_facts": related_facts,
        }



class ReportLogger:
    """
    Report Agent detailed log recorder
    
    in report file folder generate agent_log.jsonl file，record each step detailed action。
    Each line is a complete of JSON object，include time stamp、action type、detailed content etc.。
    """
    
    def __init__(self, report_id: str):
        """
        initialize log recorder
        
        Args:
            report_id: report ID，Used to determine log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """ensure log file is stored in directory"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """get from start to now of time consuming（seconds）"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        record a log
        
        Args:
            action: action type，like 'start', 'tool_call', 'llm_response', 'section_complete' etc.
            stage: current stage，like 'planning', 'generating', 'completed'
            details: detailed content characters，Do not truncate
            section_title: current section title（optional）
            section_index: current section index（optional）
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # append write JSONL file
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """record report generate start"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": t('report.taskStarted')
            }
        )
    
    def log_planning_start(self):
        """record outline planning start"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": t('report.planningStart')}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """record get of context information during planning"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": t('report.fetchSimContext'),
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """record outline planning completed"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": t('report.planningComplete'),
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """record section generate start"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": t('report.sectionStart', title=section_title)}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """record ReACT thought process"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": t('report.reactThought', iteration=iteration)
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """record tool call"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": t('report.toolCall', toolName=tool_name)
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """record tool call result（complete content，Do not truncate）"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # complete result，Do not truncate
                "result_length": len(result),
                "message": t('report.toolResult', toolName=tool_name)
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """record LLM response（complete content，Do not truncate）"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # complete response，Do not truncate
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": t('report.llmResponse', hasToolCalls=has_tool_calls, hasFinalAnswer=has_final_answer)
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """record section content generate completed（only record content，Does not mean the entire section completed）"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # complete content，Do not truncate
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": t('report.sectionContentDone', title=section_title)
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        record section generate completed

        The front end should listen to this log to determine whether a section is actually completed.，and get complete content
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": t('report.sectionComplete', title=section_title)
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """record report generated completed"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": t('report.reportComplete')
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """record error"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": t('report.errorOccurred', error=error_message)
            }
        )


class ReportConsoleLogger:
    """
    Report Agent Console log recorder
    
    Change the console style of log（INFO、WARNING etc.）Write to the report file folder of console_log.txt file。
    These log and agent_log.jsonl different，Is a pure text format of console output。
    """
    
    def __init__(self, report_id: str):
        """
        initialize console log recorder
        
        Args:
            report_id: report ID，Used to determine log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """ensure log file is stored in directory"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """settings file handler，Write log to file at the same time"""
        import logging
        
        # create file handler
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # use and console same as concise format
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # add to report_agent Related of logger
        loggers_to_attach = [
            'Veyronix.report_agent',
            'Veyronix.local_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # avoid duplication add
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """close file handler and from logger medium remove"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'Veyronix.report_agent',
                'Veyronix.local_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """ensure close file handler on destruction"""
        self.close()


class ReportStatus(str, Enum):
    """report status"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """report section"""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """convert for Markdown format"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """report outline"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """convert for Markdown format"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """complete report"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt Template constants
# ═══════════════════════════════════════════════════════════════

# ── tool description ──

TOOL_DESC_INSIGHT_FORGE = """\
[Deep insight search - powerful retrieval tool]
This is the powerful retrieval function, designed for deep analysis. It:
1. Automatically breaks your question into sub-questions
2. Retrieves information from the simulation graph along multiple dimensions
3. Combines semantic search, entity analysis and relationship-chain tracing
4. Returns the most complete and deepest retrieval content

[Use it when]
- You need to analyse a topic in depth
- You need to understand several aspects of the event
- You need rich material to support a report section

[Returns]
- Related facts (quotable as-is)
- Core entity insights
- Relationship chain analysis"""

TOOL_DESC_PANORAMA_SEARCH = """\
[Wide search - the full picture]
This tool retrieves the complete picture of the simulation results. It is ideal for understanding how the event evolved. It:
1. Retrieves all related nodes and relationships
2. Separates currently valid facts from historical/expired facts
3. Shows how the discussion evolved over time

[Use it when]
- You need the full timeline of the event
- You need to compare how the discussion changed between phases
- You need comprehensive entity and relationship information

[Returns]
- Currently valid facts (latest simulation results)
- Historical/expired facts (evolution record)
- All entities involved"""

TOOL_DESC_QUICK_SEARCH = """\
[Quick search - fast retrieval]
A lightweight quick-retrieval tool for simple, direct lookups.

[Use it when]
- You need to find one specific piece of information
- You need to verify a fact
- You need a simple lookup

[Returns]
- The list of facts most relevant to the query"""

TOOL_DESC_INTERVIEW_AGENTS = """\
[Deep interview - real agent interviews (both platforms)]
Calls the interview API of the running simulation to interview real simulated agents.
This is not an LLM simulation: it calls the real interview interface and returns the agents' original answers.
By default it interviews on Twitter and Reddit at the same time for a fuller picture.

How it works:
1. Reads the persona files to learn about all simulated agents
2. Selects the agents most relevant to the interview topic (students, media, officials, ...)
3. Generates interview questions automatically
4. Calls /api/simulation/interview/batch to interview both platforms for real
5. Consolidates all answers into a multi-perspective analysis

[Use it when]
- You need to know how different roles see the event (how do students see it? media? officials?)
- You need to collect opinions from many sides
- You need the real answers produced inside the simulation
- You want the report to include verbatim interview excerpts

[Returns]
- Identity details of the interviewed agents
- Each agent's answers on Twitter and Reddit
- Key quotes (directly quotable)
- Interview summaries and a comparison of viewpoints

[Important] The simulation environment must be running to use this tool."""

# ── Outline planning prompt ──

PLAN_SYSTEM_PROMPT = """\
You are an expert writer of "future prediction reports" with a god's-eye view of the
simulated world: you can observe every agent's behaviour, statements and interactions.

[CORE IDEA]
We built a simulated world and injected a specific "simulation requirement" into it as
a variable. The evolution of that world is the prediction of what may happen in the
future. You are not looking at "experiment data", you are watching a rehearsal of the future.

[YOUR TASK]
Write a "future prediction report" that answers:
1. Under the conditions we set, what happened in the future?
2. How did the different groups of agents react and act?
3. Which future trends and risks does this simulation reveal?

[REPORT POSITIONING]
- This is a prediction report based on the simulation, revealing "if this happens, this is what the future looks like"
- Focus on the predicted outcomes: event trajectory, group reactions, emergent phenomena, latent risks
- The words and actions of agents in the simulated world are predictions of future human behaviour
- It is NOT an analysis of the current state of the real world
- It is NOT a generic overview of public sentiment

[CHAPTER COUNT]
- Minimum 2 chapters, maximum 5 chapters
- No sub-chapters; write each chapter as one complete piece
- Keep the content concise and focused on the core predicted findings
- Design the chapter structure yourself based on the predictions

Return the report outline as JSON:
{
    "title": "report title",
    "summary": "one sentence summary of the core predicted finding",
    "sections": [
        {
            "title": "chapter title",
            "description": "what the chapter covers"
        }
    ]
}

Note: the sections array must contain at least 2 and at most 5 elements."""

PLAN_USER_PROMPT_TEMPLATE = """\
[PREDICTION SCENARIO]
The variable injected into the simulated world (simulation requirement): {simulation_requirement}

[SIMULATION SCALE]
- Number of participating entities: {total_nodes}
- Number of relationships between entities: {total_edges}
- Entity type distribution: {entity_types}
- Number of active agents: {total_entities}

[SAMPLE OF PREDICTED FUTURE FACTS]
{related_facts_json}

Review this rehearsal of the future from a god's-eye view:
1. Under the conditions we set, what state does the future show?
2. How did the different groups (agents) react and act?
3. Which future trends does this simulation reveal?

Design the report chapter structure that best fits the predictions.

[Reminder] Number of chapters: minimum 2, maximum 5. Keep the content concise and focused
on the core predicted findings."""

# ── Section generation prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert writer of "future prediction reports" and you are writing one chapter.

Report title: {report_title}
Report summary: {report_summary}
Prediction scenario (simulation requirement): {simulation_requirement}

Chapter you are writing now: {section_title}

═══════════════════════════════════════════════════════════════
[CORE IDEA]
═══════════════════════════════════════════════════════════════

The simulated world is a rehearsal of the future. We injected specific conditions
(simulation requirement) into it, and the behaviour and interactions of the agents in
the simulation are predictions of future human behaviour.

Your task:
- Reveal what happened in the future under the conditions we set
- Predict how the different groups (agents) reacted and acted
- Identify future trends, risks and opportunities worth attention

Do NOT write an analysis of the current state of the real world.
Focus on "what the future looks like" - the simulation result IS the predicted future.

═══════════════════════════════════════════════════════════════
[THE MOST IMPORTANT RULES - YOU MUST FOLLOW THEM]
═══════════════════════════════════════════════════════════════

1. [YOU MUST CALL TOOLS TO OBSERVE THE SIMULATED WORLD]
   - You are observing the rehearsal of the future from a god's-eye view
   - All content must come from events and agent statements inside the simulated world
   - Never use your own knowledge to write the report
   - Call at least 3 tools per chapter (at most 5) to observe the world that represents the future

2. [YOU MUST QUOTE THE AGENTS' ORIGINAL STATEMENTS]
   - The statements and actions of agents are predictions of future human behaviour
   - Show these predictions as quotations in the report, for example:
     > "A group of people would say: the original text..."
   - These quotations are the core evidence of the prediction

3. [LANGUAGE CONSISTENCY - TRANSLATE QUOTED CONTENT INTO THE REPORT LANGUAGE]
   - Tool results may contain wording in a different language than the report
   - The whole report must be written in the language requested by the user
   - When you quote content returned by a tool in another language, translate it into the
     report language before writing it down, keeping the meaning intact and the wording natural
   - This rule applies to body text and to quotation blocks (the > format)

4. [REPRESENT THE PREDICTED RESULTS FAITHFULLY]
   - The report must reflect the simulated results that represent the future
   - Do not add information that does not exist in the simulation
   - If something is under-determined, say so honestly

5. [NEVER FABRICATE DATA]
   - Never invent usernames, quotations, statistics or interaction data
   - Never include <tool_result> blocks in your reply - only the system provides tool results
   - Only reference entities, quotations and data that really appear in the tool results
   - If the tool results do not contain something, say so instead of inventing it

═══════════════════════════════════════════════════════════════
[FORMATTING RULES - EXTREMELY IMPORTANT]
═══════════════════════════════════════════════════════════════

[One chapter = the smallest content unit]
- Each chapter is the smallest content block of the report
- Never use any Markdown heading inside the chapter (#, ##, ###, #### and so on)
- Never repeat the chapter title at the start of the content
- The system adds the chapter title; you only write body content
- Use **bold**, paragraph breaks, quotations and lists to structure the content, but no headings

[Correct example]
```
This chapter analyses how the event spread. A close look at the simulated data shows...

**First reaction phase**

Social media acted as the first response point and carried the initial wave of attention:

> "68% of the first-wave responses came from social media..."

**Amplification phase**

The second platform further amplified the impact of the event:

- Strong visual pull
- High emotional resonance
```

[Wrong example]
```
## Executive summary          <- wrong, never add headings
### 1. First phase            <- wrong, do not split with ###
#### 1.1 Detailed analysis    <- wrong, do not subdivide with ####

This chapter analyses...
```

═══════════════════════════════════════════════════════════════
[AVAILABLE RETRIEVAL TOOLS] (call 3-5 times per chapter)
═══════════════════════════════════════════════════════════════

{tools_description}

[Tool usage tips - mix different tools, do not use only one]
- insight_forge: deep analysis that decomposes the question and searches facts and relationships from several angles
- panorama_search: wide search for the overall picture, timeline and evolution
- quick_search: fast verification of one specific detail
- interview_agents: interview simulated agents for first-person views and real reactions

═══════════════════════════════════════════════════════════════
[WORKFLOW]
═══════════════════════════════════════════════════════════════

On every turn you do exactly one of the following two things (never both):

Option A - call a tool:
Write your reasoning, then call one tool in this format:
<tool_call>
{{"name": "tool_name", "parameters": {{"parameter_name": "parameter_value"}}}}
</tool_call>
The system executes the tool and returns the result. You must not write tool results yourself.

Option B - output the final content:
Once you have enough information from the tools, start the chapter content with "Final Answer:".

Strictly forbidden:
- Never combine a tool call and a Final Answer in the same reply
- Never fabricate tool results (Observations); the system injects every tool result
- Call at most one tool per reply

═══════════════════════════════════════════════════════════════
[CHAPTER CONTENT REQUIREMENTS]
═══════════════════════════════════════════════════════════════

1. The content must be based on the simulation data retrieved with the tools
2. Quote the original text often to show the effect of the simulation
3. Use Markdown formatting (but no headings):
   - Use **bold text** for emphasis (instead of sub-headings)
   - Use lists (- or 1.2.3.) to organise points
   - Use blank lines to separate paragraphs
   - Never use #, ##, ###, #### heading syntax
4. [QUOTATION FORMAT - each quotation must be its own paragraph]
   A quotation must stand alone, with a blank line before and after it, and must not be
   merged into a paragraph:

   Correct format:
   ```
   The response from the university was seen as lacking substance.

   > "The response pattern looked rigid and slow in a fast-moving social media environment."

   That assessment reflects the wider public frustration.
   ```

   Wrong format:
   ```
   The response from the university was seen as lacking substance.> "The response pattern..." That assessment reflects...
   ```
5. Stay logically consistent with the other chapters
6. [AVOID REPETITION] Read the completed chapters below carefully and do not repeat the same information
7. [REPEAT] Do not add any headings! Use **bold** instead of sub-headings"""

SECTION_USER_PROMPT_TEMPLATE = """\
Completed chapter content (read carefully, avoid repetition):
{previous_content}

═══════════════════════════════════════════════════════════════
[CURRENT TASK] Write the chapter: {section_title}
═══════════════════════════════════════════════════════════════

[Important reminders]
1. Read the completed chapters above and avoid repeating the same content!
2. You must call tools for simulation data before you start writing
3. Mix different tools, do not use only one
4. The report content must come from the retrieval results, not from your own knowledge

[⚠️ Format warning - you must follow it]
- Do not write any headings (#, ##, ###, #### are all forbidden)
- Do not start with "{section_title}"
- The system adds the chapter title
- Write body content directly and use **bold** instead of sub-headings

Please start:
1. First think (Thought) about what information this chapter needs
2. Then call a tool (Action) to retrieve simulation data
3. Once you have enough information, output the Final Answer (body content only, no headings)"""

# ── Messages used inside the ReACT loop ──

REACT_OBSERVATION_TEMPLATE = """\
Observation (retrieval result):

═══ tool {tool_name} returned ═══
{result}

═══════════════════════════════════════════════════════════════
Tools called {tool_calls_count}/{max_tool_calls} times (used: {used_tools_str}){unused_hint}
- If you have enough information: start the chapter content with "Final Answer:" (quote the text above)
- If you need more information: call one tool to continue retrieving
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "[Notice] You called the tools only {tool_calls_count} times, at least {min_tool_calls} are required."
    "Call the tools again to retrieve more simulation data, then output the Final Answer.{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "You called the tools only {tool_calls_count} times, at least {min_tool_calls} are required."
    "Call the tools to retrieve simulation data.{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "The tool call limit has been reached ({tool_calls_count}/{max_tool_calls}); no more tool calls are allowed."
    'Output the chapter content now, starting with "Final Answer:", based on the information you already have.'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 You have not used yet: {unused_list}. Try different tools for a multi-angle view."

REACT_FORCE_FINAL_MSG = "The tool call limit has been reached. Output the Final Answer: and write the chapter content now."

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
You are a concise and efficient simulation prediction assistant.

[CONTEXT]
Prediction conditions: {simulation_requirement}

[ANALYSIS REPORT ALREADY GENERATED]
{report_content}

[RULES]
1. Answer based on the report above first
2. Answer directly, without long reasoning
3. Call a tool only when the report is not enough to answer
4. Keep the answer concise, clear and organised

[AVAILABLE TOOLS] (use only when needed, at most 1-2 calls)
{tools_description}

[TOOL CALL FORMAT]
<tool_call>
{{"name": "tool_name", "parameters": {{"parameter_name": "parameter_value"}}}}
</tool_call>

[ANSWER STYLE]
- Direct and concise, no long essays
- Use the > format to quote key content
- Give the conclusion first, then explain"""

CHAT_OBSERVATION_SUFFIX = "\n\nPlease answer the question concisely."


# ═══════════════════════════════════════════════════════════════
# ReportAgent main class
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - simulation report generate Agent

    use ReACT（Reasoning + Acting）model：
    1. planning stage：Analysis simulation requirement，Planning the report directory structure
    2. generate stage：generate content section by section，Each section can call the tool multiple times to get information.
    3. reflection stage：check content completeness and accuracy
    """
    
    # maximum tool call times count（per section）
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # maximum reflection rounds count
    MAX_REFLECTION_ROUNDS = 3
    
    # of maximum tool calls times count in conversation
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        local_tools: Optional[LocalToolsService] = None
    ):
        """
        initialize Report Agent
        
        Args:
            graph_id: graph ID
            simulation_id: simulation ID
            simulation_requirement: simulation requirement description
            llm_client: LLM client（optional）
            local_tools: Zep Tool services（optional）
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.local_tools = local_tools or LocalToolsService()
        
        # Tool definition
        self.tools = self._define_tools()
        
        # log recorder（in generate_report medium initialise）
        self.report_logger: Optional[ReportLogger] = None
        # Console log recorder（in generate_report medium initialise）
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(t('report.agentInitDone', graphId=graph_id, simulationId=simulation_id))
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """Define available tools"""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "The question or topic you want to analyse in depth",
                    "report_context": "Context of the current report section (optional, helps generate sharper sub-questions)"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "Search query used for relevance ranking",
                    "include_expired": "Include expired/historical content (default True)"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "Search query string",
                    "limit": "Number of results to return (optional, default 10)"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "Interview topic or requirement (e.g. 'how students view the dormitory formaldehyde incident')",
                    "max_agents": "Maximum number of agents to interview (optional, default 5, max 10)"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Execute line tool call
        
        Args:
            tool_name: Tool name
            parameters: tool parameter
            report_context: report context（used for InsightForge）
            
        Returns:
            Tool execution line result（text format）
        """
        logger.info(t('report.executingTool', toolName=tool_name, params=parameters))
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.local_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # breadth search - get the whole picture
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.local_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # Simple search - fast retrieve
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.local_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # in-depth interview - call true of OASIS interview API get simulation Agent of answer（double platform）
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.local_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== backwards compatible of old tools（Internal redirect to new tool） ==========
            
            elif tool_name == "search_graph":
                # Redirect to quick_search
                logger.info(t('report.redirectToQuickSearch'))
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.local_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.local_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # Redirect to insight_forge，because it is more powerful
                logger.info(t('report.redirectToInsightForge'))
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.local_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"Unknown tool: {tool_name}. Use one of: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(t('report.toolExecFailed', toolName=tool_name, error=str(e)))
            return f"Tool execution failed: {str(e)}"
    
    # Legal of tool name collection，for bare JSON Fallback parse time verification
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        from LLM parse tool call in response

        support of format（by priority）：
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. bare JSON（response as a whole or a single line is a tool call JSON）
        """
        tool_calls = []

        # format 1: XML style（Standard format）
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # format 2: fallback - LLM Direct output naked JSON（No bag <tool_call> label）
        # only in format 1 Try when no match，Avoid mismatch of in the text JSON
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # response possibly include reflection characters + bare JSON，Try to extract most after JSON object
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """Check parse out of JSON Is it a legal of tool call?"""
        # support {"name": ..., "parameters": ...} and {"tool": ..., "params": ...} Two key names
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # Unified key name for name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """generate tool description text"""
        desc_parts = ["Available tools:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Parameters: {params_desc}")
        return "\n".join(desc_parts)

    @staticmethod
    def _strip_fake_tool_results(response: str) -> str:
        """Strip any <tool_result> blocks the LLM fabricated in its response.

        When the LLM generates a <tool_call> block and then continues to generate
        a <tool_result> block in the same response, we must strip the fake result
        before appending to message history. The real tool result will be injected
        separately by the system.
        """
        tag_pattern = re.compile(r'</?tool_result\b[^>]*>', flags=re.IGNORECASE)
        parts = []
        cursor = 0
        depth = 0

        for match in tag_pattern.finditer(response):
            if depth == 0:
                parts.append(response[cursor:match.start()])

            if match.group(0).lstrip().startswith('</'):
                depth = max(0, depth - 1)
            else:
                depth += 1
            cursor = match.end()

        if depth == 0:
            parts.append(response[cursor:])

        cleaned = ''.join(parts)
        # Treat a malformed opening tag without a closing `>` as unsafe too.
        cleaned = re.sub(r'<tool_result\b.*$', '', cleaned, flags=re.IGNORECASE | re.DOTALL)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        planning report outline
        
        use LLM Analysis simulation requirement，Planning the report of directory structure
        
        Args:
            progress_callback: progress callback function count
            
        Returns:
            ReportOutline: report outline
        """
        logger.info(t('report.startPlanningOutline'))
        
        if progress_callback:
            progress_callback("planning", 0, t('progress.analyzingRequirements'))
        
        # First get the simulation context
        context = self.local_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, t('progress.generatingOutline'))
        
        system_prompt = f"{PLAN_SYSTEM_PROMPT}\n\n{get_language_instruction()}"
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, t('progress.parsingOutline'))
            
            # parse outline
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Simulation Analysis Report"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, t('progress.outlinePlanComplete'))
            
            logger.info(t('report.outlinePlanDone', count=len(sections)))
            return outline
            
        except Exception as e:
            logger.error(t('report.outlinePlanFailed', error=str(e)))
            # return default outline（3 section，for fallback）
            return ReportOutline(
                title="Future Prediction Report",
                summary="Analysis of the future trends and risks predicted by the simulation",
                sections=[
                    ReportSection(title="Prediction Scenario and Key Findings"),
                    ReportSection(title="Predicted Audience Behaviour"),
                    ReportSection(title="Trend Outlook and Risk Warnings")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        use ReACT Mode generate single section content
        
        ReACT cycle：
        1. Thought（think）- Analyze what information is needed
        2. Action（line move）- Call the tool to get information
        3. Observation（observe）- Analysis tool return result
        4. Repeat until to information is sufficient or to maximum times count
        5. Final Answer（most final answer）- generate section content
        
        Args:
            section: to generate section
            outline: complete outline
            previous_sections: previous section of content（for stable coherence）
            progress_callback: progress callback
            section_index: section index（for log record）
            
        Returns:
            section content（Markdown format）
        """
        logger.info(t('report.reactGenerateSection', title=section.title))
        
        # record section start log
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        # build user prompt - Pass in maximum for each completed section 4000 characters
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # most per section 4000 characters
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(this is the first chapter)"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT cycle
        tool_calls_count = 0
        max_iterations = 5  # maximum iteration rounds count
        min_tool_calls = 3  # most few tool calls times count
        conflict_retries = 0  # Tool call and Final Answer Simultaneous occurrence of consecutive conflicts times count
        used_tools = set()  # record has called of tool name
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # report context，used for InsightForge of sub question generate
        report_context = f"Section title: {section.title}\nSimulation requirement: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    t('progress.deepSearchAndWrite', current=tool_calls_count, max=self.MAX_TOOL_CALLS_PER_SECTION)
                )
            
            # call LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # check LLM return whether for None（API exception or content for empty）
            if response is None:
                logger.warning(t('report.sectionIterNone', title=section.title, iteration=iteration + 1))
                # if and iteration times count，add message and retry
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(the response was empty)"})
                    messages.append({"role": "user", "content": "Please continue generating the content."})
                    continue
                # most last times iteration also returns None，Break out of the loop and enter the forced ending
                break

            logger.debug(f"LLM response: {response[:200]}...")

            # parse one times，Reuse result
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── Conflict handling：LLM At the same time the output tool calls and Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    t('report.sectionConflict', title=section.title, iteration=iteration+1, conflictCount=conflict_retries)
                )

                if conflict_retries <= 2:
                    # first two times：Discard this times response，Require LLM Repeat new reply
                    cleaned_response = ReportAgent._strip_fake_tool_results(response)
                    messages.append({"role": "assistant", "content": cleaned_response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "[Format error] Your reply contained both a tool call and a Final Answer, which is not allowed.\n"
                            "Each reply must do exactly one of the following:\n"
                            "- Call one tool (emit one <tool_call> block, do not write a Final Answer)\n"
                            "- Output the final content (start with 'Final Answer:', do not include <tool_call>)\n"
                            "Please reply again and do only one of them."
                        ),
                    })
                    continue
                else:
                    # third times：Downgrade processing，Truncate to first tool call，enforce line
                    logger.warning(
                        t('report.sectionConflictDowngrade', title=section.title, conflictCount=conflict_retries)
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # record LLM response log
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── Condition 1：LLM output Final Answer ──
            if has_final_answer:
                cleaned_response = ReportAgent._strip_fake_tool_results(response)
                # Insufficient tool call times count，Refuse and ask to continue debugging tools
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": cleaned_response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"(you have not used these tools yet, try them for a broader view: {', '.join(unused_tools)})" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # normal end
                final_answer = cleaned_response.split("Final Answer:")[-1].strip()
                logger.info(t('report.sectionGenDone', title=section.title, count=tool_calls_count))

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── Condition 2：LLM Try calling the tool ──
            if has_tool_calls:
                # Tool quota has been exhausted → clearly informed，require output Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    cleaned_response = ReportAgent._strip_fake_tool_results(response)
                    messages.append({"role": "assistant", "content": cleaned_response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # Execute only the first tool call on line
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(t('report.multiToolOnlyFirst', total=len(tool_calls), toolName=call['name']))

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # build does not use tool hint
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                cleaned_response = ReportAgent._strip_fake_tool_results(response)
                messages.append({"role": "assistant", "content": cleaned_response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── Condition 3：Both no tool calls，Also no Final Answer ──
            cleaned_response = ReportAgent._strip_fake_tool_results(response)
            messages.append({"role": "assistant", "content": cleaned_response})

            if tool_calls_count < min_tool_calls:
                # Insufficient tool call times count，recommended not used of tool
                unused_tools = all_tools - used_tools
                unused_hint = f"(you have not used these tools yet, try them for a broader view: {', '.join(unused_tools)})" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # Tool calls are sufficient，LLM output content but without "Final Answer:" prefix
            # Directly use this content as for most final answer，No more idling
            logger.info(t('report.sectionNoPrefix', title=section.title, count=tool_calls_count))
            final_answer = cleaned_response

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # up to maximum iteration times count，Force generate content
        logger.warning(t('report.sectionMaxIter', title=section.title))
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # check when forcing closing LLM return whether for None
        if response is None:
            logger.error(t('report.sectionForceFailed', title=section.title))
            final_answer = t('report.sectionGenFailedContent')
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # record section content generate completed log
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        generate complete report（Section real-time output）
        
        Immediately after each section generate completed save to file folder，No need etc. To be completed report completed。
        file structure：
        reports/{report_id}/
            meta.json       - report element information
            outline.json    - report outline
            progress.json   - generate progress
            section_01.md   - No.1 section
            section_02.md   - No.2 section
            ...
            full_report.md  - complete report
        
        Args:
            progress_callback: progress callback function count (stage, progress, message)
            report_id: report ID（optional，If not passed, it will automatically generate）
            
        Returns:
            Report: complete report
        """
        import uuid
        
        # if no passed in report_id，then automatically generate
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # Completed of section title list（for progress tracking）
        completed_section_titles = []
        
        try:
            # initialize：create report file folder and existitial status
            ReportManager._ensure_report_folder(report_id)
            
            # initialize log recorder（structured log agent_log.jsonl）
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # initialize console log recorder（console_log.txt）
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, t('progress.initReport'),
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # stage 1: planning outline
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, t('progress.startPlanningOutline'),
                completed_sections=[]
            )
            
            # record planning start log
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, t('progress.startPlanningOutline'))
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # record planning completed log
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # save outline to file
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, t('progress.outlineDone', count=len(outline.sections)),
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(t('report.outlineSavedToFile', reportId=report_id))
            
            # stage 2: generate section by section（section save）
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # save content for context
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # update progress
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    t('progress.generatingSection', title=section.title, current=section_num, total=total_sections),
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )

                if progress_callback:
                    progress_callback(
                        "generating",
                        base_progress,
                        t('progress.generatingSection', title=section.title, current=section_num, total=total_sections)
                    )
                
                # generate main section content
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # save section
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # record section completed log
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(t('report.sectionSaved', reportId=report_id, sectionNum=f"{section_num:02d}"))
                
                # update progress
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    t('progress.sectionDone', title=section.title),
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # stage 3: Assemble complete report
            if progress_callback:
                progress_callback("generating", 95, t('progress.assemblingReport'))
            
            ReportManager.update_progress(
                report_id, "generating", 95, t('progress.assemblingReport'),
                completed_sections=completed_section_titles
            )
            
            # use ReportManager Assemble complete report
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # calculate total time taken
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # record report completed log
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # save most final report
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, t('progress.reportComplete'),
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, t('progress.reportComplete'))
            
            logger.info(t('report.reportGenDone', reportId=report_id))
            
            # close console log recorder
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(t('report.reportGenFailed', error=str(e)))
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # record error log
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # save failed status
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, t('progress.reportFailed', error=str(e)),
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # ignore save failed of error
            
            # close console log recorder
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        and Report Agent dialogue
        
        in conversation Agent can call the retrieve tool autonomously to answer the question
        
        Args:
            message: user message
            chat_history: conversation history
            
        Returns:
            {
                "response": "Agent reply",
                "tool_calls": [Call of tool list],
                "sources": [source of information]
            }
        """
        logger.info(t('report.agentChat', message=message[:50]))
        
        chat_history = chat_history or []
        
        # get generated of report content
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # Limit report length，Avoid long context
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [report content truncated] ..."
        except Exception as e:
            logger.warning(t('report.fetchReportFailed', error=e))
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "(no report available yet)",
            tools_description=self._get_tools_description(),
        )
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        # build message
        messages = [{"role": "system", "content": system_prompt}]
        
        # add history conversation
        for h in chat_history[-10:]:  # Limit history length
            messages.append(h)
        
        # add user message
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT cycle（Simplified version）
        tool_calls_made = []
        max_iterations = 2  # Reduce iteration rounds count
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # parse tool call
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # no tool call，Return response directly
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                clean_response = ReportAgent._strip_fake_tool_results(clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Execute line tool call（Limit count）
            tool_results = []
            for call in tool_calls[:1]:  # execute most lines per rounds 1 times tool call
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # Limit result length
                })
                tool_calls_made.append(call)
            
            # add result to message
            cleaned_response = ReportAgent._strip_fake_tool_results(response)
            messages.append({"role": "assistant", "content": cleaned_response})
            observation = "\n".join([f"[{r['tool']} result]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # up to maximum iterations，get most final response
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Clean response
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        clean_response = ReportAgent._strip_fake_tool_results(clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    report manager
    
    Responsible for report of persistence storage and retrieval
    
    file structure（section output）：
    reports/
      {report_id}/
        meta.json          - report element information and status
        outline.json       - report outline
        progress.json      - generate progress
        section_01.md      - No.1 section
        section_02.md      - No.2 section
        ...
        full_report.md     - complete report
    """
    
    # report storage directory
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """ensure report root directory is stored in"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """get report file folder path"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """ensure report file folder in and return path"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """get report element information file path"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """get complete report Markdown file path"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """get outline file path"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """get progress file path"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """get section Markdown file path"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """get Agent log file path"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """get console log file path"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        get console log content
        
        This is the console output log during report generate（INFO、WARNING etc.），
        and agent_log.jsonl of structured log differs。
        
        Args:
            report_id: report ID
            from_line: from line start read（for incremental get，0 Represents from header start）
            
        Returns:
            {
                "logs": [log line list],
                "total_lines": total line count,
                "from_line": Starting line number,
                "has_more": Are there any more logs
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # Keep original log line，Remove trailing line swap
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # read to end
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        get complete of console log（one times sex get all）
        
        Args:
            report_id: report ID
            
        Returns:
            log line list
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        get Agent log content
        
        Args:
            report_id: report ID
            from_line: from line start read（for incremental get，0 Represents from header start）
            
        Returns:
            {
                "logs": [log eye list],
                "total_lines": total line count,
                "from_line": Starting line number,
                "has_more": Are there any more logs
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # Skip parse failed of line
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # read to end
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        get complete of Agent log（Used for one-time get all）
        
        Args:
            report_id: report ID
            
        Returns:
            log eye list
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        save report outline
        
        called immediately after in planning stage completed
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(t('report.outlineSaved', reportId=report_id))
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        save single section

        in Called immediately after each section generate completed，Implement section output

        Args:
            report_id: report ID
            section_index: section index（from 1 start）
            section: section object

        Returns:
            save of file path
        """
        cls._ensure_report_folder(report_id)

        # build section Markdown NO NAME SPACE NO KEY VALUE!! - Clean possibly stored in of duplicate title
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # save file
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(t('report.sectionFileSaved', reportId=report_id, fileSuffix=file_suffix))
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Clean up section content
        
        1. remove content Beginning and section title Duplicate of Markdown title line
        2. Put all ### and below of title convert for bold text
        
        Args:
            content: Original content
            section_title: Section Title
            
        Returns:
            Post-cleanup of content
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # check if yes Markdown title line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # check if and section title is a duplicate of title（Skip Before 5 in-line of repeats）
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # the all level of title（#, ##, ###, ####etc.）convert for bold
                # because section title is added by the system，there should be no title in the content
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # add empty line
                continue
            
            # if the previous line is skipped of title，and the current behaviour is empty，Also skip
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # remove start of empty line
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # remove of divider at the beginning
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # Also remove the empty line after the separator line
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        update report generate progress
        
        Frontend can read via progress.json get real-time progress
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """get report generate progress"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        get generated of section list
        
        return all saved of section file information
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # from file name parse section index
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        Assemble complete report
        
        from saved of section file Assembly complete report，Parallel line title cleanup
        """
        folder = cls._get_report_folder(report_id)
        
        # build report header
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # by sequentially read all section file
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # Post Processing：Cleanup report of title question
        md_content = cls._post_process_report(md_content, outline)
        
        # save complete report
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(t('report.fullReportAssembled', reportId=report_id))
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        Post-processing report content
        
        1. remove Duplicate of title
        2. Keep report master title(#)Section Title(##)，remove other level of title(###, ####etc.)
        3. Clean excess of empty line and divider
        
        Args:
            content: Original report content
            outline: report outline
            
        Returns:
            After processing of content
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # Collect outline of all section title
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # check if its a title line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # check if it is a duplicate title（in Continuous 5 same content of title in line）
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # Skip repeating title and after of empty line
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # title hierarchy：
                # - # (level=1) Keep only the main title of the report
                # - ## (level=2) Section Title
                # - ### and below (level>=3) convert for bold text
                
                if level == 1:
                    if title == outline.title:
                        # Keep report master title
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # section title error use#，Fix for##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # other first level title transfer for bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # Section Title
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # Non section of secondary title to for bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### and below of title convert for bold text
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # Skip title followed by of separator line
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # leave only one empty line after title
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Clean up continuous of multi-empty line（Hold most many 2 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT）
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """save report Meta information and complete report"""
        cls._ensure_report_folder(report.report_id)
        
        # save Meta information JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # save outline
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # save complete Markdown report
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(t('report.reportSaved', reportId=report.report_id))
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """get report"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # compatible old format：check direct storage in reports under directory of file
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Rebuild Report object
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # if markdown_content for Empty，Try from full_report.md read
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """According to simulation ID get report"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # new format：file folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # compatible old format：JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """column out report"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # new format：file folder
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # compatible old format：JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # by create time reverse order
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """delete report（Entire file folder）"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # new format：delete whole file folder
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(t('report.reportFolderDeleted', reportId=report_id))
            return True
        
        # compatible old format：delete Separate of file
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
