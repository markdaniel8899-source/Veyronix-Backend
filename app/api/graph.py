"""
Graph API routes.
Adopts project context mechanism, server persists state.
"""

import os
import re
import json
import traceback
import threading
from contextlib import ExitStack, nullcontext
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.local_memory import LocalMemory
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus
from ..services.simulation_manager import SimulationManager
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.llm_client import LLMResponseError

# get logger
logger = get_logger('Veyronix.api')
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


class GraphInUseError(RuntimeError):
    pass


def _active_graph_consumers(graph_id: str) -> list[str]:
    """Check for active consumers of a graph (simulations/reports)."""
    active = []
    active_runner_statuses = {
        RunnerStatus.STARTING,
        RunnerStatus.RUNNING,
        RunnerStatus.PAUSED,
        RunnerStatus.STOPPING,
    }
    for simulation in SimulationManager().list_simulations():
        if simulation.graph_id != graph_id:
            continue
        run_state = SimulationRunner.get_run_state(simulation.simulation_id)
        if run_state and run_state.runner_status in active_runner_statuses:
            active.append(simulation.simulation_id)
    return sorted(active)


def _delete_cloud_graph_if_present(graph_id: str | None) -> None:
    """Delete a graph from local SQLite after checking active consumers."""
    if not graph_id:
        return
    active_simulations = _active_graph_consumers(graph_id)
    if active_simulations:
        raise GraphInUseError(
            f"Graph {graph_id} is in use by active consumer(s): "
            f"{', '.join(active_simulations)}"
        )
    memory = LocalMemory(Config.DATABASE_PATH)
    memory.delete_agents_by_graph(graph_id)
    memory.delete_edges_by_graph(graph_id)


def _clear_project_graph_reference(project) -> None:
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None


def _project_build_lock(project_id: str) -> threading.Lock:
    with _build_locks_guard:
        return _build_locks.setdefault(project_id, threading.Lock())


def _project_has_active_build(project) -> bool:
    if project.status != ProjectStatus.GRAPH_BUILDING:
        return False
    if not project.graph_build_task_id:
        return False
    task = TaskManager().get_task(project.graph_build_task_id)
    return bool(
        task
        and task.status in {TaskStatus.PENDING, TaskStatus.PROCESSING}
    )


def allowed_file(filename: str) -> bool:
    """check file extension is allowed"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== project management API ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    get project details
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    column out all project
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    with _project_build_lock(project_id):
        return _delete_project_impl(project_id)


def _delete_project_impl(project_id: str):
    """
    delete project
    """
    project = ProjectManager.get_project(project_id)
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404
    if _project_has_active_build(project):
        return jsonify({
            "success": False,
            "error": t('api.graphBuilding')
        }), 409

    graph_id = project.graph_id
    graph_guard = nullcontext()
    with graph_guard:
        try:
            _delete_cloud_graph_if_present(graph_id)
        except GraphInUseError as error:
            return jsonify({"success": False, "error": str(error)}), 409
        # The local reference remains protected until it is removed, so a new
        # simulation cannot claim the just-deleted graph in between.
        success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": t('api.projectDeleteFailed', id=project_id)
        }), 404

    return jsonify({
        "success": True,
        "message": t('api.projectDeleted', id=project_id)
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    with _project_build_lock(project_id):
        return _reset_project_impl(project_id)


def _reset_project_impl(project_id: str):
    """
    reset project status（Used to re-build the graph）
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": t('api.projectNotFound', id=project_id)
        }), 404

    if _project_has_active_build(project):
        return jsonify({
            "success": False,
            "error": t('api.graphBuilding')
        }), 409

    graph_id = project.graph_id
    graph_guard = nullcontext()
    with graph_guard:
        try:
            _delete_cloud_graph_if_present(graph_id)
        except GraphInUseError as error:
            return jsonify({"success": False, "error": str(error)}), 409

        # reset to ontology generated status
        if project.ontology:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
        else:
            project.status = ProjectStatus.CREATED

        _clear_project_graph_reference(project)
        ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": t('api.projectReset', id=project_id),
        "data": project.to_dict()
    })


