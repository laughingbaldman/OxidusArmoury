"""Oxidus web piece. Shared state lives on hub."""
from flask import Flask, render_template, request, jsonify, g
from pathlib import Path
from typing import Optional
import sys
import json
from datetime import datetime, timezone
import os
import shutil
import threading
import time
import subprocess
import random
import re
import importlib
import math
from collections import deque
import yaml
from src.web.hub import hub
from src.utils.memryx_indexing import build_index, compile_to_dfp, export_onnx, MemryxIndexingError
from src.utils.memryx_env import apply_memryx_env
from core.oxidus import Oxidus
from utils.thought_stream import ThoughtType
from src.utils.lm_studio_client import LMStudioClient

from flask import Blueprint
bp = Blueprint("admin", __name__)

def admin_page():
    """Serve the admin page."""
    return render_template('admin.html')
hub.admin_page = admin_page

def admin_health_check():
    """Get detailed system health for admin use."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    return jsonify(hub._build_health_report(include_admin=True))
hub.admin_health_check = admin_health_check

def admin_clear_safe_mode():
    """Clear safe mode (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    hub._exit_safe_mode()
    return jsonify({'success': True, 'safe_mode': dict(hub.SAFE_MODE)})
hub.admin_clear_safe_mode = admin_clear_safe_mode

def admin_telemetry_recent():
    """Fetch recent admin telemetry entries."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        limit = int(request.args.get('limit', 60))
    except ValueError:
        limit = 60
    limit = max(1, min(limit, 200))
    if not hub.TELEMETRY_LOG.exists():
        return jsonify({'entries': []})
    entries = []
    try:
        with open(hub.TELEMETRY_LOG, 'r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return (jsonify({'error': 'Unable to read telemetry log'}), 500)
    return jsonify({'entries': entries[-limit:]})
hub.admin_telemetry_recent = admin_telemetry_recent

def admin_validate_e2e():
    """Run end-to-end validation checks (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    result = hub._run_e2e_validation()
    hub._log_telemetry('e2e_validation', {'success': result.get('success')})
    return jsonify(result)
hub.admin_validate_e2e = admin_validate_e2e

def ops_mode():
    """Get or set operations mode (chat, study, crawl)."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if request.method == 'GET':
        return jsonify({'mode': hub.OPS_MODE.get('mode'), 'crawl_strategy': hub.OPS_MODE.get('crawl_strategy'), 'seed_strategy': hub.OPS_MODE.get('seed_strategy'), 'last_changed_at': hub.OPS_MODE.get('last_changed_at'), 'study_status': hub.STUDY_STATUS})
    payload = request.json or {}
    mode = (payload.get('mode') or 'chat').strip().lower()
    if mode not in {'chat', 'study', 'crawl'}:
        return (jsonify({'error': 'Invalid mode'}), 400)
    if hub.SAFE_MODE.get('active') and mode == 'crawl':
        return (jsonify({'error': 'Safe mode enabled. Crawl mode blocked.'}), 423)
    hub.OPS_MODE['mode'] = mode
    hub.OPS_MODE['crawl_strategy'] = (payload.get('crawl_strategy') or hub.OPS_MODE['crawl_strategy']).strip().lower()
    hub.OPS_MODE['seed_strategy'] = (payload.get('seed_strategy') or hub.OPS_MODE['seed_strategy']).strip().lower()
    hub.OPS_MODE['last_changed_at'] = datetime.now().isoformat()
    hub._log_telemetry('ops_mode_set', {'mode': hub.OPS_MODE['mode'], 'crawl_strategy': hub.OPS_MODE['crawl_strategy'], 'seed_strategy': hub.OPS_MODE['seed_strategy']})
    result = {'success': True, 'mode': hub.OPS_MODE['mode']}
    if mode == 'study':
        result['study'] = hub._run_study_sweep()
    elif mode == 'crawl':
        domains = payload.get('domains') or []
        max_depth = int(payload.get('max_depth', 2))
        max_pages = payload.get('max_pages')
        if max_pages is not None:
            max_pages = int(max_pages)
        if domains:
            crawl_result = hub.oxidus.start_wiki_crawl(domains=domains, max_depth=max_depth, max_pages=max_pages, strategy=hub.OPS_MODE['crawl_strategy'], seed_strategy=hub.OPS_MODE['seed_strategy'])
            result['crawl'] = crawl_result
        else:
            result['crawl'] = {'success': False, 'error': 'No domains provided'}
    return jsonify(result)
hub.ops_mode = ops_mode

def admin_status():
    """Get admin status overview."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    health_report = hub._build_health_report(include_admin=True)
    crawler = hub.oxidus.wiki_crawl_status()
    try:
        health = (crawler or {}).get('health') or {}
        last_restart = health.get('last_watchdog_restart_at')
        global LAST_WATCHDOG_RESTART_AT
        if last_restart and last_restart != LAST_WATCHDOG_RESTART_AT:
            hub._log_telemetry('watchdog_restarted', {'restarts': health.get('watchdog_restart_count'), 'last_watchdog_restart_at': last_restart})
            LAST_WATCHDOG_RESTART_AT = last_restart
    except Exception:
        pass
    lm_status = None
    try:
        from src.utils.lm_studio_client import get_lm_studio_client
        client = get_lm_studio_client()
        lm_status = {'available': client.is_available(), 'model': client.model, 'primary_model': client.primary_model, 'secondary_model': client.secondary_model}
    except Exception:
        lm_status = {'available': False}
    knowledge_sources = 0
    knowledge_loose_ends = {}
    if hub.oxidus.knowledge_organizer:
        knowledge_sources = len(hub.oxidus.knowledge_organizer.scraped_sources)
        try:
            knowledge_loose_ends = hub.oxidus.knowledge_organizer.get_loose_ends_report()
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'get_loose_ends', 'error_type': type(exc).__name__})
            knowledge_loose_ends = {}
    return jsonify({'chat_style': hub.oxidus.get_chat_style(), 'autonomy_enabled': bool(getattr(hub.oxidus, 'autonomous_thinking_enabled', False)), 'crawler': crawler, 'lm_studio': lm_status, 'knowledge_sources': knowledge_sources, 'knowledge': {'loose_ends': knowledge_loose_ends}, 'conversation_messages': len(hub.conversation_history), 'last_maintenance': hub.LAST_MAINTENANCE_RESULT, 'memryx': hub.get_memryx_status(), 'indexing': health_report.get('indexing') or {}, 'safe_mode': health_report.get('safe_mode'), 'alerts': health_report.get('alerts') or []})
