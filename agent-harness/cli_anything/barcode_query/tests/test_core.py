"""Unit tests for barcode_query CLI"""
import pytest
import os
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))


class TestProjectManager:
    """Tests for ProjectManager"""

    def test_create_project(self, tmp_path):
        from cli_anything.barcode_query.core.project import ProjectManager

        project_file = tmp_path / "test_project.json"
        pm = ProjectManager(str(project_file))
        result = pm.create_project("test_project")

        assert result['success'] is True
        assert pm.data['name'] == 'test_project'

    def test_get_status(self, tmp_path):
        from cli_anything.barcode_query.core.project import ProjectManager

        project_file = tmp_path / "test_project.json"
        pm = ProjectManager(str(project_file))
        pm.create_project("test")

        status = pm.get_status()
        assert status['success'] is True
        assert status['name'] == 'test'


class TestQueryManager:
    """Tests for QueryManager"""

    def test_init(self):
        from cli_anything.barcode_query.core.query import QueryManager

        qm = QueryManager()
        assert qm.barcode_dir.exists()

    def test_list_results_empty(self):
        from cli_anything.barcode_query.core.query import QueryManager

        qm = QueryManager()
        result = qm.list_results()

        assert result['success'] is True
        assert 'barcodes' in result


class TestExportManager:
    """Tests for ExportManager"""

    def test_init(self):
        from cli_anything.barcode_query.core.export import ExportManager

        em = ExportManager()
        assert em.barcode_dir.exists()

    def test_export_xlsx_no_barcodes(self):
        from cli_anything.barcode_query.core.export import ExportManager

        em = ExportManager()
        result = em.export_xlsx(barcodes=[], export_all=False)

        assert result['success'] is False
        assert 'error' in result
