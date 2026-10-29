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

def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
hub._utc_iso = _utc_iso

def _file_summary(path: Path) -> dict:
    if not path.exists():
        return {'exists': False}
    stat = path.stat()
    return {'exists': True, 'size_bytes': stat.st_size, 'modified_utc': hub._utc_iso(stat.st_mtime)}
hub._file_summary = _file_summary

def _count_taxonomy_domains(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return 0
    return len(re.findall('^\\s*-\\s*id:\\s*\\"domain-', text, re.MULTILINE))
hub._count_taxonomy_domains = _count_taxonomy_domains

def _load_kb_governance_summary() -> dict:
    base_dir = hub.PROJECT_ROOT / 'data' / 'knowledge_base'
    metadata_dir = base_dir / 'metadata'
    index_path = metadata_dir / 'indexes' / 'wiki_file_topic_index.json'
    taxonomy_path = base_dir / 'classification' / 'topic_taxonomy.yaml'
    topic_registry = hub._safe_load_json(metadata_dir / 'topic_registry.json') or {}
    source_registry = hub._safe_load_json(metadata_dir / 'source_registry.json') or {}
    synonyms = hub._safe_load_json(metadata_dir / 'topic_synonyms.json') or {}
    mappings = hub._safe_load_json(metadata_dir / 'topic_to_domain.json') or {}
    edges = hub._safe_load_json(metadata_dir / 'graph_edges.json') or {}
    dedupe = hub._safe_load_json(metadata_dir / 'dedupe_registry.json') or {}
    curriculum = hub._safe_load_json(metadata_dir / 'curriculum_index.json') or {}
    index = hub._safe_load_json(index_path) or {}
    index_files = index.get('files') or []
    return {'registries': {'topics': len(topic_registry.get('topics') or []), 'sources': len(source_registry.get('sources') or []), 'synonyms': len((synonyms.get('synonyms') or {}).keys()), 'topic_mappings': len(mappings.get('mappings') or []), 'graph_edges': len(edges.get('edges') or []), 'dedupe_items': len(dedupe.get('items') or []), 'curricula': len(curriculum.get('curricula') or [])}, 'updated': {'topic_registry': topic_registry.get('updated_utc'), 'source_registry': source_registry.get('updated_utc'), 'synonyms': synonyms.get('updated_utc'), 'topic_mappings': mappings.get('updated_utc'), 'graph_edges': edges.get('updated_utc'), 'dedupe_registry': dedupe.get('updated_utc'), 'curriculum_index': curriculum.get('updated_utc')}, 'taxonomy': {'domains': hub._count_taxonomy_domains(taxonomy_path)}, 'index': {'wiki_files': len(index_files), 'updated_utc': index.get('updated_utc'), 'root': index.get('root'), 'file_summary': hub._file_summary(index_path)}, 'files': {'lifecycle_policy': hub._file_summary(metadata_dir / 'lifecycle_policy.md'), 'metadata_schema': hub._file_summary(metadata_dir / 'metadata_schema.json'), 'ingest_rules': hub._file_summary(metadata_dir / 'ingest_rules.yaml')}}
hub._load_kb_governance_summary = _load_kb_governance_summary

def _get_cached(key: str, ttl_seconds: float, compute):
    now = time.time()
    cached = hub.OPS_CACHE.get(key)
    if cached and now - cached.get('ts', 0) < ttl_seconds:
        return cached.get('value')
    value = compute()
    hub.OPS_CACHE[key] = {'ts': now, 'value': value}
    return value
hub._get_cached = _get_cached

def _cache_key(namespace: str, *parts: str) -> str:
    suffix = ':'.join((str(part) for part in parts if part))
    if suffix:
        return f'{namespace}:v{hub.OPS_CACHE_VERSION}:{suffix}'
    return f'{namespace}:v{hub.OPS_CACHE_VERSION}'
hub._cache_key = _cache_key

def _invalidate_cache(prefix: Optional[str]=None) -> None:
    if not prefix:
        hub.OPS_CACHE.clear()
        return
    keys = [key for key in hub.OPS_CACHE.keys() if key.startswith(prefix)]
    for key in keys:
        hub.OPS_CACHE.pop(key, None)
hub._invalidate_cache = _invalidate_cache

def _start_request_timer():
    g.request_start = time.time()
hub._start_request_timer = _start_request_timer

def _maybe_record_alert(code: str, level: str, message: str, meta: Optional[dict]=None) -> None:
    now = time.time()
    ttl = int(hub._load_reliability_slo().get('alert_dedupe_seconds', hub.ALERT_TTL_SECONDS))
    last = hub.ALERT_CACHE.get(code, 0)
    if now - last < ttl:
        return
    hub.ALERT_CACHE[code] = now
    hub._log_telemetry('reliability_alert', {'code': code, 'level': level, 'message': message, 'meta': meta or {}})
hub._maybe_record_alert = _maybe_record_alert

def _maybe_record_snapshot(code: str, event: str, payload: Optional[dict]=None) -> None:
    now = time.time()
    ttl = int(hub._load_reliability_slo().get('alert_dedupe_seconds', hub.ALERT_TTL_SECONDS))
    last = hub.ALERT_CACHE.get(code, 0)
    if now - last < ttl:
        return
    hub.ALERT_CACHE[code] = now
    hub._log_telemetry(event, payload or {})
hub._maybe_record_snapshot = _maybe_record_snapshot

def _run_study_sweep() -> dict:
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return {'success': False, 'error': 'Knowledge organizer not available'}
    organizer = hub.oxidus.knowledge_organizer
    try:
        clusters = organizer.cluster_concepts()
        hierarchy = organizer.build_hierarchy()
        open_threads = organizer.get_open_threads()
        loose_ends = organizer.get_loose_ends_report()
        summary = {'clusters': len(clusters), 'hierarchy_roots': len(hierarchy), 'open_threads': len(open_threads), 'low_coherence_sources': len(loose_ends.get('low_coherence_sources') or []), 'review_queue': len(loose_ends.get('review_queue') or [])}
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'organize_knowledge', 'error_type': type(exc).__name__})
        return {'success': False, 'error': 'Failed to organize knowledge'}
    hub.STUDY_STATUS['last_run_at'] = datetime.now().isoformat()
    hub.STUDY_STATUS['summary'] = summary
    if hub.oxidus and getattr(hub.oxidus, 'thought_stream', None):
        hub.oxidus.thought_stream.add_thought(ThoughtType.REFLECTION, f'Study sweep complete: {summary}')
    return {'success': True, 'summary': summary, 'last_run_at': hub.STUDY_STATUS['last_run_at']}
