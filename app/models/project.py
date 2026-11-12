"""
project context management
Used in server-side persistence project status，Avoid passing large amounts of data between front-end in APIs
"""

import os
import json
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional
from enum import Enum
from dataclasses import dataclass, field, asdict
from ..config import Config


class ProjectStatus(str, Enum):
    """project status"""
    CREATED = "created"              # Just create，file uploaded
    ONTOLOGY_GENERATED = "ontology_generated"  # ontology has generated
    GRAPH_BUILDING = "graph_building"    # in graph building
    GRAPH_COMPLETED = "graph_completed"  # graph building completed
    FAILED = "failed"                # failed


@dataclass
class Project:
    """project data model"""
    project_id: str
    name: str
    status: ProjectStatus
    created_at: str
    updated_at: str
    
    # file information
    files: List[Dict[str, str]] = field(default_factory=list)  # [{filename, path, size}]
    total_text_length: int = 0
    
    # ontology information（API 1 Fill after generate）
    ontology: Optional[Dict[str, Any]] = None
    analysis_summary: Optional[str] = None
    
    # graph information（API 2 Fill in after completed）
    graph_id: Optional[str] = None
    graph_build_task_id: Optional[str] = None
    zep_batch_id: Optional[str] = None
    zep_batch_operation_id: Optional[str] = None
    
    # configuration
    simulation_requirement: Optional[str] = None
    chunk_size: int = 4000
    chunk_overlap: int = 200
    
    # error information
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """convert for characters dictionary"""
        return {
            "project_id": self.project_id,
            "name": self.name,
            "status": self.status.value if isinstance(self.status, ProjectStatus) else self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "files": self.files,
            "total_text_length": self.total_text_length,
            "ontology": self.ontology,
            "analysis_summary": self.analysis_summary,
            "graph_id": self.graph_id,
            "graph_build_task_id": self.graph_build_task_id,
            "zep_batch_id": self.zep_batch_id,
            "zep_batch_operation_id": self.zep_batch_operation_id,
            "simulation_requirement": self.simulation_requirement,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "error": self.error
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Project':
        """from characters create"""
        status = data.get('status', 'created')
        if isinstance(status, str):
            status = ProjectStatus(status)
        
        return cls(
            project_id=data['project_id'],
            name=data.get('name', 'Unnamed Project'),
            status=status,
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
            files=data.get('files', []),
            total_text_length=data.get('total_text_length', 0),
            ontology=data.get('ontology'),
            analysis_summary=data.get('analysis_summary'),
            graph_id=data.get('graph_id'),
            graph_build_task_id=data.get('graph_build_task_id'),
            zep_batch_id=data.get('zep_batch_id'),
            zep_batch_operation_id=data.get('zep_batch_operation_id'),
            simulation_requirement=data.get('simulation_requirement'),
            chunk_size=data.get('chunk_size', 4000),
            chunk_overlap=data.get('chunk_overlap', 200),
            error=data.get('error')
        )


class ProjectManager:
    """project manager - Responsible for the project of persistence storage and retrieval"""
    
    # project storage root directory
    PROJECTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'projects')
    
    @classmethod
    def _ensure_projects_dir(cls):
        """ensure project directory is stored in"""
        os.makedirs(cls.PROJECTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_project_dir(cls, project_id: str) -> str:
        """get project directory path"""
        return os.path.join(cls.PROJECTS_DIR, project_id)
    
    @classmethod
    def _get_project_meta_path(cls, project_id: str) -> str:
        """get project meta data file path"""
        return os.path.join(cls._get_project_dir(project_id), 'project.json')
    
    @classmethod
    def _get_project_files_dir(cls, project_id: str) -> str:
        """get project file storage directory"""
        return os.path.join(cls._get_project_dir(project_id), 'files')
    
    @classmethod
    def _get_project_text_path(cls, project_id: str) -> str:
        """get project extract text storage path"""
        return os.path.join(cls._get_project_dir(project_id), 'extracted_text.txt')
    
    @classmethod
    def create_project(cls, name: str = "Unnamed Project") -> Project:
        """
        create new project
        
        Args:
            name: project name
            
        Returns:
            new create of Project object
        """
        cls._ensure_projects_dir()
        
        project_id = f"proj_{uuid.uuid4().hex[:12]}"
        now = datetime.now().isoformat()
        
        project = Project(
            project_id=project_id,
            name=name,
            status=ProjectStatus.CREATED,
            created_at=now,
            updated_at=now
        )
        
        # create project directory structure
        project_dir = cls._get_project_dir(project_id)
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(project_dir, exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        
        # save project metadata
        cls.save_project(project)
        
        return project
    
    @classmethod
    def save_project(cls, project: Project) -> None:
        """save project metadata"""
        project.updated_at = datetime.now().isoformat()
        meta_path = cls._get_project_meta_path(project.project_id)
        
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(project.to_dict(), f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_project(cls, project_id: str) -> Optional[Project]:
        """
        get project
        
        Args:
            project_id: project ID
            
        Returns:
            Project object，if does not exist in return None
        """
        meta_path = cls._get_project_meta_path(project_id)
        
        if not os.path.exists(meta_path):
            return None
        
        with open(meta_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return Project.from_dict(data)
    
    @classmethod
    def list_projects(cls, limit: Optional[int] = 50) -> List[Project]:
        """
        column out all project
        
        Args:
            limit: return count limit
            
        Returns:
            project list，by create time reverse order
        """
        cls._ensure_projects_dir()
        
        projects = []
        for project_id in os.listdir(cls.PROJECTS_DIR):
            project = cls.get_project(project_id)
            if project:
                projects.append(project)
        
        # by create time reverse sort
        projects.sort(key=lambda p: p.created_at, reverse=True)
        
        return projects if limit is None else projects[:limit]

    @classmethod
    def find_projects_by_graph_id(cls, graph_id: str) -> List[Project]:
        """Return every persisted project that references a Cloud graph."""

        return [
            project
            for project in cls.list_projects(limit=None)
            if project.graph_id == graph_id
        ]
    
    @classmethod
    def delete_project(cls, project_id: str) -> bool:
        """
        delete project and all its files
        
        Args:
            project_id: project ID
            
        Returns:
            Whether to delete success
        """
        project_dir = cls._get_project_dir(project_id)
        
        if not os.path.exists(project_dir):
            return False
        
        shutil.rmtree(project_dir)
        return True
    
    @classmethod
    def save_file_to_project(cls, project_id: str, file_storage, original_filename: str) -> Dict[str, str]:
        """
        save upload of file to project directory
        
        Args:
            project_id: project ID
            file_storage: Flask of FileStorage object
            original_filename: original file name
            
        Returns:
            file information characters {filename, path, size}
        """
        files_dir = cls._get_project_files_dir(project_id)
        os.makedirs(files_dir, exist_ok=True)
        
        # generate security of file name
        ext = os.path.splitext(original_filename)[1].lower()
        safe_filename = f"{uuid.uuid4().hex[:8]}{ext}"
        file_path = os.path.join(files_dir, safe_filename)
        
        # save file
        file_storage.save(file_path)
        
        # get file size
        file_size = os.path.getsize(file_path)
        
        return {
            "original_filename": original_filename,
            "saved_filename": safe_filename,
            "path": file_path,
            "size": file_size
        }
    
    @classmethod
    def save_extracted_text(cls, project_id: str, text: str) -> None:
        """save extract of text"""
        text_path = cls._get_project_text_path(project_id)
        with open(text_path, 'w', encoding='utf-8') as f:
            f.write(text)
    
    @classmethod
    def get_extracted_text(cls, project_id: str) -> Optional[str]:
        """get extract of text"""
        text_path = cls._get_project_text_path(project_id)
        
        if not os.path.exists(text_path):
            return None
        
        with open(text_path, 'r', encoding='utf-8') as f:
            return f.read()
    
    @classmethod
    def get_project_files(cls, project_id: str) -> List[str]:
        """get project of all file path"""
        files_dir = cls._get_project_files_dir(project_id)
        
        if not os.path.exists(files_dir):
            return []
        
        return [
            os.path.join(files_dir, f) 
            for f in os.listdir(files_dir) 
            if os.path.isfile(os.path.join(files_dir, f))
        ]
