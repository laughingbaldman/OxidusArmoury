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
bp = Blueprint("files", __name__)

def recent_files():
    limit = int(request.args.get('limit', 8))
    limit = max(1, min(limit, 50))
    return jsonify({'success': True, 'files': hub._recent_knowledge_files(limit=limit)})
hub.recent_files = recent_files

def open_file():
    data = request.json or {}
    path_raw = (data.get('path') or '').strip()
    if not path_raw:
        return (jsonify({'success': False, 'error': 'path required'}), 400)
    kb_root = hub._knowledge_base_root().resolve()
    path = hub._resolve_path_under_base(path_raw, kb_root)
    if not path:
        return (jsonify({'success': False, 'error': 'path not allowed'}), 403)
    if not path.exists():
        return (jsonify({'success': False, 'error': 'file not found'}), 404)
    hub._record_access_signal('file_open', paths=[hub._relative_kb_path(path)])
    try:
        if sys.platform == 'win32':
            os.startfile(str(path))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)], shell=False)
        else:
            subprocess.Popen(['xdg-open', str(path)], shell=False)
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'open_file', 'error_type': type(exc).__name__})
        return (jsonify({'error': 'Failed to open file'}), 500)
    return jsonify({'success': True})
hub.open_file = open_file

def register(app):
    bp.add_url_rule('/api/files/recent', 'recent_files', hub.recent_files, methods=['GET'])
    bp.add_url_rule('/api/files/open', 'open_file', hub.open_file, methods=['POST'])
    app.register_blueprint(bp)
