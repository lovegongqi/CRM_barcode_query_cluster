"""Helper utilities"""
import json


def print_banner():
    """Print CLI banner"""
    print("╔══════════════════════════════════════════╗")
    print("║       cli-anything-barcode_query        ║")
    print("║     怡口 CRM 条码查询系统 CLI           ║")
    print("╚══════════════════════════════════════════╝")


def format_json(data):
    """Format data as JSON string"""
    return json.dumps(data, ensure_ascii=False, indent=2)


def load_json(filepath):
    """Load JSON from file"""
    import os
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None


def save_json(filepath, data):
    """Save data to JSON file"""
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
