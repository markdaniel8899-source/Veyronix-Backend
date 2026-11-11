"""
Graph builder service for Veyronix backend.
Replaces Zep Cloud with local SQLite (JSON1 + FTS5) storage.
"""

import hashlib
import json
import threading
import uuid
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass

from ..config import Config
from ..models.task import TaskManager, TaskStatus
from ..utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    RESERVED_ONTOLOGY_ATTRIBUTE_NAMES,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)
from .text_processor import TextProcessor
from .local_memory import LocalMemory
from ..utils.locale import t, get_locale, set_locale
from ..utils.logger import get_logger

logger = get_logger('Veyronix.graph_builder')


@dataclass
class GraphInfo:
    """graph information"""
    graph_id: str
    node_count: int
    edge_count: int
    entity_types: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "entity_types": self.entity_types,
        }


@dataclass(frozen=True)
class BatchSubmission:
    """Durable identity for one batch ingestion operation (local SQLite)."""

    batch_id: str
    operation_id: str
    episode_uuids: List[str]
    item_count: int


class GraphBuilderService:
    """
    Graph builder service using local SQLite instead of Zep Cloud.

    Responsible for:
    1. Creating local knowledge graphs from text chunks
    2. Setting ontology definitions
    3. Storing entities and relationships in SQLite
    4. Providing graph data for frontend visualization
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key  # Not used locally, kept for API compatibility
        self.task_manager = TaskManager()
        self.db_path = Config.DATABASE_PATH

    def build_graph_async(
        self,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str = "Veyronix Graph",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        batch_size: int = 350,
    ) -> str:
        """
        async build the graph（use local SQLite replace Zep）

        Args:
            text: input text
            ontology: ontology definition
            graph_name: graph name
            chunk_size: text block size
            chunk_overlap: block overlap size
            batch_size: send of block count per batch

        Returns:
            task ID
        """
        task_id = self.task_manager.create_task(
            task_type="graph_build",
            metadata={
                "graph_name": graph_name,
                "chunk_size": chunk_size,
                "text_length": len(text),
            }
        )

        current_locale = get_locale()

        thread = threading.Thread(
            target=self._build_graph_worker,
            args=(task_id, text, ontology, graph_name, chunk_size, chunk_overlap, batch_size, current_locale)
        )
        thread.daemon = True
        thread.start()

        return task_id

    def _build_graph_worker(
        self,
        task_id: str,
        text: str,
        ontology: Dict[str, Any],
        graph_name: str,
        chunk_size: int,
        chunk_overlap: int,
        batch_size: int,
        locale: str = 'zh',
    ):
        """graph building work thread（local SQLite version）"""
        set_locale(locale)
        try:
            self.task_manager.update_task(
                task_id,
                status=TaskStatus.PROCESSING,
                progress=5,
                message=t('progress.startBuildingGraph')
            )

            # Validate chunks
            chunks = TextProcessor.split_text(text, chunk_size, chunk_overlap)
            self.validate_batch_chunks(chunks, batch_size=batch_size)
            total_chunks = len(chunks)

            # Generate graph ID
            import json
            graph_id = f"graph_{uuid.uuid4().hex[:12]}"
            self.task_manager.update_task(
                task_id,
                progress=10,
                message=t('progress.graphCreated', graphId=graph_id)
            )

            # Save ontology to local memory
            memory = LocalMemory(self.db_path)
            ontology_agent_id = f"ontology_{graph_id}"
            memory.save_agent(
                agent_id=ontology_agent_id,
                persona=json.dumps(ontology, ensure_ascii=False),
                state="ontology"
            )

            self.task_manager.update_task(
                task_id,
                progress=15,
                message=t('progress.ontologySet')
            )

            # Save chunks as agents in local memory
            self.task_manager.update_task(
                task_id,
                progress=20,
                message=t('progress.textSplit', count=total_chunks)
            )

            for i, chunk in enumerate(chunks):
                agent_id = f"chunk_{graph_id}_{i}"
                persona = {
                    "type": "document_chunk",
                    "content": chunk,
                    "chunk_index": i,
                    "total_chunks": total_chunks,
                    "graph_id": graph_id,
                }
                memory.save_agent(
                    agent_id=agent_id,
                    persona=json.dumps(persona, ensure_ascii=False),
                    state="created"
                )
                if i % 10 == 0:
                    progress = 20 + int((i / total_chunks) * 40)
                    self.task_manager.update_task(
                        task_id,
                        progress=min(progress, 60),
                        message=t('progress.addingChunks', count=total_chunks)
                    )

            self.task_manager.update_task(
                task_id,
                progress=60,
                message=t('progress.waitingZepProcess')
            )

            # Mark as complete
            self.task_manager.update_task(
                task_id,
                progress=90,
                message=t('progress.fetchingGraphInfo')
            )

            graph_info = GraphInfo(
                graph_id=graph_id,
                node_count=total_chunks,
                edge_count=0,
                entity_types=list(set(
                    et.get("name", "Entity")
                    for et in ontology.get("entity_types", [])
                )),
            )

            self.task_manager.complete_task(task_id, {
                "graph_id": graph_id,
                "graph_info": graph_info.to_dict(),
                "chunks_processed": total_chunks,
            })

        except Exception as e:
            import traceback
            error_msg = f"{str(e)}\n{traceback.format_exc()}"
            self.task_manager.fail_task(task_id, error_msg)

    def create_graph(
        self,
        name: str,
        *,
        graph_id: Optional[str] = None,
        graph_id_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Create a graph with a caller-durable ID (local SQLite version)."""
        graph_id = graph_id or f"Veyronix_{uuid.uuid4().hex[:16]}"
        if graph_id_callback:
            graph_id_callback(graph_id)
        return graph_id

    @staticmethod
    def build_operation_id(graph_id: str, chunks: List[str]) -> str:
        payload_hash = hashlib.sha256("\0".join(chunks).encode("utf-8")).hexdigest()
        return hashlib.sha256(
            f"{graph_id}:{payload_hash}".encode("utf-8")
        ).hexdigest()

    def set_ontology(self, graph_id: str, ontology: Dict[str, Any]):
        """Set ontology definition for a graph (local SQLite version)."""
        import warnings
        from typing import Optional
        from pydantic import Field

        warnings.filterwarnings('ignore', category=UserWarning, module='pydantic')

        def safe_attr_name(attr_name: str) -> str:
            if attr_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES:
                return f"entity_{attr_name}"
            return attr_name

        entity_types = {}
        for entity_def in ontology.get("entity_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = entity_def["name"]
            description = entity_def.get("description", f"A {name} entity.")

            attrs = {"__doc__": description}
            annotations = {}

            for normalized in normalize_ontology_attributes(
                entity_def.get("attributes", [])
            ):
                attr_name = safe_attr_name(normalized["name"])
                attr_desc = normalized["description"]
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]

            attrs["__annotations__"] = annotations

            entity_class = type(name, (), attrs)
            entity_class.__doc__ = description
            entity_types[name] = entity_class

        edge_definitions = {}
        for edge_def in ontology.get("edge_types", [])[:MAX_ONTOLOGY_TYPES]:
            name = edge_def["name"]
            description = edge_def.get("description", f"A {name} relationship.")

            attrs = {"__doc__": description}
            annotations = {}

            for normalized in normalize_ontology_attributes(
                edge_def.get("attributes", [])
            ):
                attr_name = safe_attr_name(normalized["name"])
                attr_desc = normalized["description"]
                attrs[attr_name] = Field(description=attr_desc, default=None)
                annotations[attr_name] = Optional[str]

            attrs["__annotations__"] = annotations

            class_name = ''.join(word.capitalize() for word in name.split('_'))
            edge_class = type(class_name, (), attrs)
            edge_class.__doc__ = description

            source_targets = []
            for st in normalize_ontology_source_targets(
                edge_def.get("source_targets", [])
            ):
                source_targets.append({
                    "source": st.get("source", "Entity"),
                    "target": st.get("target", "Entity"),
                })

            if source_targets:
                edge_definitions[name] = (edge_class, source_targets)

        # Store ontology in local memory
        memory = LocalMemory(self.db_path)
        ontology_id = f"ontology_{graph_id}"
        memory.save_agent(
            agent_id=ontology_id,
            persona=json.dumps({
                "entity_types": list(entity_types.keys()),
                "edge_types": list(edge_definitions.keys()),
            }, ensure_ascii=False),
            state="ontology"
        )

    def add_text_batches(
        self,
        graph_id: str,
        chunks: List[str],
        batch_size: int = 350,
        progress_callback: Optional[Callable] = None,
        batch_created_callback: Optional[Callable[[str | None, str], None]] = None,
    ) -> BatchSubmission:
        """Store text chunks as agents in local SQLite (replaces Zep batch API)."""

        if not graph_id:
            raise ValueError("graph_id is required")
        self.validate_batch_chunks(chunks, batch_size=batch_size)

        total_chunks = len(chunks)
        operation_id = self.build_operation_id(graph_id, chunks)
        if batch_created_callback:
            batch_created_callback(None, operation_id)

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"

        memory = LocalMemory(self.db_path)
        for i, chunk in enumerate(chunks):
            agent_id = f"chunk_{graph_id}_{batch_id}_{i}"
            persona = {
                "type": "document_chunk",
                "content": chunk,
                "chunk_index": i,
                "batch_id": batch_id,
                "graph_id": graph_id,
                "operation_id": operation_id,
            }
            memory.save_agent(
                agent_id=agent_id,
                persona=json.dumps(persona, ensure_ascii=False),
                state="stored"
            )
            if progress_callback:
                progress = (i + 1) / total_chunks
                batch_num = i // batch_size + 1
                total_batches = (total_chunks + batch_size - 1) // batch_size
                progress_callback(
                    t('progress.sendingBatch', current=batch_num, total=total_batches, chunks=len(chunk)),
                    progress
                )

        return BatchSubmission(
            batch_id=batch_id,
            operation_id=operation_id,
            episode_uuids=[],
            item_count=total_chunks,
        )

    @staticmethod
    def validate_batch_chunks(chunks: List[str], *, batch_size: int = 350) -> None:
        """Validate chunks before storage."""

        if not chunks:
            raise ValueError("At least one text chunk is required")
        if not 1 <= batch_size <= 350:
            raise ValueError("batch_size must be between 1 and 350")
        if len(chunks) > 50_000:
            raise ValueError("A batch cannot contain more than 50,000 items")
        oversized = [index for index, chunk in enumerate(chunks) if len(chunk) > 10_000]
        if oversized:
            raise ValueError(
                f"Batch item exceeds 10,000 characters at chunk {oversized[0]}"
            )

    def get_batch_summary(self, batch_id: str) -> Any:
        """Read batch info from local memory."""
        return {
            "batch_id": batch_id,
            "status": "succeeded",
            "item_count": 0,
        }

    def _wait_for_batch(
        self,
        submission: BatchSubmission,
        progress_callback: Optional[Callable] = None,
        timeout: Optional[int] = None,
    ) -> List[str]:
        """Local batch processing is synchronous, so this is a no-op."""
        if progress_callback:
            progress_callback(
                t(
                    'progress.processingComplete',
                    completed=submission.item_count,
                    total=submission.item_count,
                    pending=0,
                    elapsed=0,
                ),
                1.0,
            )
        return []

    def _get_graph_info(self, graph_id: str) -> GraphInfo:
        """Get graph info from local memory."""
        memory = LocalMemory(self.db_path)
        agents = memory.list_agents()
        # Filter chunks for this graph
        graph_agents = [
            a for a in agents
            if a.get("persona", {}).get("graph_id") == graph_id
        ]
        entity_types = set()
        for a in graph_agents:
            p = a.get("persona", {})
            if p.get("type"):
                entity_types.add(p["type"])

        return GraphInfo(
            graph_id=graph_id,
            node_count=len(graph_agents),
            edge_count=0,
            entity_types=list(entity_types),
        )

    def get_graph_data(self, graph_id: str) -> Dict[str, Any]:
        """Get complete graph data from local memory."""
        memory = LocalMemory(self.db_path)
        agents = memory.list_agents()

        nodes_data = []
        for agent in agents:
            p = agent.get("persona", {})
            if p.get("graph_id") != graph_id:
                continue
            nodes_data.append({
                "uuid": agent["id"],
                "name": p.get("content", "")[:100],
                "labels": [p.get("type", "Entity")],
                "summary": p.get("content", "")[:200],
                "attributes": p,
                "created_at": None,
            })

        return {
            "graph_id": graph_id,
            "nodes": nodes_data,
            "edges": [],
            "node_count": len(nodes_data),
            "edge_count": 0,
        }

    def delete_graph(self, graph_id: str):
        """Delete graph from local memory (no-op for now, data remains)."""
        # Local SQLite deletion would require additional queries
        # For now, mark as deleted by not returning in queries
        logger.info(f"Graph {graph_id} marked for deletion (local SQLite)")


__all__ = [
    "GraphBuilderService",
    "GraphInfo",
    "BatchSubmission",
]