hub._run_study_sweep = _run_study_sweep

def load_settings() -> dict:
    """Load persisted UI/behavior settings."""
    if hub.SETTINGS_FILE.exists():
        try:
            with open(hub.SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f'Error loading settings: {e}')
    return {}
hub.load_settings = load_settings

def _log_telemetry(event_type: str, payload: Optional[dict]=None) -> None:
    """Append a lightweight telemetry event for admin actions."""
    try:
        hub.TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {'ts': datetime.now().isoformat(), 'event': event_type, 'payload': payload or {}}
        if request:
            entry['remote_addr'] = request.remote_addr
            entry['user_agent'] = request.headers.get('User-Agent')
        with open(hub.TELEMETRY_LOG, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        pass
hub._log_telemetry = _log_telemetry

def _record_access_signal(event: str, topics: Optional[list]=None, paths: Optional[list]=None) -> None:
    if not event:
        return
    topic_list = [topic for topic in topics or [] if topic]
    path_list = [path for path in paths or [] if path]
    now = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    with hub.ACCESS_HEAT_LOCK:
        data = hub._load_access_heat()
        events = data.setdefault('events', {})
        events[event] = int(events.get(event, 0) or 0) + 1
        topics_store = data.setdefault('topics', {})
        for topic in topic_list:
            key = hub._normalize_topic_key(topic)
            if not key:
                continue
            entry = topics_store.setdefault(key, {'count': 0, 'last_access_utc': None})
            entry['count'] = int(entry.get('count', 0) or 0) + 1
            entry['last_access_utc'] = now
        paths_store = data.setdefault('paths', {})
        for path in path_list:
            entry = paths_store.setdefault(path, {'count': 0, 'last_access_utc': None})
            entry['count'] = int(entry.get('count', 0) or 0) + 1
            entry['last_access_utc'] = now
        data['updated_utc'] = now
        hub._save_access_heat(data)
hub._record_access_signal = _record_access_signal

def require_admin_token():
    """Validate admin token for protected endpoints."""
    from src.web.auth import load_admin_token
    expected = load_admin_token(hub.ADMIN_TOKEN_FILE)
    if expected:
        os.environ[hub.ADMIN_TOKEN_ENV] = expected
    if not expected:
        return (jsonify({'error': 'Admin token not configured'}), 503)
    payload = request.get_json(silent=True) or {}
    supplied = request.headers.get('X-Oxidus-Admin-Token') or request.args.get('admin_token') or payload.get('admin_token')
    if not supplied or supplied != expected:
        return (jsonify({'error': 'Unauthorized'}), 401)
    return None
hub.require_admin_token = require_admin_token

def cleanup_external_sources(include_retired: bool=False, dry_run: bool=True) -> dict:
    """Remove external scraped sources from disk."""
    targets = list(hub.EXTERNAL_SOURCE_DIRS)
    if include_retired:
        targets.extend(hub.EXTERNAL_SOURCE_DIRS_RETIRED)
    removed_files = 0
    removed_dirs = 0
    missing_dirs = 0
    for base_dir in targets:
        if not base_dir.exists():
            missing_dirs += 1
            continue
        for entry in base_dir.rglob('*'):
            if entry.is_file():
                removed_files += 1
                if not dry_run:
                    try:
                        entry.unlink()
                    except Exception:
                        pass
        if not dry_run:
            try:
                for child in base_dir.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        removed_dirs += 1
            except Exception:
                pass
    return {'dry_run': dry_run, 'include_retired': include_retired, 'removed_files': removed_files, 'removed_dirs': removed_dirs, 'missing_dirs': missing_dirs, 'targets': [str(p) for p in targets]}
hub.cleanup_external_sources = cleanup_external_sources

def build_integrity_report() -> dict:
    """Check for missing scripts, unregistered scripts, and component availability."""
    base_dir = hub.PROJECT_ROOT
    scripts_dir = base_dir / 'scripts'
    script_files = set()
    if scripts_dir.exists():
        for path in scripts_dir.glob('*.py'):
            script_files.add(path.name)
    task_scripts = {task['script'] for task in hub.MAINTENANCE_TASKS.values()}
    optional_scripts = {'check_lm_studio.py', 'cleanup_external_sources.py', 'lm_probe.py', 'lm_studio_e2e.py', 'lm_studio_retry.py', 'memryx_monitor.py', 'owner_smoke_queries.py', 'train_memryx_embeddings.py'}
    unregistered_scripts = sorted(script_files - task_scripts - optional_scripts)
    missing_scripts = sorted(task_scripts - script_files)
    key_dirs = [base_dir / 'templates', base_dir / 'src', base_dir / 'config', base_dir / 'data', base_dir / 'data' / 'knowledge_base', base_dir / 'data' / 'research']
    dir_status = {str(path.relative_to(base_dir)): path.exists() for path in key_dirs}
    components = {'oxidus_initialized': hub.oxidus is not None, 'knowledge_organizer': bool(hub.oxidus and hub.oxidus.knowledge_organizer), 'memory_index': bool(hub.oxidus and hub.oxidus.memory_index), 'web_research': bool(hub.oxidus and getattr(hub.oxidus, 'web_research', None)), 'wikipedia_api': bool(hub.oxidus and getattr(hub.oxidus, 'wikipedia', None)), 'wiki_crawler': bool(hub.oxidus and getattr(hub.oxidus, 'wikipedia_crawler', None)), 'ai_conversation': bool(hub.oxidus and getattr(hub.oxidus, 'ai_conversation', None))}
    return {'scripts_total': len(script_files), 'tasks_total': len(task_scripts), 'missing_scripts': missing_scripts, 'unregistered_scripts': unregistered_scripts, 'dir_status': dir_status, 'components': components}
hub.build_integrity_report = build_integrity_report

def _latest_thought_stream() -> Optional[str]:
    streams_dir = hub.PROJECT_ROOT / 'data' / 'thought_streams'
    if not streams_dir.exists():
        return None
    candidates = list(streams_dir.glob('*.json'))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return str(latest)
hub._latest_thought_stream = _latest_thought_stream

def init_oxidus():
    """Initialize Oxidus on app startup."""
    hub.load_conversation()
    if hub.oxidus is None:
        hub.oxidus = Oxidus()
        settings = hub.load_settings()
        chat_style = settings.get('chat_style', 'hybrid')
        hub.oxidus.set_chat_style(chat_style)
        if not hub.conversation_history:
            initial_message = {'speaker': 'OXIDUS', 'message': hub.oxidus_greeting(), 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'greeting'}
            hub.conversation_history.append(initial_message)
            hub.save_conversation()
        if os.environ.get('OXIDUS_AUTO_INDEXING', '1').lower() not in {'0', 'false', 'off', 'no'}:
            if hub.SAFE_MODE.get('active'):
                hub._log_telemetry('auto_indexing_skipped', {'reason': hub.SAFE_MODE.get('reason')})
            elif not hub.INDEXING_STATUS.get('running'):
                devices = hub.get_memryx_devices().get('device_ids', [])
                if devices:
                    thread = threading.Thread(target=hub._run_memryx_indexing, args=({},), daemon=True)
                    thread.start()
hub.init_oxidus = init_oxidus

def oxidus_greeting() -> str:
    """Return Oxidus's initial greeting."""
    return hub._build_opening_message()
hub.oxidus_greeting = oxidus_greeting

def _is_breakdown_prompt(message: str) -> bool:
    if not message:
        return False
    lowered = message.strip().lower()
    if lowered.startswith('i can keep going if we break this into smaller questions'):
        return True
    if lowered.startswith('that is a complex question'):
        return True
    return False
hub._is_breakdown_prompt = _is_breakdown_prompt

def _store_answered_exchange(question: str, answer: str) -> bool:
    if not hub.oxidus or not answer or hub._is_breakdown_prompt(answer):
        return False
    topics = hub._extract_query_topics(question, max_items=2)
    note_topic = topics[0] if topics else question[:120].strip()
    follow_ups = hub._build_follow_up_tasks(question, answer, topics)
    summary = f'Q: {question}\nA: {answer}'.strip()
    if len(summary) > 1600:
        summary = summary[:1597].rstrip() + '...'
    if hub.oxidus.knowledge_organizer:
        try:
            hub.oxidus.knowledge_organizer.add_note(topic=note_topic or 'answered_question', summary=summary, questions=[question] if question else [], action_items=follow_ups, sources=['chat_exchange'])
        except Exception:
            return False
    if hub.oxidus.memory_index:
        try:
            memory_topics = hub.oxidus.memory_index.extract_topics(summary) if summary else []
            if memory_topics:
                hub.oxidus.memory_index.add_memory(f'Answered: {question}\n{answer}', memory_type='conversation', topics=memory_topics)
        except Exception:
            return False
    if hub.oxidus.ai_conversation and follow_ups:
        for item in follow_ups:
            hub.oxidus.ai_conversation.add_understanding_gap(item)
    return True
hub._store_answered_exchange = _store_answered_exchange

def _build_follow_up_tasks(question: str, answer: str, topics: list) -> list:
    base = topics[0] if topics else ''
    label = base or 'this topic'
    tasks = [f'Collect 2-3 authoritative sources on {label}', f'Identify key mechanisms and measurable variables for {label}', f'List main risks, failure modes, and constraints for {label}', f'Draft a 5-step validation plan or small test for {label}']
    if question:
        tasks.insert(0, f'Clarify scope: {question[:120].strip()}')
    if answer:
        tasks.append('Extract 3 concise takeaways and compare against sources')
    seen = set()
    deduped = []
    for task in tasks:
        key = task.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(task)
    return deduped[:6]
hub._build_follow_up_tasks = _build_follow_up_tasks

def _build_user_context_line(user_topics: list) -> Optional[str]:
    last_message = hub._latest_user_message()
    if last_message and (not hub._is_trivial_user_message(last_message)):
        snippet = hub._shorten_snippet(last_message)
        return random.choice(hub._USER_CONTEXT_LINES).format(snippet=snippet)
    if user_topics:
        return f'From you: {random.choice(user_topics)}.'
    return None
hub._build_user_context_line = _build_user_context_line

def _fallback_response(user_message: str) -> str:
    topics = hub._extract_query_topics(user_message, max_items=3)
    wiki_matches = hub._find_wiki_corpus_matches(topics, max_items=2)
    if wiki_matches:
        matched_paths = [hub._relative_kb_path(match['path']) for match in wiki_matches]
        hub._record_access_signal('local_kb_fallback', topics=topics, paths=matched_paths)
        lines = ['I do not have a live model response, but I can use local knowledge:']
        for match in wiki_matches:
            snippet = hub._extract_wiki_snippet(match['path'])
            if snippet:
                lines.append(f'- {match['title']}: {snippet}')
            else:
                lines.append(f'- {match['title']}: Local knowledge file found, but it is empty or unreadable.')
        lines.append('If you want a deeper answer, tell me the angle (definition, history, observations, or open questions).')
        return '\n'.join(lines)
    if len(topics) >= 2:
        topic_a, topic_b = (topics[0], topics[1])
        return f'I can cover {topic_a} and {topic_b}, then connect them. I will define each clearly, then map a bridge (shared mechanisms, materials, or constraints). If you want a specific angle (scientific, practical, or historical), tell me.'
    topic = None
    for word in hub._extract_user_topics(max_items=1):
        topic = word
        break
    if topic:
        return f'I can help with {topic}. Here is a practical starting map:\n- Core concepts and definitions\n- Current applications and constraints\n- Key risks and safety boundaries\n- Open questions worth researching\n\nTell me your goal (learn, build, compare, or decide) and I will go deeper.'
    return 'I am here to help. Tell me the area you want to explore, and I will map it, connect the dots, and give you clear next steps.'
hub._fallback_response = _fallback_response

def _run_e2e_validation() -> dict:
    checks = []

    def add_check(name: str, ok: bool, detail: Optional[str]=None):
        checks.append({'check': name, 'ok': ok, 'detail': detail})
    if not hub.oxidus:
        add_check('oxidus_initialized', False, 'Oxidus not initialized')
        return {'success': False, 'checks': checks}
    add_check('oxidus_initialized', True)
    add_check('knowledge_organizer', bool(hub.oxidus.knowledge_organizer))
    add_check('memory_index', bool(hub.oxidus.memory_index))
    add_check('wikipedia_api', bool(getattr(hub.oxidus, 'wikipedia', None)))
    add_check('wiki_crawler', bool(getattr(hub.oxidus, 'wikipedia_crawler', None)))
    try:
        status = hub.oxidus.wiki_crawl_status()
        add_check('wiki_crawl_status', status.get('success', False), status.get('error'))
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'wiki_crawl_status', 'error_type': type(exc).__name__})
        add_check('wiki_crawl_status', False, 'Failed to check wiki crawl status')
    try:
        settings = hub.oxidus.wiki_crawl_settings()
        add_check('wiki_crawl_settings', settings.get('success', False), settings.get('error'))
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'wiki_crawl_settings', 'error_type': type(exc).__name__})
        add_check('wiki_crawl_settings', False, 'Failed to check wiki crawl settings')
    try:
        summary = hub._build_ops_summary(include_admin=False)
        ok = bool(summary.get('knowledge', {}).get('available'))
        add_check('ops_summary', ok, None if ok else 'knowledge summary unavailable')
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'ops_summary', 'error_type': type(exc).__name__})
        add_check('ops_summary', False, 'Failed to check operations summary')
    return {'success': all((item['ok'] for item in checks)), 'checks': checks}
