#!/usr/bin/env python3
"""怡口 CRM 条码查询 CLI - Click-based CLI with REPL support"""
import os
import sys
import json
import click
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from cli_anything.barcode_query.core.project import ProjectManager
from cli_anything.barcode_query.core.session import CRMSessionManager
from cli_anything.barcode_query.core.query import QueryManager
from cli_anything.barcode_query.core.export import ExportManager
from cli_anything.barcode_query.utils.helpers import load_json, save_json, print_banner, format_json

PROJECT_FILE = "barcode_project.json"


@click.group()
@click.option('--json', 'output_json', is_flag=True, help='Output results as JSON')
@click.option('--project', default=PROJECT_FILE, help='Project state file')
@click.pass_context
def cli(ctx, output_json, project):
    """怡口 CRM 条码查询系统 CLI"""
    ctx.ensure_object(dict)
    ctx.obj['output_json'] = output_json
    ctx.obj['project'] = project
    ctx.obj['project_manager'] = ProjectManager(project)
    ctx.obj['session_manager'] = CRMSessionManager()
    ctx.obj['query_manager'] = QueryManager()
    ctx.obj['export_manager'] = ExportManager()


@cli.command()
@click.option('--name', default='barcode_project', help='Project name')
@click.pass_context
def new(ctx, name):
    """创建新项目"""
    pm = ctx.obj['project_manager']
    result = pm.create_project(name)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 项目已创建: {name}")
            click.echo(f"  项目文件: {ctx.obj['project']}")
        else:
            click.echo(f"✘ 创建失败: {result.get('error')}", err=True)


@cli.command()
@click.pass_context
def status(ctx):
    """查看项目状态"""
    pm = ctx.obj['project_manager']
    result = pm.get_status()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        click.echo("=" * 50)
        click.echo("  项目状态")
        click.echo("=" * 50)
        click.echo(f"  项目文件: {result.get('project_file')}")
        click.echo(f"  创建时间: {result.get('created_at', 'N/A')}")
        click.echo(f"  查询记录数: {result.get('query_count', 0)}")
        click.echo(f"  已归档数: {result.get('archived_count', 0)}")
        click.echo("=" * 50)


@cli.group()
def crm():
    """CRM 会话管理"""
    pass


@crm.command()
@click.argument('username')
@click.argument('password')
@click.pass_context
def login(ctx, username, password):
    """登录 CRM"""
    sm = ctx.obj['session_manager']
    result = sm.login(username, password)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 登录成功: {username}")
            if result.get('message'):
                click.echo(f"  {result['message']}")
        else:
            click.echo(f"✘ 登录失败: {result.get('error')}", err=True)


@crm.command()
@click.pass_context
def logout(ctx):
    """登出 CRM"""
    sm = ctx.obj['session_manager']
    result = sm.logout()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo("✓ 已登出")
        else:
            click.echo(f"✘ 登出失败: {result.get('error')}", err=True)


@crm.command()
@click.pass_context
def session_status(ctx):
    """查看会话状态"""
    sm = ctx.obj['session_manager']
    result = sm.get_status()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        click.echo("=" * 50)
        click.echo("  CRM 会话状态")
        click.echo("=" * 50)
        click.echo(f"  浏览器运行中: {'是' if result.get('browser_running') else '否'}")
        click.echo(f"  已登录: {'是' if result.get('logged_in') else '否'}")
        click.echo("=" * 50)


@cli.group()
def query():
    """条码查询"""
    pass


@query.command()
@click.argument('barcode')
@click.option('--save/--no-save', default=True, help='保存查询结果')
@click.pass_context
def single(ctx, barcode, save):
    """查询单个条码"""
    qm = ctx.obj['query_manager']
    result = qm.query_single(barcode, save=save)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 查询成功: {barcode}")
            if result.get('view_url'):
                click.echo(f"  查看: http://localhost:5001{result['view_url']}")
        else:
            click.echo(f"✘ 查询失败: {result.get('error')}", err=True)


@query.command()
@click.argument('barcode_file', type=click.Path(exists=True))
@click.option('--background/--foreground', default=True, help='后台模式查询')
@click.pass_context
def batch(ctx, barcode_file, background):
    """批量查询条码（从文件，每行一个条码）"""
    qm = ctx.obj['query_manager']
    result = qm.query_batch(str(barcode_file), background=background)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 批量查询已启动")
            click.echo(f"  模式: {'后台' if background else '前台'}")
            click.echo(f"  源文件: {barcode_file}")
            click.echo(f"\n提示: 使用 'web' 命令启动网页查看结果")
        else:
            click.echo(f"✘ 批量查询失败: {result.get('error')}", err=True)


