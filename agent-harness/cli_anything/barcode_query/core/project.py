"""Project state management"""
import os
import json
from datetime import datetime
from pathlib import Path


class ProjectManager:
    """Manages project state and history"""

    def __init__(self, project_file='barcode_project.json'):
        self.project_file = project_file
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.project_file):
            try:
                with open(self.project_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {
            'created_at': datetime.now().isoformat(),
            'query_count': 0,
            'archived_count': 0,
            'queries': []
        }

    def _save(self):
        with open(self.project_file, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def create_project(self, name):
        self.data = {
            'name': name,
            'created_at': datetime.now().isoformat(),
            'query_count': 0,
            'archived_count': 0,
            'queries': []
        }
        self._save()
        return {'success': True, 'name': name}

    def add_query(self, barcode):
        self.data['query_count'] += 1
        self.data['queries'].append({
            'barcode': barcode,
            'timestamp': datetime.now().isoformat()
        })
        self._save()
        return {'success': True}

    def get_status(self):
        return {
            'success': True,
            'project_file': self.project_file,
            'name': self.data.get('name', 'unnamed'),
            'created_at': self.data.get('created_at', ''),
            'query_count': self.data.get('query_count', 0),
            'archived_count': self.data.get('archived_count', 0)
        }