hub._run_e2e_validation = _run_e2e_validation

def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith('Z'):
        raw = raw[:-1] + '+00:00'
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None
hub._parse_iso_utc = _parse_iso_utc

def _days_since(value: Optional[datetime]) -> Optional[float]:
    if not value:
        return None
    try:
        delta = datetime.utcnow() - value.replace(tzinfo=None)
    except Exception:
        return None
    return round(delta.total_seconds() / 86400, 2)
hub._days_since = _days_since

def _iter_kb_files(roots: Optional[list]=None, max_files: Optional[int]=None) -> list:
    kb_root = hub._knowledge_base_root().resolve()
    resolved_roots = []
    if roots:
        for rel in roots:
            if not isinstance(rel, str):
                continue
            constructed = hub._resolve_relative_path_under_base(str(rel), kb_root)
            if constructed and hub._is_path_safe(constructed, kb_root):
                resolved_roots.append(constructed)
    else:
        resolved_roots = [kb_root]
    files = []
    for root in resolved_roots:
        if not root.exists():
            continue
        for ext in ('.json', '.md'):
            for path in root.rglob(f'*{ext}'):
                if not path.is_file():
                    continue
                if not hub._is_path_safe(path, kb_root):
                    continue
                files.append(path)
                if max_files and len(files) >= max_files:
                    return files
    return files