hub.admin_status = admin_status

def ops_summary():
    """Get streamlined operations summary for the main UI."""
    try:
        if not hub.oxidus:
            return jsonify({'error': 'Oxidus not initialized'})
        summary = hub._get_cached(hub._cache_key('ops_summary'), 3.0, lambda: hub._build_ops_summary(include_admin=False))
        return jsonify(summary)
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'ops_summary', 'error_type': type(exc).__name__})
        return (jsonify({'error': 'Failed to retrieve operations summary'}), 500)
hub.ops_summary = ops_summary

def admin_ops_summary():
    """Get streamlined operations summary for the admin UI."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    summary = hub._get_cached(hub._cache_key('admin_ops_summary'), 4.0, lambda: hub._build_ops_summary(include_admin=True))
    return jsonify(summary)
hub.admin_ops_summary = admin_ops_summary

def admin_integrity():
    """Run integrity checks for scripts, directories, and components."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    report = hub.build_integrity_report()
    return jsonify(report)
hub.admin_integrity = admin_integrity

def admin_clear_conversation():
    """Clear conversation via admin control."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    return hub.clear_conversation()
hub.admin_clear_conversation = admin_clear_conversation

def admin_conversation_log():
    """Get recent conversation entries for admin review."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        limit = int(request.args.get('limit', 80))
    except ValueError:
        limit = 80
    limit = max(1, min(limit, 300))
    entries = hub.conversation_history[-limit:]
    formatted = []
    for entry in entries:
        formatted.append({'speaker': entry.get('speaker', 'UNKNOWN'), 'message': entry.get('message', ''), 'timestamp': entry.get('timestamp'), 'documented': bool(entry.get('documented'))})
    return jsonify({'count': len(formatted), 'entries': formatted})
hub.admin_conversation_log = admin_conversation_log

