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
bp = Blueprint("wiki", __name__)

def wiki_search():
    """Search Wikipedia and return results."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if not getattr(hub.oxidus, 'wikipedia', None):
        return jsonify({'error': 'Wikipedia API not available'})
    data = request.json or {}
    query = data.get('query', '').strip()
    limit = int(data.get('limit', 5))
    if not query:
        return jsonify({'error': 'No query provided'})
    hub._record_access_signal('wiki_search', topics=[query])
    result = hub.oxidus.wiki_search(query, limit=limit)
    return jsonify(result)
hub.wiki_search = wiki_search

def wiki_page():
    """Fetch a Wikipedia page and optionally organize it."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if not getattr(hub.oxidus, 'wikipedia', None):
        return jsonify({'error': 'Wikipedia API not available'})
    data = request.json or {}
    title = data.get('title', '').strip()
    organize = bool(data.get('organize', True))
    if not title:
        return jsonify({'error': 'No title provided'})
    hub._record_access_signal('wiki_page', topics=[title])
    result = hub.oxidus.wiki_page(title, organize=organize)
    return jsonify(result)
hub.wiki_page = wiki_page

def wiki_crawl_start():
    """Start Wikipedia crawling for selected domains."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('wiki_crawl_start')
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    data = request.json or {}
    domains = data.get('domains', [])
    max_depth = int(data.get('max_depth', 2))
    max_pages = data.get('max_pages')
    if max_pages is not None:
        max_pages = int(max_pages)
    strategy = (data.get('strategy') or 'breadth').strip().lower()
    seed_strategy = (data.get('seed_strategy') or 'auto').strip().lower()
    if not domains:
        return jsonify({'error': 'No domains provided'})
    result = hub.oxidus.start_wiki_crawl(domains=domains, max_depth=max_depth, max_pages=max_pages, strategy=strategy, seed_strategy=seed_strategy)
    if result.get('success'):
        hub.OPS_MODE['mode'] = 'crawl'
        hub.OPS_MODE['crawl_strategy'] = strategy
        hub.OPS_MODE['seed_strategy'] = seed_strategy
        hub.OPS_MODE['last_changed_at'] = datetime.now().isoformat()
        hub._log_telemetry('wiki_crawl_start', {'domains': domains, 'max_depth': max_depth, 'max_pages': max_pages, 'strategy': strategy, 'seed_strategy': seed_strategy})
    return jsonify(result)
hub.wiki_crawl_start = wiki_crawl_start

def wiki_crawl_stop():
    """Stop Wikipedia crawling."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    result = hub.oxidus.stop_wiki_crawl()
    if result.get('success'):
        hub.OPS_MODE['mode'] = 'chat'
        hub.OPS_MODE['last_changed_at'] = datetime.now().isoformat()
        hub._log_telemetry('wiki_crawl_stop')
    return jsonify(result)
hub.wiki_crawl_stop = wiki_crawl_stop

def wiki_crawl_resume():
    """Resume Wikipedia crawling from saved state."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('wiki_crawl_resume')
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    data = request.json or {}
    max_pages = data.get('max_pages')
    if max_pages is not None:
        max_pages = int(max_pages)
    result = hub.oxidus.resume_wiki_crawl(max_pages=max_pages)
    if result.get('success'):
        hub.OPS_MODE['mode'] = 'crawl'
        hub.OPS_MODE['last_changed_at'] = datetime.now().isoformat()
        hub._log_telemetry('wiki_crawl_resume', {'max_pages': max_pages})
    return jsonify(result)
hub.wiki_crawl_resume = wiki_crawl_resume

def wiki_crawl_status():
    """Get Wikipedia crawler status."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    result = hub.oxidus.wiki_crawl_status()
    return jsonify(result)
hub.wiki_crawl_status = wiki_crawl_status

def wiki_crawl_settings():
    """Get or update Wikipedia crawler settings."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if request.method == 'GET':
        result = hub.oxidus.wiki_crawl_settings()
        return jsonify(result)
    updates = request.json or {}
    guard = hub._block_if_safe_mode('wiki_crawl_settings_update')
    if guard:
        return guard
    result = hub.oxidus.update_wiki_crawl_settings(updates)
    if result.get('success'):
        hub._log_telemetry('wiki_crawl_settings_update', {'updates': updates})
    return jsonify(result)
hub.wiki_crawl_settings = wiki_crawl_settings

def wiki_crawl_settings_reset():
    """Reset Wikipedia crawler settings to defaults."""
    guard = hub.require_admin_token()
    if guard:
        return guard
    guard = hub._block_if_safe_mode('wiki_crawl_settings_reset')
    if guard:
        return guard
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    result = hub.oxidus.reset_wiki_crawl_settings()
    if result.get('success'):
        hub._log_telemetry('wiki_crawl_settings_reset')
    return jsonify(result)
hub.wiki_crawl_settings_reset = wiki_crawl_settings_reset

def register(app):
    bp.add_url_rule('/api/wiki-search', 'wiki_search', hub.wiki_search, methods=['POST'])
    bp.add_url_rule('/api/wiki-page', 'wiki_page', hub.wiki_page, methods=['POST'])
    bp.add_url_rule('/api/wiki-crawl/start', 'wiki_crawl_start', hub.wiki_crawl_start, methods=['POST'])
    bp.add_url_rule('/api/wiki-crawl/stop', 'wiki_crawl_stop', hub.wiki_crawl_stop, methods=['POST'])
    bp.add_url_rule('/api/wiki-crawl/resume', 'wiki_crawl_resume', hub.wiki_crawl_resume, methods=['POST'])
    bp.add_url_rule('/api/wiki-crawl/status', 'wiki_crawl_status', hub.wiki_crawl_status, methods=['GET'])
    bp.add_url_rule('/api/wiki-crawl/settings', 'wiki_crawl_settings', hub.wiki_crawl_settings, methods=['GET', 'POST'])
    bp.add_url_rule('/api/wiki-crawl/settings/reset', 'wiki_crawl_settings_reset', hub.wiki_crawl_settings_reset, methods=['POST'])
    app.register_blueprint(bp)
