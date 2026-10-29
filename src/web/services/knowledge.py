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

def _load_access_heat() -> dict:
    cached = hub.ACCESS_HEAT_CACHE.get('data')
    if isinstance(cached, dict):
        return cached
    data = {'updated_utc': None, 'topics': {}, 'paths': {}, 'events': {}}
    if hub.ACCESS_HEAT_PATH.exists():
        try:
            payload = json.loads(hub.ACCESS_HEAT_PATH.read_text(encoding='utf-8'))
            if isinstance(payload, dict):
                data['updated_utc'] = payload.get('updated_utc')
                data['topics'] = payload.get('topics') or {}
                data['paths'] = payload.get('paths') or {}
                data['events'] = payload.get('events') or {}
        except Exception:
            pass
    hub.ACCESS_HEAT_CACHE['data'] = data
    return data
hub._load_access_heat = _load_access_heat

def _save_access_heat(data: dict) -> None:
    try:
        hub.ACCESS_HEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = hub.ACCESS_HEAT_PATH.with_suffix('.json.tmp')
        with tmp_path.open('w', encoding='utf-8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=True)
        tmp_path.replace(hub.ACCESS_HEAT_PATH)
        hub.ACCESS_HEAT_CACHE['data'] = data
    except Exception:
        pass
hub._save_access_heat = _save_access_heat

def _build_access_heat_snapshot(limit: int=50) -> dict:
    data = hub._load_access_heat()
    topics = data.get('topics') or {}
    paths = data.get('paths') or {}

    def _sort_key(item):
        payload = item[1] if isinstance(item[1], dict) else {}
        return int(payload.get('count', 0) or 0)
    top_topics = sorted(topics.items(), key=_sort_key, reverse=True)[:limit]
    top_paths = sorted(paths.items(), key=_sort_key, reverse=True)[:limit]
    return {'updated_utc': data.get('updated_utc'), 'events': data.get('events') or {}, 'top_topics': [{'topic': topic, 'count': entry.get('count', 0), 'last_access_utc': entry.get('last_access_utc')} for topic, entry in top_topics if isinstance(entry, dict)], 'top_paths': [{'path': path, 'count': entry.get('count', 0), 'last_access_utc': entry.get('last_access_utc')} for path, entry in top_paths if isinstance(entry, dict)]}
hub._build_access_heat_snapshot = _build_access_heat_snapshot

def _collect_knowledge_seeds(max_items: int=10) -> list:
    seeds = []
    if not hub.oxidus or not hub.oxidus.knowledge_organizer:
        return seeds
    organizer = hub.oxidus.knowledge_organizer
    try:
        seeds.extend(organizer.get_open_threads()[:max_items])
    except Exception:
        pass
    try:
        seeds.extend(organizer.get_underexplored_values()[:max_items])
    except Exception:
        pass
    for note in organizer.notes[-20:]:
        topic = (note or {}).get('topic')
        if topic:
            seeds.append(topic)
    for source in list(organizer.scraped_sources.values())[-30:]:
        title = (source or {}).get('title')
        if title:
            seeds.append(title)
    unique = []
    seen = set()
    for seed in seeds:
        cleaned = str(seed).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen or key in hub._IGNORED_TOPICS:
            continue
        seen.add(key)
        unique.append(cleaned)
        if len(unique) >= max_items:
            break
    return unique
hub._collect_knowledge_seeds = _collect_knowledge_seeds

def _knowledge_base_root() -> Path:
    return hub.PROJECT_ROOT / 'data' / 'knowledge_base'
hub._knowledge_base_root = _knowledge_base_root

def _relative_kb_path(path: Path) -> str:
    """Convert an absolute path to a relative knowledge base path. Returns sanitized path."""
    kb_root = hub._knowledge_base_root().resolve()
    try:
        resolved_path = path.resolve()
        if hub._is_path_safe(resolved_path, kb_root):
            relative = str(resolved_path.relative_to(kb_root))
            return relative.replace('\\', '/')
        else:
            return 'external'
    except (ValueError, OSError):
        return 'invalid'
hub._relative_kb_path = _relative_kb_path

def _load_policy(path: Path, defaults: dict) -> dict:
    if not path.exists():
        return dict(defaults)
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return dict(defaults)
    if not isinstance(payload, dict):
        return dict(defaults)
    merged = dict(defaults)
    merged.update({key: value for key, value in payload.items() if value is not None})
    return merged
hub._load_policy = _load_policy

def _load_tiering_policy() -> dict:
    defaults = {'hot_access_count': 5, 'hot_access_days': 30, 'warm_access_count': 2, 'warm_access_days': 60, 'max_candidates': 200}
    return hub._load_policy(hub.TIERING_POLICY_PATH, defaults)
hub._load_tiering_policy = _load_tiering_policy

def _load_retention_policy() -> dict:
    defaults = {'no_access_days': 180, 'min_age_days': 180, 'max_candidates': 200, 'eligible_roots': ['normalized_deeper/sorted', 'wiki_corpus', 'notes'], 'exclude_paths': ['metadata', 'index']}
    return hub._load_policy(hub.RETENTION_POLICY_PATH, defaults)
hub._load_retention_policy = _load_retention_policy

def _load_archival_policy() -> dict:
    defaults = {'archive_root': str(hub.PROJECT_ROOT / 'retired' / 'data' / 'knowledge_base' / 'archives'), 'max_per_run': 200, 'dry_run_default': True}
    return hub._load_policy(hub.ARCHIVAL_POLICY_PATH, defaults)
hub._load_archival_policy = _load_archival_policy

def _build_tiering_overview(max_candidates: Optional[int]=None) -> dict:
    policy = hub._load_tiering_policy()
    retention = hub._load_retention_policy()
    max_candidates = max_candidates or int(policy.get('max_candidates', 200))
    heat = hub._load_access_heat()
    heat_paths = heat.get('paths') or {}
    hot_roots = hub._hot_index_roots()
    hot_root_set = {str(root.resolve()) for root in hot_roots}

    def _tier_for_path(path: Path) -> str:
        for root in hot_roots:
            try:
                path.resolve().relative_to(root.resolve())
                return 'hot'
            except Exception:
                continue
        return 'cold'
    promote = []
    demote = []
    tier_counts = {'hot': 0, 'cold': 0}
    candidates = hub._iter_kb_files(roots=retention.get('eligible_roots'))
    for path in candidates:
        relative = hub._relative_kb_path(path)
        entry = heat_paths.get(relative) or {}
        access_count = int(entry.get('count', 0) or 0)
        last_access = hub._parse_iso_utc(entry.get('last_access_utc'))
        access_age_days = hub._days_since(last_access)
        tier = _tier_for_path(path)
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        if tier == 'cold':
            if access_count >= policy.get('hot_access_count', 0):
                if access_age_days is None or access_age_days <= policy.get('hot_access_days', 0):
                    promote.append({'path': relative, 'access_count': access_count, 'last_access_utc': entry.get('last_access_utc')})
        if tier == 'hot':
            no_access_days = retention.get('no_access_days')
            if access_age_days is not None and no_access_days is not None:
                if access_age_days >= float(no_access_days):
                    demote.append({'path': relative, 'access_count': access_count, 'last_access_utc': entry.get('last_access_utc')})
        if len(promote) >= max_candidates and len(demote) >= max_candidates:
            break
    return {'policy': policy, 'retention': retention, 'tier_counts': tier_counts, 'promote_candidates': promote[:max_candidates], 'demote_candidates': demote[:max_candidates]}
hub._build_tiering_overview = _build_tiering_overview

def _load_archive_manifest() -> dict:
    if not hub.ARCHIVE_MANIFEST_PATH.exists():
        return {'entries': []}
    try:
        payload = json.loads(hub.ARCHIVE_MANIFEST_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'entries': []}
    if not isinstance(payload, dict):
        return {'entries': []}
    if 'entries' not in payload or not isinstance(payload.get('entries'), list):
        payload['entries'] = []
    return payload
hub._load_archive_manifest = _load_archive_manifest

def _save_archive_manifest(payload: dict) -> None:
    try:
        hub.ARCHIVE_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = hub.ARCHIVE_MANIFEST_PATH.with_suffix('.json.tmp')
        with tmp_path.open('w', encoding='utf-8') as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)
        tmp_path.replace(hub.ARCHIVE_MANIFEST_PATH)
    except Exception:
        pass
