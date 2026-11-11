"""
Simulation API routes.
Step2: Entity reading, OASIS simulation preparation and execution (fully automated).
"""

import os
import traceback
from contextlib import nullcontext
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.local_memory import LocalMemory
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import (
    SimulationRunner,
    RunnerStatus,
    SimulationStopPending,
)
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale
from ..models.project import ProjectManager

logger = get_logger('Veyronix.api.simulation')


def _get_default_platform(simulation_id: str) -> str:
    """
    According to simulation configuration return default platform

    read SimulationState in of enable_twitter / enable_reddit settings，
    return the simulation actual use of platform，rather than hard encoding 'reddit'。

    Args:
        simulation_id: simulation ID

    Returns:
        'twitter' or 'reddit'
    """
    try:
        manager = SimulationManager()
        state = manager._load_simulation_state(simulation_id)
        if state:
            return state.get_default_platform()
    except Exception:
        pass
    return "reddit"


# Interview prompt Optimize prefix
# add this prefix can avoid Agent Call tool，Reply directly with text
INTERVIEW_PROMPT_PREFIX = "combine you of persona、all of past memory and line movements，Reply me directly with text without calling any tools："


def optimize_interview_prompt(prompt: str) -> str:
    """
    optimization Interview Ask a question，add prefix avoidance Agent Call tool
    
    Args:
        prompt: Original question
        
    Returns:
        After optimization of Ask a question
    """
    if not prompt:
        return prompt
    # Avoid repeating add prefix
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== entity read API ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    Get all entities from the local SQLite graph (filtered).
    
    Query parameters:
        entity_types: Comma-separated entity types (optional)
        enrich: Include edge info (default: true)
    """
    try:
        memory = LocalMemory(Config.DATABASE_PATH)
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        
        logger.info(f"Getting graph entities: graph_id={graph_id}, entity_types={entity_types}")
        
        # Get graph-scoped agents from local memory (replaces Zep entity reader)
        agents = memory.list_agents(graph_id)
        if not agents:
            # Legacy graphs lack graph_id in persona JSON.
            agents = memory.list_agents()
        
        # Filter by entity types if specified
        if entity_types:
            allowed = {item.strip().lower() for item in entity_types}
            agents = [
                a for a in agents
                if str((a.get('persona') or {}).get('type') or '').strip().lower()
                in allowed
            ]
        
        return jsonify({
            "success": True,
            "data": {
                "entities": agents,
                "count": len(agents)
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get graph entities: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """Get single entity details."""
    try:
        memory = LocalMemory(Config.DATABASE_PATH)
        entity = memory.get_agent(entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": t('api.entityNotFound', id=entity_uuid)
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity
        })
        
    except Exception as e:
        logger.error(f"Failed to get entity details: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """Get all entities of a specific type."""
    try:
        memory = LocalMemory(Config.DATABASE_PATH)
        agents = memory.list_agents(graph_id)
        if not agents:
            agents = memory.list_agents()
        
        expected = str(entity_type or '').strip().lower()
        entities = [
            a for a in agents
            if str((a.get('persona') or {}).get('type') or '').strip().lower() == expected
        ]
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": entities
            }
        })
        
    except Exception as e:
        logger.error(f"Failed to get entities: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== simulation management API ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    create new of simulation
    
    note：max_rounds etc. parameter by LLM automatically generate，none requires manual settings
    
    request（JSON）：
        {
            "project_id": "proj_xxxx",      // required
            "graph_id": "Veyronix_xxxx",    // optional，If not provided then from project get
            "enable_twitter": true,          // optional，default true
            "enable_reddit": true            // optional，default true
        }
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "Veyronix_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": t('api.requireProjectId')
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=project_id)
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.graphNotBuilt')
            }), 400
        
        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"create simulation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    check simulation is already ready completed
    
    check piece：
    1. state.json exist and status for "ready"
    2. Necessary file is stored in：reddit_profiles.json, twitter_profiles.csv, simulation_config.json
    
    note：Run line script(run_*.py)reserved in backend/scripts/ directory，No more copy to simulation directory
    
    Args:
        simulation_id: simulation ID
        
    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # check directory if it exists in
    if not os.path.exists(simulation_dir):
        return False, {"reason": "simulation directory does not exist in"}
    
    # Necessary file list（Does not include scripts，The script is located at backend/scripts/）
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # check file is stored in
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "Missing necessary files",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # check state.json in status
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # detailed log
        logger.debug(f"Detect simulation preparation status: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # if config_generated=True And file is stored in，acknowledge for preparation completed
        # The following status description preparation work has been completed：
        # - ready: Prepare completed，can transport line
        # - preparing: if config_generated=True description completed
        # - running: Positive in luck line，description ready already completed
        # - completed: run line completed，description ready already completed
        # - stopped: has stopped，description ready already completed
        # - failed: Run line failed（But preparation is completed of）
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # get file statistics information
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # if status is preparing But the file has been completed，automatically update status for ready
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"Automatically update simulation status: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"automatic update status failed: {e}")
            
            logger.info(f"simulation {simulation_id} Detection result: Prepared completed (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"simulation {simulation_id} Detection result: Not ready completed (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"status is not in prepared list or config_generated for false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"read status file failed: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    Prepare simulation environment（async task，LLM Smart generate all parameters）
    
    This is a time-consuming operation，API will return immediately task_id，
    use GET /api/simulation/prepare/status query progress
    
    characteristic：
    - Automatic detection completed of preparations，avoid duplication generate
    - if ready completed，Directly return the existing result
    - support force new generate（force_regenerate=true）
    
    step：
    1. check whether the preparation work has been completed of
    2. Read from local SQLite graph and filter entities
    3. for every entity generate OASIS Agent Profile（With retry mechanism）
    4. LLM Intelligent generate simulation configuration（With retry mechanism）
    5. save configuration file and default script
    
    request（JSON）：
        {
            "simulation_id": "sim_xxxx",                   // required，simulation ID
            "entity_types": ["Student", "PublicFigure"],  // optional，Specify entity type
            "use_llm_for_profiles": true,                 // optional，Whether to use LLM generate persona
            "parallel_profile_count": 5,                  // optional，and line generate persona count，default 5
            "force_regenerate": false                     // optional，Force new generate，default false
        }
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // return when new task
                "status": "preparing|ready",
                "message": "Preparation task started|preparation already complete",
                "already_prepared": true|false    // Is it ready? completed
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # check whether to force re-new generate
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"start processing /prepare request: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # check whether already ready completed（avoid duplication generate）
        if not force_regenerate:
            logger.debug(f"check simulation {simulation_id} Is it ready? completed...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"check result: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"simulation {simulation_id} Prepared completed，Skip duplicates generate")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"simulation {simulation_id} Not ready completed，The preparation task will be started")
        
        # from project get necessary information
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=state.project_id)
            }), 404
        
        # get simulation requirement
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.projectMissingRequirement')
            }), 400
        
        # get document text
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # ========== synchronous get entity count（in background task before starting） ==========
        # In this way, the front-end in calls prepare can get to expected immediately after Agent total count
        try:
            logger.info(f"synchronous get entity count: graph_id={state.graph_id}")
            memory = LocalMemory(Config.DATABASE_PATH)
            # fast read entity（only statistics count）
            agents = memory.list_agents()
            # save entity count to status（For the front end to get immediately）
            state.entities_count = len(agents)
            state.entity_types = list(set(a.get('persona', {}).get('type', 'unknown') for a in agents))
            logger.info(f"expected entity count: {state.entities_count}, type: {state.entity_types}")
        except Exception as e:
            logger.warning(f"synchronous get entity count failed（Retry in background task）: {e}")
            # failed does not affect subsequent processes，The background task will repeat new get
        
        # create async task
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # update simulation status（include pre-get of entity count）
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)
        
        # Capture locale before spawning background thread
        current_locale = get_locale()

        # Define background tasks
        def run_prepare():
            set_locale(current_locale)
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message=t('progress.startPreparingEnv')
                )
                
                # Prepare for simulation（With progress callback）
                # storage stage progress details
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # calculate total progress
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # build detailed progress information
                    stage_names = {
                        "reading": t('progress.readingGraphEntities'),
                        "generating_profiles": t('progress.generatingProfiles'),
                        "generating_config": t('progress.generatingSimConfig'),
                        "copying_scripts": t('progress.preparingScripts')
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # update stage details
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # build detailed progress information
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # build concise message
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )

                if result_state.status == SimulationStatus.FAILED:
                    task_manager.fail_task(
                        task_id,
                        result_state.error or "simulation preparation failed"
                    )
                else:
                    task_manager.complete_task(
                        task_id,
                        result=result_state.to_simple_dict()
                    )
                
            except Exception as e:
                logger.error(f"Prepare simulation failed: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # update simulation status for failed
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # Start background thread
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": t('api.prepareStarted'),
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # expected of Agent total count
                "entity_types": state.entity_types  # entity type list
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Start preparation task failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    query preparation task progress
    
    Support two query methods：
    1. pass task_id query is in line of task progress
    2. pass simulation_id check whether the preparation work has been completed of
    
    request（JSON）：
        {
            "task_id": "task_xxxx",          // optional，prepare return of task_id
            "simulation_id": "sim_xxxx"      // optional，simulation ID（Used to check completed of preparation）
        }
    
    return：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // Whether there has been completed of preparation
                "prepare_info": {...}            // Prepared completed time of detailed information
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # if provides simulation_id，First check whether it is ready completed
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": t('api.alreadyPrepared'),
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
        
        # if no task_id，return error
        if not task_id:
            if simulation_id:
                # have simulation_id but not ready completed
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": t('api.notStartedPrepare'),
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": t('api.requireTaskOrSimId')
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # task does not exist in，But if there is simulation_id，check whether it is ready completed
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": t('api.taskCompletedPrepared'),
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": t('api.taskNotFound', id=task_id)
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"query task status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """get simulation status"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        result = state.to_dict()
        
        # if simulation is ready，Additional run line description
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"get simulation status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    column out all simulation
    
    Query parameter：
        project_id: by project ID filter（optional）
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.error(f"column out simulation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
    get simulation corresponding to latest report_id
    
    Traverse reports directory，find out simulation_id match of report，
    if there are multiple then return latest of（by created_at sort）
    
    Args:
        simulation_id: simulation ID
        
    Returns:
        report_id or None
    """
    import json
    from datetime import datetime
    
    # reports directory path：backend/uploads/reports
    # __file__ yes app/api/simulation.py，need go up two levels to backend/
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None
    
    matching_reports = []
    
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue
        
        if not matching_reports:
            return None
        
        # by create time reverse sort，return latest of
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
        
    except Exception as e:
        logger.warning(f"Find simulation {simulation_id} of report failed: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
    get history simulation list（with project details）
    
    Used for home page history project display，return include project name、description etc. Rich information of simulation list
    
    Query parameter：
        limit: return count limit（default 20）
    
    return：
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "project_id": "proj_xxxx",
                    "project_name": "Wuhan University Public Opinion Analysis",
                    "simulation_requirement": "if released by Wuhan University...",
                    "status": "completed",
                    "entities_count": 68,
                    "profiles_count": 68,
                    "entity_types": ["Student", "Professor", ...],
                    "created_at": "2024-12-10",
                    "updated_at": "2024-12-10",
                    "total_rounds": 120,
                    "current_round": 120,
                    "report_id": "report_xxxx",
                    "version": "v1.0.2"
                },
                ...
            ],
            "count": 7
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        
        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]
        
        # Enhance simulation data，only from Simulation file read
        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()
            
            # get simulation configuration information（from simulation_config.json read simulation_requirement）
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # recommended rounds count（fallback value）
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 / 
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0
            
            # get run line status（from run_state.json read user settings of actual rounds count）
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # use user settings of total_rounds，If none then use recommended rounds count
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds
            
            # get association project of file list（most 3 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT）
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "unknown file")} 
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []
            
            # get association of report_id（Find the simulation latest of report）
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            
            # add version number
            sim_dict["version"] = "v1.0.2"
            
            # format date
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""
            
            enriched_simulations.append(sim_dict)
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"get history simulation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    get simulation of Agent Profile
    
    Query parameter：
        platform: platform type（reddit/twitter，default reddit）
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)

        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"get Profile failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    real time get simulation of Agent Profile（Used to view progress in real time during in generate process）
    
    and /profiles API of difference：
    - read file directly，not pass SimulationManager
    - Applies to the generate process of real-time view
    - return extra of metadata（Such as file modification time、Is it correct in generate etc.）
    
    Query parameter：
        platform: platform type（reddit/twitter，default reddit）
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // Expected total count（if there is）
                "is_generating": true,  // Is it correct in generate
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)

        # get simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # Determine file path
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # check file is stored in
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # get file modification time
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"read profiles file failed（possibly positive in writing）: {e}")
                profiles = []
        
        # check whether it is correct in generate（pass state.json judge）
        is_generating = False
        total_expected = None
        status = None
        error = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
                    error = state_data.get("error")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "status": status,
                "error": error,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.error(f"real time get Profile failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    real-time get simulation configuration（Used to view progress in real time during in generate process）
    
    and /config API of difference：
    - read file directly，not pass SimulationManager
    - Applies to the generate process of real-time view
    - return extra of metadata（Such as file modification time、Is it correct in generate etc.）
    - Partial information can be returned even if the configuration has not been generated yet.
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  // Is it correct in generate
                "generation_stage": "generating_config",  // current generate stage
                "config": {...}  // configuration content（if exist）
            }
        }
    """
    import json
    from datetime import datetime
    
    try:
        # get simulation directory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404
        
        # configuration file path
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # check file is stored in
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # get file modification time
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"read config file failed（possibly positive in writing）: {e}")
                config = None
        
        # check whether it is correct in generate（pass state.json judge）
        is_generating = False
        generation_stage = None
        status = None
        error = None
        profiles_generated = False
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    error = state_data.get("error")
                    is_generating = status == "preparing"
                    profiles_generated = state_data.get("profiles_generated", False)
                    config_generated = state_data.get("config_generated", False)
                    
                    # Determine the current stage
                    if is_generating:
                        if profiles_generated:
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
                    elif status == "failed":
                        generation_stage = "failed"
            except Exception:
                pass
        
        # build return data
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "status": status,
            "error": error,
            "generation_stage": generation_stage,
            "profiles_generated": profiles_generated,
            "config_generated": config_generated,
            "config": config
        }
        
        # if configuration is stored in，Extract some key statistical information
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.error(f"real time get Config failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    get simulation configuration（LLM Intelligent generate of complete configuration）
    
    return include：
        - time_config: time configuration（simulation duration、round、high peak/low valley period）
        - agent_configs: Every Agent of activity configuration（Activity、speaking frequency、stance etc.）
        - event_config: event configuration（initial post、hot topic）
        - platform_configs: platform configuration
        - generation_reasoning: LLM of configuration inference description
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": t('api.configNotFound')
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"get configuration failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """download simulation configuration file"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": t('api.configFileNotFound')
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.error(f"download configuration failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Profile generateAPI（independent use） ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    directly from graph generate OASIS Agent Profile（Do not create simulation）
    
    request（JSON）：
        {
            "graph_id": "Veyronix_xxxx",     // required
            "entity_types": ["Student"],      // optional
            "use_llm": true,                  // optional
            "platform": "reddit"              // optional
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.requireGraphId')
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        memory = LocalMemory(Config.DATABASE_PATH)
        agents = memory.list_agents()
        
        # Filter by entity types if specified
        if entity_types:
            agents = [a for a in agents if a.get('persona', {}).get('type') in entity_types]
        
        if len(agents) == 0:
            return jsonify({
                "success": False,
                "error": t('api.noMatchingEntities')
            }), 400
        
        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=agents,
            use_llm=use_llm
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.error(f"generate Profile failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== simulation run line control API ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    start run line simulation

    request（JSON）：
        {
            "simulation_id": "sim_xxxx",          // required，simulation ID
            "platform": "parallel",                // optional: twitter / reddit / parallel (default)
            "max_rounds": 100,                     // optional: maximum simulation rounds count，Used to truncate overly long simulations
            "enable_graph_memory_update": false,   // Optional: whether to update agent activity to local graph memory
            "force": false                         // optional: Forced restart new start（will stop the running line of simulation and clear the log）
        }

    about force parameter：
        - After enabling，if simulation is running in line or completed，Will first stop and clean up the running line log
        - Clean up of content include：run_state.json, actions.jsonl, simulation.log etc.
        - Configuration files will not be cleaned（simulation_config.json）and profile file
        - Suitable for need heavy new operation line simulation of scenarios

    about enable_graph_memory_update：
        - When enabled, all agent activity (posts, comments, likes, etc.) is updated to local graph memory in real time
        - This can make graph"remember"simulation process，for subsequent analysis or AI dialogue
        - need simulation association of project has valid of graph_id
        - Use batch update mechanism，reduce API call times count

    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // Whether to enable graph memory update
                "force_restarted": true               // Is it a forced restart new start?
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')  # optional：maximum simulation rounds count
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)  # optional：Whether to enable graph memory update
        force = data.get('force', False)  # optional：Forced restart new start
        if not isinstance(enable_graph_memory_update, bool):
            return jsonify({
                "success": False,
                "error": "enable_graph_memory_update must be a JSON boolean",
            }), 400
        if not isinstance(force, bool):
            return jsonify({
                "success": False,
                "error": "force must be a JSON boolean",
            }), 400

        # validate max_rounds parameter
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": t('api.maxRoundsPositive')
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": t('api.maxRoundsInvalid')
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": t('api.invalidPlatform', platform=platform)
            }), 400

        # check simulation is ready
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        force_restarted = False
        
        # Intelligent processing status：if preparation has been completed，Allow restart new
        if state.status != SimulationStatus.READY:
            # check whether the preparation work has been completed
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                run_state = SimulationRunner.get_run_state(simulation_id)
                # Graph memory updater replaced by local SQLite - no active updater to check
                updater = None
                needs_finalization = bool(
                    run_state
                    and run_state.runner_status in {
                        RunnerStatus.RUNNING,
                        RunnerStatus.PAUSED,
                        RunnerStatus.STOPPING,
                        RunnerStatus.FAILED,
                    }
                    and (
                        run_state.runner_status
                        in {
                            RunnerStatus.RUNNING,
                            RunnerStatus.PAUSED,
                            RunnerStatus.STOPPING,
                        }
                        or updater is not None
                    )
                )
                if needs_finalization:
                    if not force:
                        return jsonify({
                            "success": False,
                            "error": t('api.simRunningForceHint')
                        }), 400
                    logger.info(f"Forced mode：Complete old simulation first to terminate {simulation_id}")
                    try:
                        stopped = SimulationRunner.stop_simulation(simulation_id)
                    except SimulationStopPending as error:
                        return jsonify({
                            "success": False,
                            "pending": True,
                            "error": str(error),
                        }), 409
                    except Exception as error:
                        return jsonify({
                            "success": False,
                            "error": (
                                "Cannot restart until the previous simulation "
                                f"finalizes safely: {error}"
                            ),
                        }), 409
                    if stopped.runner_status != RunnerStatus.STOPPED:
                        return jsonify({
                            "success": False,
                            "error": "Previous simulation did not reach STOPPED",
                        }), 409

                # if is a forced mode，Clean up the run line log
                if force:
                    logger.info(f"Forced mode：Clean simulation log {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        return jsonify({
                            "success": False,
                            "error": (
                                "Failed to clean previous simulation logs: "
                                f"{cleanup_result.get('errors')}"
                            ),
                        }), 500
                    force_restarted = True

                # process does not exist in or has ended，reset status for ready
                logger.info(f"simulation {simulation_id} Preparation work completed，reset status for ready（original status: {state.status.value}）")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # Preparatory work has not been completed
                return jsonify({
                    "success": False,
                    "error": t('api.simNotReady', status=state.status.value)
                }), 400
        
        # get graph ID（for graph memory update）
        graph_id = None
        if enable_graph_memory_update:
            # The project is authoritative. A graph ID copied into an older
            # simulation can outlive a project reset/rebuild and must not be
            # used to resurrect writes to a deleted graph.
            project = ProjectManager.get_project(state.project_id)
            graph_id = project.graph_id if project else None
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": t('api.graphIdRequiredForMemory')
                }), 400

        graph_guard = (
            nullcontext()
            if enable_graph_memory_update
            else nullcontext()
        )
        with graph_guard:
            if enable_graph_memory_update:
                # Graph memory updates handled by local SQLite - no locking needed
                refreshed_state = manager.get_simulation(simulation_id)
                refreshed_project = (
                    ProjectManager.get_project(refreshed_state.project_id)
                    if refreshed_state
                    else None
                )
                current_graph_id = (
                    refreshed_project.graph_id if refreshed_project else None
                )
                if current_graph_id != graph_id:
                    return jsonify({
                        "success": False,
                        "error": (
                            "The project graph changed while the simulation "
                            "was starting; retry after refreshing the project"
                        ),
                    }), 409
                if (
                    refreshed_state.graph_id
                    and refreshed_state.graph_id != current_graph_id
                ):
                    return jsonify({
                        "success": False,
                        "error": (
                            "The simulation references an older graph; "
                            "prepare it again before enabling graph memory"
                        ),
                    }), 409
                # No active report readers with local SQLite
                active_reports = []
                if active_reports:
                    return jsonify({
                        "success": False,
                        "error": (
                            "A report is currently reading this graph; wait "
                            "for report generation to finish before enabling "
                            "graph memory updates"
                        ),
                        "active_reports": active_reports,
                    }), 409
                state = refreshed_state
                logger.info(
                    "Enable graph memory update: simulation_id=%s, graph_id=%s",
                    simulation_id,
                    graph_id,
                )

            # Start simulation。Enable graph writing while still holding graph_guard，straight to updater
            # claim and process resource all release completed。
            run_state = SimulationRunner.start_simulation(
                simulation_id=simulation_id,
                platform=platform,
                max_rounds=max_rounds,
                enable_graph_memory_update=enable_graph_memory_update,
                graph_id=graph_id
            )
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Start simulation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    stop simulation
    
    request（JSON）：
        {
            "simulation_id": "sim_xxxx"  // required，simulation ID
        }
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # update simulation status
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.STOPPED
            state.error = None
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })

    except SimulationStopPending as e:
        return jsonify({
            "success": False,
            "pending": True,
            "error": str(e),
        }), 202

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"stop simulation failed: {str(e)}")
        simulation_id = (request.get_json(silent=True) or {}).get('simulation_id')
        if simulation_id:
            manager = SimulationManager()
            state = manager.get_simulation(simulation_id)
            if state:
                state.status = SimulationStatus.FAILED
                state.error = str(e)
                manager._save_simulation_state(state)
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Market Impact API ==============