# ============== API 1：upload file and generate ontology ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    API 1：upload file，analyze generate ontology definition
    
    request method：multipart/form-data
    
    parameter：
        files: upload of file（PDF/MD/TXT），Can be much
        simulation_requirement: simulation requirement description（required）
        project_name: project name（optional）
        additional_context: additional description（optional）
        
    return：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "ontology": {
                    "entity_types": [...],
                    "edge_types": [...],
                    "analysis_summary": "..."
                },
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    project = None
    try:
        logger.info("=== start generate ontology definition ===")
        
        # get parameter
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        logger.debug(f"project name: {project_name}")
        logger.debug(f"simulation requirement: {simulation_requirement[:100]}...")
        
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationRequirement')
            }), 400
        
        # get upload of file
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": t('api.requireFileUpload')
            }), 400
        
        # create project
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        logger.info(f"create project: {project.project_id}")
        
        # save file and extract text
        document_texts = []
        all_text = ""
        
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                # save file to project directory
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                
                # Extract text
                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": t('api.noDocProcessed')
            }), 400
        
        # save extract of text
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"text extraction completed，common {len(all_text)} characters")
        
        # generate ontology
        logger.info("call LLM generate ontology definition...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        # save ontology to project
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"ontology generated completed: {entity_count} entity type, {edge_count} relationship type")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", []),
            "key_entities": ontology.get("key_entities", []),
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== ontology generated completed === project ID: {project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as error:
        provider_status = getattr(error, "status_code", None)
        request_id = getattr(error, "request_id", None)

        if isinstance(error, LLMResponseError):
            public_error = str(error)
            response_status = 502
            logger.exception("LLM returned an unusable ontology response")
        elif isinstance(provider_status, int):
            public_error = f"LLM provider request failed (HTTP {provider_status})"
            if request_id:
                safe_request_id = re.sub(
                    r"[^a-zA-Z0-9._:-]", "", str(request_id)
                )[:128]
                if safe_request_id:
                    public_error += f" (request_id: {safe_request_id})"
            response_status = 502
            # Provider exception bodies may echo request content. Keep the
            # server log useful without serializing the exception body.
            logger.error(
                "Ontology provider request failed: type=%s status=%s request_id=%s",
                type(error).__name__,
                provider_status,
                request_id or "unknown",
            )
        else:
            public_error = "Ontology generation failed; check the server logs"
            response_status = 500
            logger.exception("Unexpected ontology generation failure")

        response_data = None
        if project is not None:
            project.status = ProjectStatus.FAILED
            project.error = public_error
            try:
                ProjectManager.save_project(project)
            except Exception:
                logger.exception(
                    "Failed to persist ontology failure for project %s",
                    project.project_id,
                )
            response_data = {"project_id": project.project_id}

        payload = {
            "success": False,
            "error": public_error,
        }
        if response_data is not None:
            payload["data"] = response_data
        return jsonify(payload), response_status


# ============== API 2：build the graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """Serialize build claims for the same project within this process."""

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id")
    if not project_id:
        return _build_graph_impl()
    with _project_build_lock(project_id):
        return _build_graph_impl()


def _build_graph_impl():
    """
    API 2：according to project_id build the graph
    
    request（JSON）：
        {
            "project_id": "proj_xxxx",  // required，from API 1
            "graph_name": "graph name",    // optional
            "chunk_size": 4000,         // optional，default 4000
            "chunk_overlap": 200        // optional，default 200
        }
        
    return：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "Graph build task started"
            }
        }
    """
    try:
        logger.info("=== start build the graph ===")
        
        # check configuration
        errors = []
        if errors:
            logger.error(f"configuration error: {errors}")
            return jsonify({
                "success": False,
                "error": t('api.configError', details="; ".join(errors))
            }), 500
        
        # parse request
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"request parameter: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        # get project
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404

        # check project status
        force = data.get('force', False)  # Force new build
        if not isinstance(force, bool):
            return jsonify({
                "success": False,
                "error": "force must be a JSON boolean"
            }), 400
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotGenerated')
            }), 400
        
        resume_existing_batch = False
        if project.status == ProjectStatus.GRAPH_BUILDING:
            if _project_has_active_build(project):
                return jsonify({
                    "success": True,
                    "data": {
                        "project_id": project_id,
                        "task_id": project.graph_build_task_id,
                        "graph_id": project.graph_id,
                        "reused": True,
                        "message": t('api.graphBuilding')
                    }
                })

            if (
                not force
                and project.graph_id
            ):
                # Check if graph already exists in local memory
                memory = LocalMemory(Config.DATABASE_PATH)
                agents = memory.list_agents()
                if agents:
                    resume_existing_batch = True

            if not resume_existing_batch:
                project.status = ProjectStatus.FAILED
                project.error = (
                    "Graph build task is no longer present; the persisted graph "
                    "cannot be resumed automatically"
                )
                ProjectManager.save_project(project)
                if not force:
                    return jsonify({
                        "success": False,
                        "error": project.error,
                        "task_id": project.graph_build_task_id,
                        "recoverable": True,
                    }), 409

        if project.status == ProjectStatus.GRAPH_COMPLETED and not force:
            return jsonify({
                "success": True,
                "data": {
                    "project_id": project_id,
                    "task_id": project.graph_build_task_id,
                    "graph_id": project.graph_id,
                    "reused": True,
                    "message": t('progress.graphBuildComplete')
                }
            })
        
        # get configuration
        graph_name = data.get('graph_name', project.name or 'Veyronix Graph')

        default_chunk_size = Config.DEFAULT_CHUNK_SIZE
        default_chunk_overlap = Config.DEFAULT_CHUNK_OVERLAP

        chunk_size = data.get('chunk_size')
        if chunk_size is None:
            chunk_size = (
                project.chunk_size
                if project.chunk_size and project.chunk_size >= 2000
                else default_chunk_size
            )

        chunk_overlap = data.get('chunk_overlap')
        if chunk_overlap is None:
            chunk_overlap = (
                project.chunk_overlap
                if project.chunk_overlap and project.chunk_overlap >= 100
                else default_chunk_overlap
            )
        if not isinstance(chunk_size, int) or chunk_size <= 0:
            return jsonify({"success": False, "error": "chunk_size must be a positive integer"}), 400
        if (
            not isinstance(chunk_overlap, int)
            or chunk_overlap < 0
            or chunk_overlap >= chunk_size
        ):
            return jsonify({
                "success": False,
                "error": "chunk_overlap must satisfy 0 <= chunk_overlap < chunk_size"
            }), 400
        
        # update project configuration
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # get extract of text
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": t('api.textNotFound')
            }), 400
        
        # get ontology
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": t('api.ontologyNotFound')
            }), 400

        # Only mutate Cloud state after the complete rebuild request validates.
        if project.status == ProjectStatus.FAILED or (
            force and project.status == ProjectStatus.GRAPH_COMPLETED
        ):
            graph_id_to_delete = project.graph_id
            graph_guard = nullcontext()
            with graph_guard:
                _delete_cloud_graph_if_present(graph_id_to_delete)
                project.status = ProjectStatus.ONTOLOGY_GENERATED
                _clear_project_graph_reference(project)
                ProjectManager.save_project(project)
        
        # create async task
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"build the graph: {graph_name}")
        logger.info(f"create graph building task: task_id={task_id}, project_id={project_id}")
        
        # update project status
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Start background task
        def build_task():
            set_locale(current_locale)
            build_logger = get_logger('Veyronix.build')
            try:
                build_logger.info(f"[{task_id}] start build the graph...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message=t('progress.initGraphService')
                )
                
                # chunk
                task_manager.update_task(
                    task_id,
                    message=t('progress.textChunking'),
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                
                if resume_existing_batch:
                    graph_id = project.graph_id
                    memory = LocalMemory(Config.DATABASE_PATH)
                    existing_agents = memory.list_agents(graph_id)
                    if not existing_agents:
                        # Legacy graphs were stored without a graph_id.
                        existing_agents = memory.list_agents()
                    if not existing_agents:
                        raise RuntimeError(
                            "Persisted graph does not match the current graph input"
                        )
                    task_manager.update_task(
                        task_id,
                        message=t('progress.waitingZepProcess'),
                        progress=55,
                    )
                else:
                    # create graph（use LocalMemory replace Zep）
                    task_manager.update_task(
                        task_id,
                        message=t('progress.creatingZepGraph'),
                        progress=10
                    )
                    
                    def remember_graph(graph_id_val):
                        project.graph_id = graph_id_val
                        ProjectManager.save_project(project)
                    
                    # Generate a unique graph ID
                    import uuid
                    graph_id = f"graph_{uuid.uuid4().hex[:12]}"
                    remember_graph(graph_id)
                    
                    # settings ontology
                    task_manager.update_task(
                        task_id,
                        message=t('progress.settingOntology'),
                        progress=15
                    )
                    
                    memory = LocalMemory(Config.DATABASE_PATH)
                    
                    def add_progress_callback(msg, progress_ratio):
                        progress = 15 + int(progress_ratio * 35)  # 15% - 50%
                        task_manager.update_task(
                            task_id,
                            message=msg,
                            progress=min(progress, 50)
                        )
                    
                    task_manager.update_task(
                        task_id,
                        message=t('progress.addingChunks', count=total_chunks),
                        progress=15
                    )
                    
                    # Extract real entities and relationships instead of storing
                    # raw document chunks as agents.
                    from ..services.entity_extractor import EntityExtractor
                    extractor = EntityExtractor()
                    entities, edges, total_chunks = extractor.extract_graph(
                        text=text,
                        ontology=ontology,
                        graph_id=graph_id,
                        chunk_size=chunk_size,
                        chunk_overlap=chunk_overlap,
                        progress_callback=add_progress_callback,
                    )
                    
                    if entities:
                        memory.save_agents([
                            {
                                "id": entity["uuid"],
                                "persona": entity,
                                "state": "created",
                            }
                            for entity in entities
                        ])
                        memory.save_edges(edges)
                        build_logger.info(
                            f"[{task_id}] Extracted {len(entities)} entities, "
                            f"{len(edges)} edges from {total_chunks} chunks"
                        )
                    else:
                        # Bounded fallback: keep representative chunks when the
                        # LLM and heuristic extractor find no named entities.
                        selected = chunks
                        if len(selected) > 200:
                            step = (len(selected) - 1) / 199
                            selected = [selected[int(round(i * step))] for i in range(200)]
                        chunk_records = []
                        for i, chunk in enumerate(selected):
                            chunk_id = f"chunk_{graph_id}_{i}"
                            chunk_records.append({
                                "id": chunk_id,
                                "persona": {
                                    "uuid": chunk_id,
                                    "name": f"Document chunk {i + 1}",
                                    "type": "DocumentChunk",
                                    "labels": ["DocumentChunk"],
                                    "summary": chunk[:300],
                                    "attributes": {"content": chunk[:1000]},
                                    "graph_id": graph_id,
                                    "related_edges": [],
                                    "related_nodes": [],
                                },
                                "state": "created",
                            })
                        memory.save_agents(chunk_records)
                        build_logger.warning(
                            f"[{task_id}] Entity extraction returned nothing; "
                            f"stored {len(chunk_records)} document chunks"
                        )
                    
                    task_manager.update_task(
                        task_id,
                        message=t('progress.waitingZepProcess'),
                        progress=55
                    )
                
                # get graph data（statistics only，Do not load the entire images into memory）
                task_manager.update_task(
                    task_id,
                    message=t('progress.fetchingGraphData'),
                    progress=95
                )
                memory = LocalMemory(Config.DATABASE_PATH)
                node_count = memory.count_agents(graph_id)
                edge_count = memory.count_edges(graph_id)
                build_logger.info(f"[{task_id}] graph building completed: graph_id={graph_id}, node={node_count}, edge={edge_count}")

                # Publish local project/task terminal state
                with _project_build_lock(project_id):
                    project.status = ProjectStatus.GRAPH_COMPLETED
                    project.error = None
                    ProjectManager.save_project(project)
                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.COMPLETED,
                        message=t('progress.graphBuildComplete'),
                        progress=100,
                        result={
                            "project_id": project_id,
                            "graph_id": graph_id,
                            "node_count": node_count,
                            "edge_count": edge_count,
                            "chunk_count": total_chunks,
                        }
                    )
                
            except Exception as e:
                # update project status for failed
                build_logger.error(f"[{task_id}] graph building failed: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                with _project_build_lock(project_id):
                    project.status = ProjectStatus.FAILED
                    project.error = str(e)
                    ProjectManager.save_project(project)

                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.FAILED,
                        message=t('progress.buildFailed', error=str(e)),
                        error=traceback.format_exc()
                    )
        
        # Start background thread
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "resumed": resume_existing_batch,
                "message": t('api.graphBuildStarted', taskId=task_id)
            }
        })
        
    except GraphInUseError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== task query API ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    query task status
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": t('api.taskNotFound', id=task_id)
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    column out all tasks
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": tasks,
        "count": len(tasks)
    })