hub._save_archive_manifest = _save_archive_manifest

def _recent_knowledge_files(limit: int=8) -> list:
    kb_root = hub._knowledge_base_root()
    roots = [kb_root / 'wiki_corpus', kb_root / 'normalized_deeper' / 'sorted', kb_root / 'notes']
    files = []
    for root in roots:
        if not root.exists():
            continue
        for ext in ('.json', '.md'):
            for path in root.rglob(f'*{ext}'):
                try:
                    stat = path.stat()
                except Exception:
                    continue
                files.append((stat.st_mtime, path, stat.st_size))
    files.sort(key=lambda item: item[0], reverse=True)
    recent = []
    for mtime, path, size in files[:limit]:
        if not hub._is_path_safe(path, kb_root):
            continue
        try:
            relative = str(path.relative_to(kb_root))
        except Exception:
            continue
        recent.append({'path': relative, 'relative': relative, 'name': path.name, 'mtime': mtime, 'size': size})
    return recent
hub._recent_knowledge_files = _recent_knowledge_files

def _resolve_path_under_base(raw_path: str, base: Path) -> Optional[Path]:
    """Resolve a user-provided path and ensure it remains under base.

    Only allows relative paths without .. or absolute path components.
    Does not expand user home directory for security (prevents path injection).
    """
    if not isinstance(raw_path, str):
        return None
    candidate_text = raw_path.strip()
    if not candidate_text:
        return None
    if raw_path.startswith('/') or raw_path.startswith('\\') or raw_path.startswith('~'):
        return None
    try:
        candidate = Path(candidate_text)
        if candidate.is_absolute() or any((part == '..' for part in candidate.parts)):
            return None
        normalized = Path(*[part for part in candidate.parts if part not in {'', '.'}])
        if not normalized.parts:
            return None
        resolved = (base / normalized).resolve()
        if not hub._is_path_safe(resolved, base):
            return None
        return resolved
    except (ValueError, OSError, RuntimeError):
        return None
