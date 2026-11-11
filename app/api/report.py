"""
Report API routes.
Provides simulation report generation, retrieval, and chat interfaces.
"""

import os
import traceback
import threading
from contextlib import nullcontext
from flask import request, jsonify, send_file

from . import report_bp
from ..config import Config
from ..services.report_agent import ReportAgent, ReportManager, ReportStatus
from ..services.simulation_manager import SimulationManager
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..services.local_memory import LocalMemory
from ..models.project import ProjectManager, ProjectStatus
from ..models.task import TaskManager, TaskStatus
from ..utils.logger import get_logger
from ..utils.locale import t, get_locale, set_locale

logger = get_logger('Veyronix.api.report')


# ============== report generateAPI ==============

@report_bp.route('/generate', methods=['POST'])
def generate_report():
    """
    generate simulation analysis report（async task）
    
    This is a time-consuming operation，API will return immediately task_id，
    use GET /api/report/generate/status query progress
    
    request（JSON）：
        {
            "simulation_id": "sim_xxxx",    // required，simulation ID
            "force_regenerate": false        // optional，Force new generate
        }
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",
                "status": "generating",
                "message": "Report generation task started"
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

        force_regenerate = data.get('force_regenerate', False)
        if not isinstance(force_regenerate, bool):
            return jsonify({
                "success": False,
                "error": "force_regenerate must be a JSON boolean",
            }), 400
        
        # get simulation information
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        run_state = SimulationRunner.get_run_state(simulation_id)
        updater = None  # Graph memory updater replaced by local SQLite
        active_statuses = {
            RunnerStatus.STARTING,
            RunnerStatus.RUNNING,
            RunnerStatus.PAUSED,
            RunnerStatus.STOPPING,
        }
        if updater is not None or (
            run_state is not None and run_state.runner_status in active_statuses
        ):
            return jsonify({
                "success": False,
                "error": (
                    "Simulation or graph ingestion is still active; "
                    "wait for a terminal run status before generating a report"
                ),
                "ingestion_pending": updater is not None,
            }), 409
        successful_terminal_statuses = {
            RunnerStatus.COMPLETED,
            RunnerStatus.STOPPED,
        }
        if (
            run_state is None
            or run_state.runner_status not in successful_terminal_statuses
        ):
            return jsonify({
                "success": False,
                "error": (
                    "A successfully completed or stopped simulation is required "
                    "before generating a report"
                ),
            }), 409

        # get project information
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=state.project_id)
            }), 404
        
        if project.status != ProjectStatus.GRAPH_COMPLETED:
            return jsonify({
                "success": False,
                "error": "The project graph must be completely built before reporting",
            }), 409

        graph_id = project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.missingGraphIdEnsure')
            }), 400
        if state.graph_id and state.graph_id != graph_id:
            return jsonify({
                "success": False,
                "error": (
                    "The simulation references an older graph; prepare it "
                    "again before generating a report"
                ),
            }), 409
        
        simulation_requirement = project.simulation_requirement
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": t('api.missingSimRequirement')
            }), 400
        
        # generate in advance report_id，So that it can be returned to the front end immediately
        import uuid
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        
        # Graph memory handled by local SQLite - no locking needed
        with nullcontext():
            refreshed_state = manager.get_simulation(simulation_id)
            refreshed_project = (
                ProjectManager.get_project(refreshed_state.project_id)
                if refreshed_state
                else None
            )
            refreshed_run_state = SimulationRunner.get_run_state(simulation_id)
            refreshed_updater = None  # Graph memory updater replaced by local SQLite
            if (
                refreshed_state is None
                or refreshed_project is None
                or refreshed_project.graph_id != graph_id
                or refreshed_project.status != ProjectStatus.GRAPH_COMPLETED
                or (
                    refreshed_state.graph_id
                    and refreshed_state.graph_id != graph_id
                )
            ):
                return jsonify({
                    "success": False,
                    "error": "The project graph changed while reporting was starting",
                }), 409
            if refreshed_updater is not None or (
                refreshed_run_state is not None
                and refreshed_run_state.runner_status in active_statuses
            ):
                return jsonify({
                    "success": False,
                    "error": (
                        "Simulation or graph ingestion became active; "
                        "retry after it reaches a terminal state"
                    ),
                    "ingestion_pending": refreshed_updater is not None,
                }), 409
            if (
                refreshed_run_state is None
                or refreshed_run_state.runner_status
                not in successful_terminal_statuses
            ):
                return jsonify({
                    "success": False,
                    "error": (
                        "A successfully completed or stopped simulation is "
                        "required before generating a report"
                    ),
                }), 409

            # Cached-report reuse is now part of the same atomic barrier, so a
            # concurrent rerun cannot make the returned report stale between
            # the status check and response.
            if not force_regenerate:
                existing_report = ReportManager.get_report_by_simulation(
                    simulation_id
                )
                if (
                    existing_report
                    and existing_report.status == ReportStatus.COMPLETED
                ):
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "report_id": existing_report.report_id,
                            "status": "completed",
                            "message": t('api.reportAlreadyExists'),
                            "already_generated": True
                        }
                    })

            task_manager = TaskManager()
            task_id = task_manager.create_task(
                task_type="report_generate",
                metadata={
                    "simulation_id": simulation_id,
                    "graph_id": graph_id,
                    "report_id": report_id
                }
            )
            current_locale = get_locale()
            # Graph reader registration replaced by local SQLite - no registration needed

            def run_generate():
                set_locale(current_locale)
                try:
                    task_manager.update_task(
                        task_id,
                        status=TaskStatus.PROCESSING,
                        progress=0,
                        message=t('api.initReportAgent')
                    )

                    agent = ReportAgent(
                        graph_id=graph_id,
                        simulation_id=simulation_id,
                        simulation_requirement=simulation_requirement
                    )

                    def progress_callback(stage, progress, message):
                        task_manager.update_task(
                            task_id,
                            progress=progress,
                            message=f"[{stage}] {message}"
                        )

                    report = agent.generate_report(
                        progress_callback=progress_callback,
                        report_id=report_id
                    )
                    ReportManager.save_report(report)

                    if report.status == ReportStatus.COMPLETED:
                        task_manager.complete_task(
                            task_id,
                            result={
                                "report_id": report.report_id,
                                "simulation_id": simulation_id,
                                "status": "completed"
                            }
                        )
                    else:
                        task_manager.fail_task(
                            task_id,
                            report.error or t('api.reportGenerateFailed')
                        )
                except Exception as e:
                    logger.error(f"report generate failed: {str(e)}")
                    task_manager.fail_task(task_id, str(e))
                finally:
                    # Graph reader unregistration replaced by local SQLite - no cleanup needed
                    pass

            try:
                thread = threading.Thread(target=run_generate, daemon=True)
                thread.start()
            except Exception:
                # Graph reader unregistration replaced by local SQLite - no cleanup needed
                pass
                raise
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "report_id": report_id,
                "task_id": task_id,
                "status": "generating",
                "message": t('api.reportGenerateStarted'),
                "already_generated": False
            }
        })
        
    except Exception as e:
        logger.error(f"Start report generate task failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/generate/status', methods=['POST'])
def get_generate_status():
    """
    query report generate task progress
    
    request（JSON）：
        {
            "task_id": "task_xxxx",         // optional，generate return of task_id
            "simulation_id": "sim_xxxx"     // optional，simulation ID
        }
    
    return：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|failed",
                "progress": 45,
                "message": "..."
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # if provides simulation_id，First check whether there is a completed report
        if simulation_id:
            existing_report = ReportManager.get_report_by_simulation(simulation_id)
            if existing_report and existing_report.status == ReportStatus.COMPLETED:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "report_id": existing_report.report_id,
                        "status": "completed",
                        "progress": 100,
                        "message": t('api.reportGenerated'),
                        "already_completed": True
                    }
                })
        
        if not task_id:
            return jsonify({
                "success": False,
                "error": t('api.requireTaskOrSimId')
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            return jsonify({
                "success": False,
                "error": t('api.taskNotFound', id=task_id)
            }), 404
        
        return jsonify({
            "success": True,
            "data": task.to_dict()
        })
        
    except Exception as e:
        logger.error(f"query task status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============== report get API ==============

@report_bp.route('/<report_id>', methods=['GET'])
def get_report(report_id: str):
    """
    get report details
    
    return：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "simulation_id": "sim_xxxx",
                "status": "completed",
                "outline": {...},
                "markdown_content": "...",
                "created_at": "...",
                "completed_at": "..."
            }
        }
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": t('api.reportNotFound', id=report_id)
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict()
        })
        
    except Exception as e:
        logger.error(f"get report failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/by-simulation/<simulation_id>', methods=['GET'])
def get_report_by_simulation(simulation_id: str):
    """
    According to simulation ID get report
    
    return：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                ...
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": t('api.noReportForSim', id=simulation_id),
                "has_report": False
            }), 404
        
        return jsonify({
            "success": True,
            "data": report.to_dict(),
            "has_report": True
        })
        
    except Exception as e:
        logger.error(f"get report failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/list', methods=['GET'])
def list_reports():
    """
    column out all report
    
    Query parameter：
        simulation_id: by simulation ID filter（optional）
        limit: return count limit（default 50）
    
    return：
        {
            "success": true,
            "data": [...],
            "count": 10
        }
    """
    try:
        simulation_id = request.args.get('simulation_id')
        limit = request.args.get('limit', 50, type=int)
        
        reports = ReportManager.list_reports(
            simulation_id=simulation_id,
            limit=limit
        )
        
        return jsonify({
            "success": True,
            "data": [r.to_dict() for r in reports],
            "count": len(reports)
        })
        
    except Exception as e:
        logger.error(f"column out report failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/download', methods=['GET'])
def download_report(report_id: str):
    """
    download report（Markdown format）
    
    return Markdown file
    """
    try:
        report = ReportManager.get_report(report_id)
        
        if not report:
            return jsonify({
                "success": False,
                "error": t('api.reportNotFound', id=report_id)
            }), 404
        
        md_path = ReportManager._get_report_markdown_path(report_id)
        
        if not os.path.exists(md_path):
            # if MD file does not exist in，generate a temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
                f.write(report.markdown_content)
                temp_path = f.name
            
            return send_file(
                temp_path,
                as_attachment=True,
                download_name=f"{report_id}.md"
            )
        
        return send_file(
            md_path,
            as_attachment=True,
            download_name=f"{report_id}.md"
        )
        
    except Exception as e:
        logger.error(f"download report failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>', methods=['DELETE'])
def delete_report(report_id: str):
    """delete report"""
    try:
        success = ReportManager.delete_report(report_id)
        
        if not success:
            return jsonify({
                "success": False,
                "error": t('api.reportNotFound', id=report_id)
            }), 404
        
        return jsonify({
            "success": True,
            "message": t('api.reportDeleted', id=report_id)
        })
        
    except Exception as e:
        logger.error(f"delete report failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Report Agent Conversation API ==============

@report_bp.route('/chat', methods=['POST'])
def chat_with_report_agent():
    """
    and Report Agent dialogue
    
    Report Agent can call the retrieve tool autonomously in the conversation to answer the question
    
    request（JSON）：
        {
            "simulation_id": "sim_xxxx",        // required，simulation ID
            "message": "Explain the current public opinion trend",    // required, user message
            "chat_history": [                   // optional，conversation history
                {"role": "user", "content": "..."},
                {"role": "assistant", "content": "..."}
            ]
        }
    
    return：
        {
            "success": true,
            "data": {
                "response": "Agent reply...",
                "tool_calls": [Call of tool list],
                "sources": [source of information]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        message = data.get('message')
        chat_history = data.get('chat_history', [])
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": t('api.requireSimulationId')
            }), 400

        if not message:
            return jsonify({
                "success": False,
                "error": t('api.requireMessage')
            }), 400
        
        # get simulation and project information
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": t('api.simulationNotFound', id=simulation_id)
            }), 404

        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": t('api.projectNotFound', id=state.project_id)
            }), 404
        
        graph_id = state.graph_id or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": t('api.missingGraphId')
            }), 400
        
        simulation_requirement = project.simulation_requirement or ""
        
        # create Agent parallel line conversation
        agent = ReportAgent(
            graph_id=graph_id,
            simulation_id=simulation_id,
            simulation_requirement=simulation_requirement
        )
        
        result = agent.chat(message=message, chat_history=chat_history)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Conversation failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== report progress and section API ==============

@report_bp.route('/<report_id>/progress', methods=['GET'])
def get_report_progress(report_id: str):
    """
    get report generate progress（real time）
    
    return：
        {
            "success": true,
            "data": {
                "status": "generating",
                "progress": 45,
                "message": "Generating section: Key Findings",
                "current_section": "Key Findings",
                "completed_sections": ["Executive Summary", "Simulation Background"],
                "updated_at": "2025-12-09T..."
            }
        }
    """
    try:
        progress = ReportManager.get_progress(report_id)
        
        if not progress:
            return jsonify({
                "success": False,
                "error": t('api.reportProgressNotAvail', id=report_id)
            }), 404
        
        return jsonify({
            "success": True,
            "data": progress
        })
        
    except Exception as e:
        logger.error(f"get report progress failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/sections', methods=['GET'])
def get_report_sections(report_id: str):
    """
    get generated of section list（section output）
    
    The front end can rounds query this API to get generated of section content，none requires etc. to be adjusted report completed
    
    return：
        {
            "success": true,
            "data": {
                "report_id": "report_xxxx",
                "sections": [
                    {
                        "filename": "section_01.md",
                        "section_index": 1,
                        "content": "## Execute line summary\\n\\n..."
                    },
                    ...
                ],
                "total_sections": 3,
                "is_complete": false
            }
        }
    """
    try:
        sections = ReportManager.get_generated_sections(report_id)
        
        # get report status
        report = ReportManager.get_report(report_id)
        is_complete = report is not None and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "report_id": report_id,
                "sections": sections,
                "total_sections": len(sections),
                "is_complete": is_complete
            }
        })
        
    except Exception as e:
        logger.error(f"get section list failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/section/<int:section_index>', methods=['GET'])
def get_single_section(report_id: str, section_index: int):
    """
    get single section content
    
    return：
        {
            "success": true,
            "data": {
                "filename": "section_01.md",
                "content": "## Execute line summary\\n\\n..."
            }
        }
    """
    try:
        section_path = ReportManager._get_section_path(report_id, section_index)
        
        if not os.path.exists(section_path):
            return jsonify({
                "success": False,
                "error": t('api.sectionNotFound', index=f"{section_index:02d}")
            }), 404
        
        with open(section_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({
            "success": True,
            "data": {
                "filename": f"section_{section_index:02d}.md",
                "section_index": section_index,
                "content": content
            }
        })
        
    except Exception as e:
        logger.error(f"get section content failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== report status check API ==============

@report_bp.route('/check/<simulation_id>', methods=['GET'])
def check_report_status(simulation_id: str):
    """
    check simulation if there is report，and report status
    
    Used by the front end to determine whether to unlock the lock Interview Function
    
    return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "has_report": true,
                "report_status": "completed",
                "report_id": "report_xxxx",
                "interview_unlocked": true
            }
        }
    """
    try:
        report = ReportManager.get_report_by_simulation(simulation_id)
        
        has_report = report is not None
        report_status = report.status.value if report else None
        report_id = report.report_id if report else None
        
        # The lock is released only after report completed interview
        interview_unlocked = has_report and report.status == ReportStatus.COMPLETED
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "has_report": has_report,
                "report_status": report_status,
                "report_id": report_id,
                "interview_unlocked": interview_unlocked
            }
        })
        
    except Exception as e:
        logger.error(f"check report status failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Agent log API ==============

@report_bp.route('/<report_id>/agent-log', methods=['GET'])
def get_agent_log(report_id: str):
    """
    get Report Agent of detailed execution line log
    
    Real-time get report generate process of each step of action，include：
    - report start、planning start/completed
    - Every section of start、Tool call、LLM response、completed
    - report completed or failed
    
    Query parameter：
        from_line: from line start read（optional，default 0，for incremental get）
    
    return：
        {
            "success": true,
            "data": {
                "logs": [
                    {
                        "timestamp": "2025-12-13T...",
                        "elapsed_seconds": 12.5,
                        "report_id": "report_xxxx",
                        "action": "tool_call",
                        "stage": "generating",
                        "section_title": "Execute line summary",
                        "section_index": 1,
                        "details": {
                            "tool_name": "insight_forge",
                            "parameters": {...},
                            ...
                        }
                    },
                    ...
                ],
                "total_lines": 25,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_agent_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"get Agent log failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/agent-log/stream', methods=['GET'])
def stream_agent_log(report_id: str):
    """
    get complete of Agent log（one times sex get all）
    
    return：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 25
            }
        }
    """
    try:
        logs = ReportManager.get_agent_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"get Agent log failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Console log API ==============

@report_bp.route('/<report_id>/console-log', methods=['GET'])
def get_console_log(report_id: str):
    """
    get Report Agent of console output log
    
    Real-time get report generate process of console output（INFO、WARNING etc.），
    this and agent-log API return of structured JSON log different，
    Is a pure text format of console style log。
    
    Query parameter：
        from_line: from line start read（optional，default 0，for incremental get）
    
    return：
        {
            "success": true,
            "data": {
                "logs": [
                    "[19:46:14] INFO: search completed: Find to 15 Relevant facts",
                    "[19:46:14] INFO: graph search: graph_id=xxx, query=...",
                    ...
                ],
                "total_lines": 100,
                "from_line": 0,
                "has_more": false
            }
        }
    """
    try:
        from_line = request.args.get('from_line', 0, type=int)
        
        log_data = ReportManager.get_console_log(report_id, from_line=from_line)
        
        return jsonify({
            "success": True,
            "data": log_data
        })
        
    except Exception as e:
        logger.error(f"get console log failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/<report_id>/console-log/stream', methods=['GET'])
def stream_console_log(report_id: str):
    """
    get complete of console log（one times sex get all）
    
    return：
        {
            "success": true,
            "data": {
                "logs": [...],
                "count": 100
            }
        }
    """
    try:
        logs = ReportManager.get_console_log_stream(report_id)
        
        return jsonify({
            "success": True,
            "data": {
                "logs": logs,
                "count": len(logs)
            }
        })
        
    except Exception as e:
        logger.error(f"get console log failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Tool call API（for debugging use）==============

@report_bp.route('/tools/search', methods=['POST'])
def search_graph_tool():
    """
    graph search tool API（for debugging use）
    
    request（JSON）：
        {
            "graph_id": "Veyronix_xxxx",
            "query": "search query",
            "limit": 10
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        query = data.get('query')
        limit = data.get('limit', 10)
        
        if not graph_id or not query:
            return jsonify({
                "success": False,
                "error": t('api.requireGraphIdAndQuery')
            }), 400
        
        memory = LocalMemory(Config.DATABASE_PATH)
        agents = memory.list_agents()
        
        # Simple text search across agent personas
        results = []
        for agent in agents:
            persona = agent.get('persona', {})
            content = str(persona).lower()
            if query.lower() in content:
                results.append(agent)
        
        return jsonify({
            "success": True,
            "data": {
                "facts": [],
                "edges": [],
                "nodes": results,
                "query": query,
                "total_count": len(results)
            }
        })
        
    except Exception as e:
        logger.error(f"graph search failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@report_bp.route('/tools/statistics', methods=['POST'])
def get_graph_statistics_tool():
    """
    graph statistics tools API（for debugging use）
    
    request（JSON）：
        {
            "graph_id": "Veyronix_xxxx"
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
        
        memory = LocalMemory(Config.DATABASE_PATH)
        agents = memory.list_agents()
        summaries = memory.list_summaries()
        
        return jsonify({
            "success": True,
            "data": {
                "agent_count": len(agents),
                "summary_count": len(summaries),
                "graph_id": graph_id
            }
        })
        
    except Exception as e:
        logger.error(f"get graph statistics failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500