# ============== graph data API ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    get graph data（node and edge），Only return the specified graph of data。
    """
    try:
        memory = LocalMemory(Config.DATABASE_PATH)
        records = memory.list_agents(graph_id)
        if not records:
            # Legacy graphs were stored without graph_id; fall back to all
            # agents so existing projects remain viewable.
            records = memory.list_agents()

        nodes = []
        for record in records:
            persona = record.get("persona") or {}
            if not isinstance(persona, dict):
                persona = {"content": str(persona)}
            labels = persona.get("labels")
            if not isinstance(labels, list) or not labels:
                entity_type = (
                    persona.get("type")
                    or persona.get("entity_type")
                    or "Entity"
                )
                labels = [str(entity_type)]
            attributes = persona.get("attributes")
            if not isinstance(attributes, dict):
                attributes = {}
            nodes.append({
                "uuid": record.get("id"),
                "name": (
                    persona.get("name")
                    or persona.get("title")
                    or record.get("id")
                ),
                "labels": labels,
                "summary": (
                    persona.get("summary")
                    or persona.get("description")
                    or ""
                ),
                "attributes": attributes,
                "state": record.get("state"),
                "created_at": persona.get("created_at"),
            })

        edges = memory.list_edges(graph_id)
        if not edges:
            # Derive edges from embedded relationship data for legacy records.
            seen = set()
            for record in records:
                persona = record.get("persona") or {}
                for edge in persona.get("related_edges") or []:
                    if not isinstance(edge, dict):
                        continue
                    edge_id = edge.get("uuid") or edge.get("id")
                    if not edge_id or edge_id in seen:
                        continue
                    if edge.get("direction") == "incoming":
                        continue
                    seen.add(edge_id)
                    edges.append(edge)

        return jsonify({
            "success": True,
            "data": {
                "graph_id": graph_id,
                "nodes": nodes,
                "edges": edges,
                "node_count": len(nodes),
                "edge_count": len(edges),
            }
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    delete graph
    """
    try:
        projects = ProjectManager.find_projects_by_graph_id(graph_id)
        if not projects:
            return jsonify({
                "success": False,
                "error": "No local project references this graph"
            }), 404
        project_ids = sorted({project.project_id for project in projects})
        with ExitStack() as stack:
            for project_id in project_ids:
                stack.enter_context(_project_build_lock(project_id))

            # Re-read under all owning project locks so a concurrent build
            # claim cannot appear between validation and deletion.
            projects = ProjectManager.find_projects_by_graph_id(graph_id)
            if any(_project_has_active_build(project) for project in projects):
                return jsonify({
                    "success": False,
                    "error": t('api.graphBuilding')
                }), 409

            _delete_cloud_graph_if_present(graph_id)

            for project in projects:
                _clear_project_graph_reference(project)
                project.status = (
                    ProjectStatus.ONTOLOGY_GENERATED
                    if project.ontology
                    else ProjectStatus.CREATED
                )
                ProjectManager.save_project(project)
        
        return jsonify({
            "success": True,
            "message": t('api.graphDeleted', id=graph_id)
        })
        
    except GraphInUseError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