def admin_stop_autonomy():
    """Stop Oxidus's autonomous thinking loop (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    try:
        hub.oxidus.autonomous_thinking_enabled = False
        t = getattr(hub.oxidus, 'autonomous_thread', None)
        if t and t.is_alive():
            try:
                t.join(timeout=1)
            except Exception:
                pass
        hub.oxidus.thought_stream.add_thought(ThoughtType.DECISION, 'Autonomy disabled by admin')
        hub._log_telemetry('autonomy_disabled')
        return jsonify({'success': True, 'message': 'Autonomous thinking disabled'})
    except Exception as exc:
        return hub._handle_api_error(exc, 'stop_autonomy')
hub.admin_stop_autonomy = admin_stop_autonomy

def admin_reset_knowledge():
    """Reset organized knowledge and wipe wiki corpus on disk."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_reset_knowledge')
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    result = hub.oxidus.reset_knowledge_organizer()
    corpus_dir = hub.PROJECT_ROOT / 'data' / 'knowledge_base' / 'wiki_corpus'
    if corpus_dir.exists():
        for entry in corpus_dir.iterdir():
            try:
                if entry.is_file():
                    entry.unlink()
                elif entry.is_dir():
                    shutil.rmtree(entry)
            except Exception:
                pass
    hub._log_telemetry('knowledge_reset')
    return jsonify(result)
hub.admin_reset_knowledge = admin_reset_knowledge