@simulation_bp.route('/<simulation_id>/market-shock', methods=['POST'])
def inject_market_shock(simulation_id: str):
    """
    Inject market shock events into simulation。

    request（JSON）：
        {
            "event": "The central bank suddenly announced an interest rate hike 200 base",  // required
            "round_num": 3                            // optional，from which rounds start takes effect
        }
    """
    try:
        data = request.get_json() or {}
        event = str(data.get('event') or data.get('shock') or '').strip()
        if not event:
            return jsonify({
                "success": False,
                "error": "event is required"
            }), 400
        if len(event) > 2000:
            return jsonify({
                "success": False,
                "error": "event must be 2000 characters or fewer"
            }), 400

        round_num = data.get('round_num')
        if round_num is not None:
            try:
                round_num = int(round_num)
            except (TypeError, ValueError):
                return jsonify({
                    "success": False,
                    "error": "round_num must be an integer"
                }), 400
            if round_num < 1:
                return jsonify({
                    "success": False,
                    "error": "round_num must be at least 1"
                }), 400

        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        memory = LocalMemory(Config.DATABASE_PATH)
        shock_id = memory.add_market_shock(
            simulation_id=simulation_id,
            event=event,
            round_num=round_num,
        )

        return jsonify({
            "success": True,
            "data": {
                "shock_id": shock_id,
                "simulation_id": simulation_id,
                "event": event,
                "round_num": round_num,
                "message": "Market shock queued for the next simulation round",
            }
        })
    except Exception as e:
        logger.error(f"Failed to inject market shock: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/market-shocks', methods=['GET'])
def list_market_shocks(simulation_id: str):
    """List all market shocks injected into a simulation."""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        memory = LocalMemory(Config.DATABASE_PATH)
        shocks = memory.list_market_shocks(simulation_id)
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "count": len(shocks),
                "shocks": shocks,
            }
        })
    except Exception as e:
        logger.error(f"Failed to list market shocks: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Real-time status monitoring API ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    get simulation run line real-time status（Used for front-end rounds query）
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"get run line status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    get simulation run line detailed status（include all actions）
    
    Used for front-end display of real-time dynamics
    
    Query parameter：
        platform: filter platform（twitter/reddit，optional）
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # Twitter platform of all actions
                "reddit_actions": [...]    # Reddit platform of all actions
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # get complete of action list
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # platform get action
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # get current round of action（recent_actions Show only latest one rounds）
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # get basic status information
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions Only display the current latest one rounds two platform of content
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"get detailed status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    get simulation in of Agent action history
    
    Query parameter：
        limit: return count（default 100）
        offset: offset（default 0）
        platform: filter platform（twitter/reddit）
        agent_id: filter Agent ID
        round_num: filter round
    
    return：
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"get action history failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    get simulation timeline（by round summary）
    
    Used for front-end display progress and timeline view
    
    Query parameter：
        start_round: starting round（default 0）
        end_round: end round（default all）
    
    return each rounds of summary information
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.error(f"get timeline failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    get every Agent of statistical information
    
    For front-end display Agent Activity ranking line、action distribution etc.
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.error(f"get Agent statistics failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== database query API ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    get simulation in of post
    
    Query parameter：
        platform: platform type（twitter/reddit）
        limit: return count（default 50）
        offset: offset
    
    return post list（from SQLite database read）
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )

        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": t('api.dbNotExist')
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            posts = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"get post failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    get simulation in of comment

    Query parameter：
        platform: platform type（twitter/reddit，Automatic selection based on simulation configuration）
        post_id: filter post ID（optional）
        limit: return count
        offset: offset
    """
    try:
        platform = request.args.get('platform') or _get_default_platform(simulation_id)
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)

        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, f"{platform}_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            comments = [dict(row) for row in cursor.fetchall()]
            
        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"get comment failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interview interview API ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    interview single Agent

    note：This function needs simulation environment running line status（After completed simulation loop, enter etc. waiting for command mode）

    request（JSON）：
        {
            "simulation_id": "sim_xxxx",       // required，simulation ID
            "agent_id": 0,                     // required，Agent ID
            "prompt": "what do you think about this matter？",  // required，interview questions
            "platform": "twitter",             // optional，Specify platform（twitter/reddit）
                                               // When not specified：Dual platform simulation interview two platforms at the same time
            "timeout": 60                      // optional，timeout time（seconds），default 60
        }

    return（Not specified platform，Dual platform mode）：
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "what do you think about this matter？",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    return（Specify platform）：
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "what do you think about this matter？",
                "result": {
                    "agent_id": 0,
                    "response": "I agree for...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # optional：twitter/reddit/None
        timeout = data.get('timeout', 60)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": t('api.requireAgentId')
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400
        
        # validate platform parameter
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400
        
        # check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400
        
        # optimization prompt，add prefix avoidance Agent Call tool
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.interviewTimeout', error=str(e))
        }), 504
        
    except Exception as e:
        logger.error(f"Interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    Lots of interviews in batches Agent

    note：This function needs simulation environment running line status

    request（JSON）：
        {
            "simulation_id": "sim_xxxx",       // required，simulation ID
            "interviews": [                    // required，interview list
                {
                    "agent_id": 0,
                    "prompt": "you are right A What do you think?？",
                    "platform": "twitter"      // optional，Specify the Agent interview platform
                },
                {
                    "agent_id": 1,
                    "prompt": "you are right B What do you think?？"  // Not specified platform Then use default value
                }
            ],
            "platform": "reddit",              // optional，default platform（by each item of platform cover）
                                               // When not specified：Dual platform simulation each Agent Interview two platforms at the same time
            "timeout": 120                     // optional，timeout time（seconds），default 120
        }

    return：
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # optional：twitter/reddit/None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": t('api.requireInterviews')
            }), 400

        # validate platform parameter
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # validate each interview item
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingAgentId', index=i+1)
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListMissingPrompt', index=i+1)
                }), 400
            # validate each item of platform（if there is）
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": t('api.interviewListInvalidPlatform', index=i+1)
                }), 400

        # check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # Optimize each interview item of prompt，add prefix avoidance Agent Call tool
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.batchInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.error(f"batch Interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    global interview - use same question interview all Agent

    note：This function needs simulation environment running line status

    request（JSON）：
        {
            "simulation_id": "sim_xxxx",            // required，simulation ID
            "prompt": "What is your overall opinion on this matter?？",  // required，interview questions（all Agent use same question）
            "platform": "reddit",                   // optional，Specify platform（twitter/reddit）
                                                    // When not specified：Dual platform simulation each Agent Interview two platforms at the same time
            "timeout": 180                          // optional，timeout time（seconds），default 180
        }

    return：
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # optional：twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": t('api.requirePrompt')
            }), 400

        # validate platform parameter
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": t('api.invalidInterviewPlatform')
            }), 400

        # check environment status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": t('api.envNotRunning')
            }), 400

        # optimization prompt，add prefix avoidance Agent Call tool
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": t('api.globalInterviewTimeout', error=str(e))
        }), 504

    except Exception as e:
        logger.error(f"overall situation Interview failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    get Interview history record

    from simulation database read all Interview record

    request（JSON）：
        {
            "simulation_id": "sim_xxxx",  // required，simulation ID
            "platform": "reddit",          // optional，platform type（reddit/twitter）
                                           // If not specified, return two platforms of all history.
            "agent_id": 0,                 // optional，only get the Agent of interview history
            "limit": 100                   // optional，return count，default 100
        }

    return：
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "I agree for...",
                        "prompt": "what do you think about this matter？",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # If not specified, return two platforms of history.
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"get Interview history failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    get simulation environment status

    check simulation environment is alive（can receive Interview Order）

    request（JSON）：
        {
            "simulation_id": "sim_xxxx"  // required，simulation ID
        }

    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "Environment is running and ready for interview commands"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # get more detailed status information
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = t('api.envRunning')
        else:
            message = t('api.envNotRunningShort')

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"get environment status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    close simulation environment
    
    Send close environment command to simulation，Make it graceful exit etc. await command mode。
    
    note：This is different from /stop API，/stop will forcefully terminate the process，
    This API will allow simulation to gracefully close the environment and exit。
    
    request（JSON）：
        {
            "simulation_id": "sim_xxxx",  // required，simulation ID
            "timeout": 30                  // optional，timeout time（seconds），default 30
        }
    
    return：
        {
            "success": true,
            "data": {
                "message": "Environment close command sent",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # update simulation status
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"close environment failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