hub._iter_kb_files = _iter_kb_files

def _compute_archival_candidates(max_candidates: Optional[int]=None) -> list:
    retention = hub._load_retention_policy()
    heat = hub._load_access_heat()
    heat_paths = heat.get('paths') or {}
    max_candidates = max_candidates or int(retention.get('max_candidates', 200))
    no_access_days = float(retention.get('no_access_days', 180))
    min_age_days = float(retention.get('min_age_days', 180))
    candidates = []
    for path in hub._iter_kb_files(roots=retention.get('eligible_roots')):
        relative = hub._relative_kb_path(path)
        excluded = any((relative.startswith(prefix) for prefix in retention.get('exclude_paths', [])))
        if excluded:
            continue
        entry = heat_paths.get(relative) or {}
        last_access = hub._parse_iso_utc(entry.get('last_access_utc'))
        access_age_days = hub._days_since(last_access)
        if access_age_days is not None and access_age_days < no_access_days:
            continue
        age_days = hub._days_since(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
        if age_days is not None and age_days < min_age_days:
            continue
        candidates.append({'path': relative, 'access_count': int(entry.get('count', 0) or 0), 'last_access_utc': entry.get('last_access_utc'), 'age_days': age_days})
        if len(candidates) >= max_candidates:
            break
    return candidates
hub._compute_archival_candidates = _compute_archival_candidates

def _hybrid_status() -> dict:
    memryx_root = Path(os.environ.get('MEMRYX_HOME', 'C:/Program Files/MemryX'))
    wsl_enabled = os.environ.get('OXIDUS_WSL_MX', '0').lower() in {'1', 'true', 'yes', 'on'}
    wsl_venv = os.environ.get('OXIDUS_WSL_MX_VENV', '$HOME/mx')
    wsl_available = False
    wsl_mx_nc = None
    if sys.platform == 'win32' and wsl_enabled:
        try:
            result = subprocess.run(['wsl', '-e', 'bash', '-lc', 'true'], capture_output=True, text=True, timeout=5)
            wsl_available = result.returncode == 0
        except Exception:
            wsl_available = False
    if wsl_available:
        try:
            activate = f'source {wsl_venv}/bin/activate >/dev/null 2>&1 || true'
            cmd = f'{activate} && mx_nc --version'
            result = subprocess.run(['wsl', '-e', 'bash', '-lc', cmd], capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                wsl_mx_nc = result.stdout.strip() or 'ok'
            else:
                wsl_mx_nc = result.stderr.strip() or 'not available'
        except Exception:
            wsl_mx_nc = 'error'
    accl_bench = memryx_root / 'acclBench' / 'acclBench.exe'
    mx_bench_path = shutil.which('mx_bench')
    return {'compile_mode': 'wsl' if wsl_enabled else 'windows', 'wsl_enabled': wsl_enabled, 'wsl_available': wsl_available, 'wsl_venv': wsl_venv, 'wsl_mx_nc': wsl_mx_nc, 'memryx_home': str(memryx_root), 'accl_bench': str(accl_bench) if accl_bench.exists() else '', 'mx_bench_path': mx_bench_path or ''}
hub._hybrid_status = _hybrid_status

def _build_search_observability() -> dict:
    index_meta = hub._safe_load_json(hub.MEMRYX_INDEX_DIR / 'index_meta.json') or {}
    staleness = hub._compute_index_staleness(index_meta)
    weights = hub._safe_load_json(hub.PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'search_weights.json') or {}
    tuning_set = hub._safe_load_json(hub.PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'search_tuning_set.json') or {}
    metadata_entries = hub._safe_load_json(hub.MEMRYX_INDEX_DIR / 'metadata.json')
    entry_count = len(metadata_entries) if isinstance(metadata_entries, list) else 0
    return {'index_meta': index_meta, 'index_entries': entry_count, 'index_staleness_hours': staleness.get('staleness_hours'), 'index_last_built_utc': staleness.get('indexed_at_utc'), 'weights': weights.get('weights', {}), 'tuning_cases': len(tuning_set.get('cases') or []), 'tuning_updated_utc': tuning_set.get('updated_utc')}
hub._build_search_observability = _build_search_observability

def _handle_api_error(exc: Exception, operation: str='operation') -> tuple:
    """Handle API errors safely: log details, return generic message to client."""
    hub._log_telemetry('api_error', {'operation': operation, 'error_type': type(exc).__name__})
    import traceback
    hub._log_indexing(f'Exception traceback: {traceback.format_exc()}')
    return (jsonify({'success': False, 'error': f'Failed to complete {operation}'}), 500)
hub._handle_api_error = _handle_api_error