hub._resolve_path_under_base = _resolve_path_under_base

def _resolve_relative_path_under_base(rel_path: str, base: Path) -> Optional[Path]:
    """Resolve a relative path safely under a base directory."""
    if not isinstance(rel_path, str):
        return None
    candidate_text = rel_path.strip()
    if not candidate_text:
        return None
    try:
        candidate = Path(candidate_text)
        if candidate.is_absolute() or any((part == '..' for part in candidate.parts)):
            return None
        normalized = Path(*[part for part in candidate.parts if part not in {'', '.'}])
        if not normalized.parts:
            return None
        resolved = (base / normalized).resolve()
        if not hub._is_path_safe(resolved, base):
            return None
        return resolved
    except (ValueError, OSError, RuntimeError):
        return None
hub._resolve_relative_path_under_base = _resolve_relative_path_under_base

def _archive_paths(paths: list, dry_run: bool=True, reason: str='retention_policy') -> dict:
    policy = hub._load_archival_policy()
    archive_root = Path(policy.get('archive_root') or '').expanduser()
    if not archive_root.is_absolute():
        archive_root = (hub.PROJECT_ROOT / archive_root).resolve()
    kb_root = hub._knowledge_base_root().resolve()
    manifest = hub._load_archive_manifest()
    entries = manifest.get('entries') or []
    now = datetime.utcnow().replace(microsecond=0).isoformat() + 'Z'
    moved = []
    skipped = []
    for rel_path in paths:
        if not rel_path:
            continue
        source = hub._resolve_relative_path_under_base(str(rel_path), kb_root)
        if not source:
            skipped.append({'path': rel_path, 'reason': 'invalid_path'})
            continue
        normalized_rel_path = source.relative_to(kb_root).as_posix()
        if not source.exists():
            skipped.append({'path': normalized_rel_path, 'reason': 'missing'})
            continue
        dest = hub._resolve_relative_path_under_base(normalized_rel_path, archive_root)
        if not dest:
            skipped.append({'path': normalized_rel_path, 'reason': 'invalid_path'})
            continue
        if dest.exists():
            skipped.append({'path': normalized_rel_path, 'reason': 'already_archived'})
            continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(dest))
        entry = {'original_relative': normalized_rel_path, 'archived_relative': normalized_rel_path, 'archived_at_utc': now, 'reason': reason}
        entries.append(entry)
        moved.append(entry)
    if not dry_run:
        manifest['entries'] = entries
        hub._save_archive_manifest(manifest)
    return {'dry_run': dry_run, 'archive_root': str(archive_root), 'moved': moved, 'skipped': skipped}
hub._archive_paths = _archive_paths