def admin_knowledge_dedupe():
    """Dedupe and reindex knowledge sources (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_knowledge_dedupe')
    if guard:
        return guard
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return (jsonify({'success': False, 'error': 'Knowledge organizer not available'}), 400)
    try:
        result = hub.oxidus.knowledge_organizer.dedupe_sources()
    except Exception as exc:
        return hub._handle_api_error(exc, 'deduplicate_sources')
    hub._log_telemetry('knowledge_dedupe', {'removed_duplicates': result.get('removed_duplicates'), 'total_sources': result.get('total_sources')})
    return jsonify({'success': True, 'result': result})
hub.admin_knowledge_dedupe = admin_knowledge_dedupe

def admin_knowledge_rebuild_dedupe():
    """Rebuild knowledge index from disk, then dedupe/reindex (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_knowledge_rebuild_dedupe')
    if guard:
        return guard
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return (jsonify({'success': False, 'error': 'Knowledge organizer not available'}), 400)
    rebuild_result = {}
    try:
        rebuild_result = hub.oxidus.rebuild_knowledge_index()
    except Exception as exc:
        return hub._handle_api_error(exc, 'rebuild_knowledge_index')
    try:
        dedupe_result = hub.oxidus.knowledge_organizer.dedupe_sources()
    except Exception as exc:
        return hub._handle_api_error(exc, 'dedupe_sources')
    hub._log_telemetry('knowledge_rebuild_dedupe', {'rebuild': rebuild_result, 'dedupe': dedupe_result})
    return jsonify({'success': True, 'rebuild': rebuild_result, 'dedupe': dedupe_result})
hub.admin_knowledge_rebuild_dedupe = admin_knowledge_rebuild_dedupe

def admin_notes():
    """Create or list structured notes (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return jsonify({'error': 'Knowledge organizer not available'})
    if request.method == 'GET':
        status = request.args.get('status')
        return jsonify({'notes': hub.oxidus.knowledge_organizer.list_notes(status=status)})
    data = request.json or {}
    note = hub.oxidus.knowledge_organizer.add_note(topic=data.get('topic', ''), summary=data.get('summary', ''), questions=data.get('questions') or [], action_items=data.get('action_items') or [], sources=data.get('sources') or [], status=data.get('status', 'open'))
    return jsonify({'success': True, 'note': note})
hub.admin_notes = admin_notes

def admin_review_note():
    """Mark a note as reviewed (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return jsonify({'error': 'Knowledge organizer not available'})
    data = request.json or {}
    note_id = data.get('note_id')
    if not note_id:
        return (jsonify({'error': 'note_id required'}), 400)
    note = hub.oxidus.knowledge_organizer.review_note(note_id)
    if not note:
        return (jsonify({'error': 'Note not found'}), 404)
    return jsonify({'success': True, 'note': note})
hub.admin_review_note = admin_review_note

def admin_knowledge_config():
    """Get or update knowledge relevance/retention settings (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return jsonify({'error': 'Knowledge organizer not available'})
    if request.method == 'GET':
        return jsonify({'relevance': hub.oxidus.knowledge_organizer.relevance, 'retention': hub.oxidus.knowledge_organizer.retention, 'coverage_targets': hub.oxidus.knowledge_organizer.coverage_targets, 'quality_thresholds': hub.oxidus.knowledge_organizer.quality_thresholds})
    data = request.json or {}
    guard = hub._block_if_safe_mode('admin_knowledge_config_update')
    if guard:
        return guard
    relevance = data.get('relevance', {})
    retention = data.get('retention', {})
    coverage_targets = data.get('coverage_targets')
    quality_thresholds = data.get('quality_thresholds')
    updated_relevance = hub.oxidus.knowledge_organizer.set_relevance_config(min_content_len=relevance.get('min_content_len'), min_concepts=relevance.get('min_concepts'), min_value_hits=relevance.get('min_value_hits'), strict=relevance.get('strict'), lm_gate_enabled=relevance.get('lm_gate_enabled'), lm_gate_min_score=relevance.get('lm_gate_min_score'), lm_gate_required=relevance.get('lm_gate_required'))
    updated_retention = hub.oxidus.knowledge_organizer.set_retention_config(interval_days=retention.get('interval_days'))
    updated_targets = hub.oxidus.knowledge_organizer.set_coverage_targets(coverage_targets)
    updated_thresholds = hub.oxidus.knowledge_organizer.set_quality_thresholds(quality_thresholds)
    return jsonify({'success': True, 'relevance': updated_relevance, 'retention': updated_retention, 'coverage_targets': updated_targets, 'quality_thresholds': updated_thresholds})
hub.admin_knowledge_config = admin_knowledge_config

def admin_learning_trace():
    """Return the most recent learning trace from ingestion."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        if not hub.oxidus or not hub.oxidus.knowledge_organizer:
            return (jsonify({'success': False, 'error': 'Knowledge organizer not available'}), 400)
        trace = hub.oxidus.knowledge_organizer.get_learning_trace() or {}
        return jsonify({'success': True, 'trace': trace})
    except Exception as exc:
        return hub._handle_api_error(exc, 'learning_trace')
hub.admin_learning_trace = admin_learning_trace

def admin_moltbook_ingest():
    """Trigger a manual Moltbook ingest cycle."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_moltbook_ingest')
    if guard:
        return guard
    if not hub.oxidus:
        return (jsonify({'success': False, 'error': 'Oxidus not initialized'}), 400)
    if not getattr(hub.oxidus, 'moltbook', None):
        return (jsonify({'success': False, 'error': 'Moltbook client not available'}), 400)
    if not getattr(hub.oxidus, 'knowledge_organizer', None):
        return (jsonify({'success': False, 'error': 'Knowledge organizer not available'}), 400)
    try:
        hub.oxidus._moltbook_ingest_once()
    except Exception as exc:
        hub._enter_safe_mode(f'Moltbook ingest error: {type(exc).__name__}', 'moltbook_ingest')
        return hub._handle_api_error(exc, 'moltbook_ingest')
    return jsonify({'success': True, 'message': 'Moltbook ingest complete.'})
hub.admin_moltbook_ingest = admin_moltbook_ingest

def admin_rebuild_knowledge():
    """Rebuild knowledge index from disk (texts -> organizer -> memory index)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_rebuild_knowledge')
    if guard:
        return guard
    try:
        if not hub.oxidus:
            return jsonify({'error': 'Oxidus not initialized'})
        result = hub.oxidus.rebuild_knowledge_index()
        hub._log_telemetry('knowledge_rebuild')
        hub._invalidate_cache('ops_summary')
        hub._invalidate_cache('admin_ops_summary')
        hub._warm_ops_cache()
        return jsonify(result)
    except Exception as exc:
        return hub._handle_api_error(exc, 'rebuild_knowledge')
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    result = {}
    try:
        result = hub.oxidus.rebuild_knowledge_index()
    except Exception as exc:
        return hub._handle_api_error(exc, 'rebuild_knowledge')
    if result.get('success'):
        hub._invalidate_cache('ops_summary')
        hub._invalidate_cache('admin_ops_summary')
        hub._invalidate_cache('tiering_overview')
        hub._warm_ops_cache()
    return jsonify(result)
hub.admin_rebuild_knowledge = admin_rebuild_knowledge

def admin_cleanup_external_sources():
    """Cleanup external scraped sources from disk (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_cleanup_external_sources')
    if guard:
        return guard
    try:
        data = request.json or {}
        include_retired = bool(data.get('include_retired', False))
        dry_run = bool(data.get('dry_run', True))
        confirm = bool(data.get('confirm', False))
        if not dry_run and (not confirm):
            return (jsonify({'success': False, 'error': 'Confirmation required for destructive cleanup'}), 400)
        result = hub.cleanup_external_sources(include_retired=include_retired, dry_run=dry_run)
        result['success'] = True
        return jsonify(result)
    except Exception as exc:
        return hub._handle_api_error(exc, 'cleanup_external')
hub.admin_cleanup_external_sources = admin_cleanup_external_sources

def admin_run_maintenance():
    """Run maintenance tasks (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_run_maintenance')
    if guard:
        return guard
    data = request.json or {}
    task = data.get('task')
    result = hub.run_maintenance_task(task, data)
    if result.get('success'):
        hub._invalidate_cache('ops_summary')
        hub._invalidate_cache('admin_ops_summary')
        hub._invalidate_cache('tiering_overview')
        hub._warm_ops_cache()
    status = 200 if result.get('success') else 400
    return (jsonify(result), status)
hub.admin_run_maintenance = admin_run_maintenance

def admin_memryx_benchmark():
    """Run MemryX benchmark hello to verify devices."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    memryx_root = Path(os.environ.get('MEMRYX_HOME', 'C:/Program Files/MemryX'))
    mx_bench_path = shutil.which('mx_bench')
    accl_bench_cmd = None
    if sys.platform == 'win32':
        candidate = memryx_root / 'acclBench' / 'acclBench.exe'
        accl_bench_cmd = str(candidate) if candidate.exists() else None
    else:
        accl_bench_cmd = shutil.which('acclBench')
    if mx_bench_path:
        cmd = [mx_bench_path, '--hello']
    elif accl_bench_cmd:
        cmd = [accl_bench_cmd, '--hello']
    else:
        return (jsonify({'success': False, 'error': 'mx_bench or acclBench not found'}), 400)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return jsonify({'success': result.returncode == 0, 'stdout': result.stdout, 'stderr': result.stderr, 'returncode': result.returncode})
    except FileNotFoundError:
        return (jsonify({'success': False, 'error': 'mx_bench/acclBench not found'}), 400)
    except Exception as exc:
        return hub._handle_api_error(exc, 'memryx_benchmark')
hub.admin_memryx_benchmark = admin_memryx_benchmark

def admin_memryx_health():
    """Get MemryX health diagnostics for the admin UI."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    return jsonify(hub._build_memryx_health_report())
hub.admin_memryx_health = admin_memryx_health

def admin_hybrid_status():
    """Get hybrid compile/runtime status (WSL compile + Windows runtime)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    return jsonify(hub._hybrid_status())
hub.admin_hybrid_status = admin_hybrid_status

def admin_search_overview():
    """Get hybrid search observability snapshot (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    snapshot = hub._build_search_observability()
    hub._log_telemetry('search_overview', {'index_entries': snapshot.get('index_entries', 0)})
    return jsonify(snapshot)
hub.admin_search_overview = admin_search_overview

def admin_access_heat():
    """Get access heat snapshot for tiering decisions (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        limit = int(request.args.get('limit', 50))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 200))
    snapshot = hub._build_access_heat_snapshot(limit=limit)
    hub._log_telemetry('access_heat_snapshot', {'limit': limit})
    return jsonify(snapshot)
hub.admin_access_heat = admin_access_heat

def admin_cache_clear():
    """Clear ops caches (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    data = request.json or {}
    prefix = (data.get('prefix') or '').strip() or None
    hub._invalidate_cache(prefix)
    hub._log_telemetry('cache_clear', {'prefix': prefix})
    return jsonify({'success': True, 'prefix': prefix})
hub.admin_cache_clear = admin_cache_clear

def admin_cache_warm():
    """Warm ops caches (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    hub._warm_ops_cache()
    hub._log_telemetry('cache_warm')
    return jsonify({'success': True})
hub.admin_cache_warm = admin_cache_warm

def admin_tiering_overview():
    """Get tiering overview and candidate movements (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        limit = int(request.args.get('limit', 200))
    except ValueError:
        limit = 200
    limit = max(10, min(limit, 500))
    overview = hub._build_tiering_overview(max_candidates=limit)
    hub._log_telemetry('tiering_overview', {'limit': limit})
    return jsonify(overview)
hub.admin_tiering_overview = admin_tiering_overview

def admin_archive_preview():
    """Preview archival candidates based on retention policy (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        limit = int(request.args.get('limit', 200))
    except ValueError:
        limit = 200
    limit = max(10, min(limit, 500))
    candidates = hub._compute_archival_candidates(max_candidates=limit)
    hub._log_telemetry('archive_preview', {'limit': limit, 'count': len(candidates)})
    return jsonify({'candidates': candidates})
hub.admin_archive_preview = admin_archive_preview

def admin_archive_execute():
    """Archive low-value content to cold storage (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    data = request.json or {}
    confirm = bool(data.get('confirm', False))
    dry_run = bool(data.get('dry_run', not confirm))
    limit = int(data.get('limit') or 200)
    limit = max(1, min(limit, 500))
    candidates = hub._compute_archival_candidates(max_candidates=limit)
    if not candidates:
        return jsonify({'success': True, 'dry_run': dry_run, 'moved': [], 'skipped': []})
    result = hub._archive_paths([item['path'] for item in candidates], dry_run=dry_run)
    hub._log_telemetry('archive_execute', {'dry_run': dry_run, 'count': len(result.get('moved', []))})
    if not dry_run:
        hub._invalidate_cache('ops_summary')
        hub._invalidate_cache('admin_ops_summary')
        hub._warm_ops_cache()
    return jsonify({'success': True, **result})
hub.admin_archive_execute = admin_archive_execute

def admin_archive_manifest():
    """Return archive manifest entries (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    payload = hub._load_archive_manifest()
    return jsonify(payload)
hub.admin_archive_manifest = admin_archive_manifest

def admin_archive_restore():
    """Restore an archived item back to the knowledge base (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    data = request.json or {}
    rel_path = (data.get('path') or '').strip()
    if not rel_path:
        return (jsonify({'success': False, 'error': 'path required'}), 400)
    policy = hub._load_archival_policy()
    archive_root = Path(policy.get('archive_root') or '').expanduser()
    if not archive_root.is_absolute():
        archive_root = (hub.PROJECT_ROOT / archive_root).resolve()
    kb_root = hub._knowledge_base_root().resolve()
    source = hub._resolve_relative_path_under_base(rel_path, archive_root)
    if not source:
        return (jsonify({'success': False, 'error': 'path not allowed'}), 403)
    normalized_rel_path = source.relative_to(archive_root).as_posix()
    dest = hub._resolve_relative_path_under_base(normalized_rel_path, kb_root)
    if not dest:
        return (jsonify({'success': False, 'error': 'path not allowed'}), 403)
    if not source.exists():
        return (jsonify({'success': False, 'error': 'archived file not found'}), 404)
    if dest.exists():
        return (jsonify({'success': False, 'error': 'destination already exists'}), 409)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    manifest = hub._load_archive_manifest()
    entries = manifest.get('entries') or []
    manifest['entries'] = [entry for entry in entries if entry.get('archived_relative') != normalized_rel_path]
    hub._save_archive_manifest(manifest)
    hub._log_telemetry('archive_restore', {'path': normalized_rel_path})
    hub._invalidate_cache('ops_summary')
    hub._invalidate_cache('admin_ops_summary')
    hub._warm_ops_cache()
    return jsonify({'success': True, 'path': normalized_rel_path})
hub.admin_archive_restore = admin_archive_restore

def admin_memryx_devices():
    """List MX3 device IDs using runtime API when available."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    return jsonify(hub.get_memryx_devices())
hub.admin_memryx_devices = admin_memryx_devices

def admin_start_indexing():
    """Start MemryX indexing pipeline in background."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('admin_start_indexing')
    if guard:
        return guard
    if hub.INDEXING_STATUS.get('running'):
        return (jsonify({'success': False, 'error': 'Indexing already running'}), 409)
    payload = request.json or {}
    thread = threading.Thread(target=hub._run_memryx_indexing, args=(payload,), daemon=True)
    thread.start()
    return jsonify({'success': True, 'message': 'Indexing started'})
hub.admin_start_indexing = admin_start_indexing

def admin_indexing_status():
    """Get MemryX indexing status."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    try:
        if hub.MEMRYX_TUNED_ONNX and hub.MEMRYX_TUNED_ONNX.exists():
            default_onnx = hub.MEMRYX_TUNED_ONNX
            default_dfp = hub.MEMRYX_TUNED_DFP
            default_model = str(hub.MEMRYX_TUNED_DIR)
        else:
            default_onnx = hub.MEMRYX_ONNX_PATH
            default_dfp = hub.MEMRYX_DFP_PATH
            default_model = hub.MEMRYX_MODEL_ID
        default_device_ids = hub.get_memryx_devices().get('device_ids') or []
        default_num_chips = max(len(default_device_ids), 1)
        index_meta = hub._load_index_meta()
        staleness = hub._compute_index_staleness(index_meta)
        return jsonify({'running': hub.INDEXING_STATUS.get('running', False), 'last_error': hub.INDEXING_STATUS.get('last_error'), 'last_result': hub.INDEXING_STATUS.get('last_result'), 'processed_batches': hub.INDEXING_STATUS.get('processed_batches', 0), 'total_batches': hub.INDEXING_STATUS.get('total_batches', 0), 'throughput_eps': hub.INDEXING_STATUS.get('throughput_eps', 0.0), 'started_at': hub.INDEXING_STATUS.get('started_at'), 'avg_batch_ms': hub.INDEXING_STATUS.get('avg_batch_ms', 0.0), 'device_ids': hub.INDEXING_STATUS.get('device_ids', []), 'priority_paths': hub.INDEXING_STATUS.get('priority_paths', []), 'batch_delay_ms': hub.INDEXING_STATUS.get('batch_delay_ms', 0), 'index_meta': index_meta, 'index_staleness_hours': staleness.get('staleness_hours'), 'index_last_built_utc': staleness.get('indexed_at_utc'), 'model_id': default_model, 'onnx_path': str(default_onnx) if default_onnx else None, 'dfp_path': str(default_dfp) if default_dfp else None, 'output_dir': str(hub.MEMRYX_INDEX_DIR) if hub.MEMRYX_INDEX_DIR else None, 'defaults': {'batch_size': 48, 'max_tokens': 192, 'num_chips': default_num_chips, 'device_ids': default_device_ids, 'prefer_async': True, 'batch_delay_ms': 0}})
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'indexing_status', 'error_type': type(exc).__name__})
        return (jsonify({'running': False, 'last_error': 'Failed to retrieve indexing status', 'last_result': None, 'processed_batches': 0, 'total_batches': 0, 'throughput_eps': 0.0, 'started_at': None, 'avg_batch_ms': 0.0, 'device_ids': [], 'priority_paths': [], 'batch_delay_ms': 0, 'index_meta': {}, 'index_staleness_hours': None, 'index_last_built_utc': None, 'model_id': None, 'onnx_path': None, 'dfp_path': None, 'output_dir': None, 'defaults': {}}), 200)
hub.admin_indexing_status = admin_indexing_status

def admin_indexing_metrics():
    """Get concise indexing metrics (admin only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    metrics = hub._build_indexing_metrics()
    hub._log_telemetry('indexing_metrics', {'running': metrics.get('running')})
    return jsonify(metrics)
hub.admin_indexing_metrics = admin_indexing_metrics

def admin_shutdown():
    """Shutdown the Flask server (dev server only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    shutdown_func = request.environ.get('werkzeug.server.shutdown')
    if shutdown_func is None:
        return jsonify({'success': False, 'error': 'Shutdown not available'})
    shutdown_func()
    return jsonify({'success': True})
hub.admin_shutdown = admin_shutdown

def admin_restart():
    """Restart the Flask server (dev server only)."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'success': False, 'error': 'Oxidus not initialized'})
    shutdown_func = request.environ.get('werkzeug.server.shutdown')

    def _restart():
        hub.save_conversation()
        time.sleep(0.5)
        try:
            subprocess.Popen([sys.executable, str(hub.ENTRY_SCRIPT)])
        except Exception:
            pass
        if shutdown_func:
            shutdown_func()
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({'success': True})
hub.admin_restart = admin_restart

def register(app):
    bp.add_url_rule('/admin', 'admin_page', hub.admin_page, methods=['GET'])
    bp.add_url_rule('/api/admin/health', 'admin_health_check', hub.admin_health_check, methods=['GET'])
    bp.add_url_rule('/api/admin/safe-mode/clear', 'admin_clear_safe_mode', hub.admin_clear_safe_mode, methods=['POST'])
    bp.add_url_rule('/api/admin/telemetry/recent', 'admin_telemetry_recent', hub.admin_telemetry_recent, methods=['GET'])
    bp.add_url_rule('/api/admin/validate-e2e', 'admin_validate_e2e', hub.admin_validate_e2e, methods=['POST'])
    bp.add_url_rule('/api/ops/mode', 'ops_mode', hub.ops_mode, methods=['GET', 'POST'])
    bp.add_url_rule('/api/admin/status', 'admin_status', hub.admin_status, methods=['GET'])
    bp.add_url_rule('/api/ops/summary', 'ops_summary', hub.ops_summary, methods=['GET'])
    bp.add_url_rule('/api/admin/ops/summary', 'admin_ops_summary', hub.admin_ops_summary, methods=['GET'])
    bp.add_url_rule('/api/admin/integrity', 'admin_integrity', hub.admin_integrity, methods=['GET'])
    bp.add_url_rule('/api/admin/clear-conversation', 'admin_clear_conversation', hub.admin_clear_conversation, methods=['POST'])
    bp.add_url_rule('/api/admin/conversation', 'admin_conversation_log', hub.admin_conversation_log, methods=['GET'])
    bp.add_url_rule('/api/admin/stop-autonomy', 'admin_stop_autonomy', hub.admin_stop_autonomy, methods=['POST'])
    bp.add_url_rule('/api/admin/reset-knowledge', 'admin_reset_knowledge', hub.admin_reset_knowledge, methods=['POST'])
    bp.add_url_rule('/api/admin/knowledge/dedupe', 'admin_knowledge_dedupe', hub.admin_knowledge_dedupe, methods=['POST'])
    bp.add_url_rule('/api/admin/knowledge/rebuild-dedupe', 'admin_knowledge_rebuild_dedupe', hub.admin_knowledge_rebuild_dedupe, methods=['POST'])
    bp.add_url_rule('/api/admin/notes', 'admin_notes', hub.admin_notes, methods=['GET', 'POST'])
    bp.add_url_rule('/api/admin/notes/review', 'admin_review_note', hub.admin_review_note, methods=['POST'])
    bp.add_url_rule('/api/admin/knowledge-config', 'admin_knowledge_config', hub.admin_knowledge_config, methods=['GET', 'POST'])
    bp.add_url_rule('/api/admin/learning-trace', 'admin_learning_trace', hub.admin_learning_trace, methods=['GET'])
    bp.add_url_rule('/api/admin/moltbook/ingest', 'admin_moltbook_ingest', hub.admin_moltbook_ingest, methods=['POST'])
    bp.add_url_rule('/api/admin/rebuild-knowledge', 'admin_rebuild_knowledge', hub.admin_rebuild_knowledge, methods=['POST'])
    bp.add_url_rule('/api/admin/cleanup-external', 'admin_cleanup_external_sources', hub.admin_cleanup_external_sources, methods=['POST'])
    bp.add_url_rule('/api/admin/maintenance/run', 'admin_run_maintenance', hub.admin_run_maintenance, methods=['POST'])
    bp.add_url_rule('/api/admin/memryx/benchmark', 'admin_memryx_benchmark', hub.admin_memryx_benchmark, methods=['POST'])
    bp.add_url_rule('/api/admin/memryx/health', 'admin_memryx_health', hub.admin_memryx_health, methods=['GET'])
    bp.add_url_rule('/api/admin/hybrid-status', 'admin_hybrid_status', hub.admin_hybrid_status, methods=['GET'])
    bp.add_url_rule('/api/admin/search/overview', 'admin_search_overview', hub.admin_search_overview, methods=['GET'])
    bp.add_url_rule('/api/admin/access-heat', 'admin_access_heat', hub.admin_access_heat, methods=['GET'])
    bp.add_url_rule('/api/admin/cache/clear', 'admin_cache_clear', hub.admin_cache_clear, methods=['POST'])
    bp.add_url_rule('/api/admin/cache/warm', 'admin_cache_warm', hub.admin_cache_warm, methods=['POST'])
    bp.add_url_rule('/api/admin/tiering/overview', 'admin_tiering_overview', hub.admin_tiering_overview, methods=['GET'])
    bp.add_url_rule('/api/admin/archive/preview', 'admin_archive_preview', hub.admin_archive_preview, methods=['GET'])
    bp.add_url_rule('/api/admin/archive/execute', 'admin_archive_execute', hub.admin_archive_execute, methods=['POST'])
    bp.add_url_rule('/api/admin/archive/manifest', 'admin_archive_manifest', hub.admin_archive_manifest, methods=['GET'])
    bp.add_url_rule('/api/admin/archive/restore', 'admin_archive_restore', hub.admin_archive_restore, methods=['POST'])
    bp.add_url_rule('/api/admin/memryx/devices', 'admin_memryx_devices', hub.admin_memryx_devices, methods=['GET'])
    bp.add_url_rule('/api/admin/indexing/start', 'admin_start_indexing', hub.admin_start_indexing, methods=['POST'])
    bp.add_url_rule('/api/admin/indexing/status', 'admin_indexing_status', hub.admin_indexing_status, methods=['GET'])
    bp.add_url_rule('/api/admin/indexing/metrics', 'admin_indexing_metrics', hub.admin_indexing_metrics, methods=['GET'])
    bp.add_url_rule('/api/admin/shutdown', 'admin_shutdown', hub.admin_shutdown, methods=['POST'])
    bp.add_url_rule('/api/admin/restart', 'admin_restart', hub.admin_restart, methods=['POST'])
    app.register_blueprint(bp)
