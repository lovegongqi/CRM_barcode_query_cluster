"""Barcode query management - wraps main.py functionality"""
import os
import sys
import json
import subprocess
from pathlib import Path
from datetime import datetime


# Import the original main.py module
main_py_path = Path(__file__).parent.parent.parent.parent.parent / 'main.py'
sys.path.insert(0, str(main_py_path.parent))

# We'll call main.py via subprocess since it has its own menu-driven interface


class QueryManager:
    """Manages barcode queries by invoking main.py"""

    def __init__(self):
        self.project_dir = Path(__file__).parent.parent.parent.parent.parent
        self.main_py = self.project_dir / 'main.py'
        self.app_py = self.project_dir / 'app.py'
        self.barcode_dir = self.project_dir / 'barcode'
        self.archive_dir = self.barcode_dir / 'archived'
        self.data_file = self.barcode_dir / 'barcode_data.json'

    def list_results(self):
        """List all query results"""
        barcodes = []
        archived = self._get_archived_set()

        if not self.barcode_dir.exists():
            return {'success': True, 'total': 0, 'barcodes': []}

        for filename in os.listdir(self.barcode_dir):
            if filename.endswith('.html'):
                barcode = filename.replace('.html', '')
                if barcode in archived:
                    continue
                filepath = self.barcode_dir / filename
                mtime = os.path.getmtime(filepath)
                time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')
                barcodes.append({
                    'barcode': barcode,
                    'time': time_str,
                    'archived': False
                })

        barcodes.sort(key=lambda x: x.get('time', ''), reverse=True)
        return {'success': True, 'total': len(barcodes), 'barcodes': barcodes}

    def list_archived(self):
        """List archived barcodes"""
        if not self.archive_dir.exists():
            return {'success': True, 'total': 0, 'barcodes': []}

        barcodes = []
        for filename in os.listdir(self.archive_dir):
            if filename.endswith('.html'):
                barcode = filename.replace('.html', '')
                filepath = self.archive_dir / filename
                mtime = os.path.getmtime(filepath)
                info = self._get_barcode_info(barcode)
                barcodes.append({
                    'barcode': barcode,
                    'time': datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'archiveTime': info.get('archiveTime', '')
                })

        barcodes.sort(key=lambda x: x.get('archiveTime', ''), reverse=True)
        return {'success': True, 'total': len(barcodes), 'barcodes': barcodes}

    def _get_archived_set(self):
        data = self._load_data()
        return {bc for bc, info in data.items() if info.get('archived')}

    def _load_data(self):
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _get_barcode_info(self, barcode):
        data = self._load_data()
        return data.get(barcode, {'remark': '', 'archived': False, 'archiveTime': ''})

    def archive(self, barcode):
        """Archive a barcode"""
        src = self.barcode_dir / f'{barcode}.html'
        if not src.exists():
            return {'success': False, 'error': '文件不存在'}

        try:
            os.makedirs(self.archive_dir, exist_ok=True)
            dst = self.archive_dir / f'{barcode}.html'
            os.rename(src, dst)

            data = self._load_data()
            info = data.get(barcode, {})
            info['archived'] = True
            info['archiveTime'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            data[barcode] = info
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return {'success': True, 'message': '归档成功'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def unarchive(self, barcode):
        """Unarchive a barcode"""
        src = self.archive_dir / f'{barcode}.html'
        if not src.exists():
            return {'success': False, 'error': '归档文件不存在'}

        try:
            dst = self.barcode_dir / f'{barcode}.html'
            os.rename(src, dst)

            data = self._load_data()
            info = data.get(barcode, {})
            info['archived'] = False
            info['archiveTime'] = ''
            data[barcode] = info
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            return {'success': True, 'message': '取消归档成功'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_detail(self, barcode):
        """Get detailed info for a barcode"""
        filepath = self.barcode_dir / f'{barcode}.html'
        if not filepath.exists():
            filepath = self.archive_dir / f'{barcode}.html'
        if not filepath.exists():
            return {'success': False, 'error': '条码不存在'}

        # Extract basic info
        mtime = os.path.getmtime(filepath)
        time_str = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M:%S')

        # Try to extract some fields from HTML
        fields = self._extract_basic_fields(filepath)

        return {
            'success': True,
            'barcode': barcode,
            'time': time_str,
            'fields': fields
        }

    def _extract_basic_fields(self, filepath):
        """Extract basic fields from HTML file"""
        import re
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                html = f.read()

            # Simple extraction of key values
            fields = {}
            patterns = [
                (r'id="newname1"[^>]*>.*?<span[^>]*>([^<]+)</span>', '条码号'),
                (r'id="ProductNumber1"[^>]*>.*?<span[^>]*>([^<]+)</span>', '物料编码'),
                (r'id="newproductidName1"[^>]*>.*?<span[^>]*>([^<]+)</span>', '机型'),
            ]

            for pattern, label in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    fields[label] = match.group(1).strip()

            return fields
        except Exception:
            return {}

    def query_single(self, barcode):
        """Query a single barcode using main.py"""
        try:
            result = subprocess.run(
                ['python3', str(self.main_py)],
                input=f'1\n{barcode}\n6\n',
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                timeout=120
            )
            return {
                'success': True,
                'barcode': barcode,
                'output': result.stdout
            }
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': '查询超时'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def query_batch(self, barcode_file, background=True):
        """Batch query from file using main.py"""
        if not os.path.exists(barcode_file):
            return {'success': False, 'error': f'文件不存在: {barcode_file}'}

        try:
            mode = '5' if background else '2'
            result = subprocess.run(
                ['python3', str(self.main_py)],
                input=f'{mode}\n{barcode_file}\n6\n',
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                timeout=600
            )
            return {
                'success': True,
                'output': result.stdout
            }
        except subprocess.TimeoutExpired:
            return {'success': False, 'error': '批量查询超时'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def start_web_ui(self):
        """Start the web UI (app.py)"""
        try:
            subprocess.Popen(
                ['python3', str(self.app_py)],
                cwd=str(self.project_dir)
            )
            return {'success': True, 'message': 'Web UI 已启动，请访问 http://localhost:5001'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