@query.command()
@click.pass_context
def list(ctx):
    """列出所有查询结果"""
    qm = ctx.obj['query_manager']
    result = qm.list_results()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        click.echo("=" * 60)
        click.echo("  查询结果列表")
        click.echo("=" * 60)
        for item in result.get('barcodes', []):
            archived = " [已归档]" if item.get('archived') else ""
            click.echo(f"  {item['barcode']}{archived} - {item.get('time', 'N/A')}")
        click.echo("=" * 60)
        click.echo(f"  共 {result.get('total', 0)} 条记录")


@query.command()
@click.argument('barcode')
@click.pass_context
def detail(ctx, barcode):
    """查看条码详情"""
    qm = ctx.obj['query_manager']
    result = qm.get_detail(barcode)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        click.echo("=" * 60)
        click.echo(f"  条码详情: {barcode}")
        click.echo("=" * 60)
        if result.get('fields'):
            for sr_key, fields in result['fields'].items():
                if isinstance(fields, dict):
                    click.echo(f"\n  [{sr_key}]")
                    for k, v in fields.items():
                        click.echo(f"    {k}: {v}")
        click.echo("=" * 60)


@cli.group()
def export():
    """导出功能"""
    pass


@export.command()
@click.option('--barcodes', help='逗号分隔的条码列表')
@click.option('--all', 'export_all', is_flag=True, help='导出所有')
@click.option('--output', default='export_result.xlsx', help='输出文件名')
@click.pass_context
def xlsx(ctx, barcodes, export_all, output):
    """导出为 Excel"""
    em = ctx.obj['export_manager']
    barcode_list = barcodes.split(',') if barcodes else []
    result = em.export_xlsx(barcode_list, export_all=export_all, output=output)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 导出成功: {result.get('filename')}")
            click.echo(f"  记录数: {result.get('count', 0)}")
        else:
            click.echo(f"✘ 导出失败: {result.get('error')}", err=True)


@cli.group()
def archive():
    """归档管理"""
    pass


@archive.command()
@click.argument('barcode')
@click.pass_context
def add(ctx, barcode):
    """归档条码"""
    qm = ctx.obj['query_manager']
    result = qm.archive(barcode)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 已归档: {barcode}")
        else:
            click.echo(f"✘ 归档失败: {result.get('error')}", err=True)


@archive.command()
@click.argument('barcode')
@click.pass_context
def remove(ctx, barcode):
    """取消归档"""
    qm = ctx.obj['query_manager']
    result = qm.unarchive(barcode)

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ 已取消归档: {barcode}")
        else:
            click.echo(f"✘ 取消归档失败: {result.get('error')}", err=True)


@archive.command()
@click.pass_context
def list(ctx):
    """列出已归档"""
    qm = ctx.obj['query_manager']
    result = qm.list_archived()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        click.echo("=" * 60)
        click.echo("  已归档条码")
        click.echo("=" * 60)
        for item in result.get('barcodes', []):
            click.echo(f"  {item['barcode']} - {item.get('archiveTime', 'N/A')}")
        click.echo("=" * 60)
        click.echo(f"  共 {result.get('total', 0)} 条")


@cli.command()
@click.pass_context
def web(ctx):
    """启动网页端查看查询结果"""
    qm = ctx.obj['query_manager']
    result = qm.start_web_ui()

    if ctx.obj['output_json']:
        click.echo(format_json(result))
    else:
        if result['success']:
            click.echo(f"✓ {result.get('message')}")
        else:
            click.echo(f"✘ 启动失败: {result.get('error')}", err=True)


@cli.command()
@click.pass_context
def repl(ctx):
    """进入交互式 REPL 模式"""
    print_banner()
    click.echo("  输入命令或 'help' 查看帮助，'exit' 退出")
    click.echo("")

    while True:
        try:
            cmd = input("barcode> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nGoodbye!")
            break

        if not cmd:
            continue
        if cmd in ('exit', 'quit', 'q'):
            click.echo("Goodbye!")
            break
        if cmd == 'help':
            click.echo("可用命令: status, crm login <user> <pw>, query single <barcode>, query list, exit")
            continue

        # Parse and execute
        parts = cmd.split()
        if parts[0] == 'status':
            ctx.invoke(status)
        elif parts[0] == 'crm' and len(parts) >= 3:
            if parts[1] == 'login':
                ctx.invoke(crm_login, username=parts[2], password=parts[3] if len(parts) > 3 else '')
        elif parts[0] == 'query' and len(parts) >= 2:
            if parts[1] == 'list':
                ctx.invoke(query_list)
            elif parts[1] == 'single' and len(parts) >= 3:
                ctx.invoke(query_single, barcode=parts[2])


if __name__ == '__main__':
    cli(obj={})
