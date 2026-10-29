"""Process-wide objects for the web server."""
from pathlib import Path
import os
import sys
PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
from src.web.hub import hub
hub.PROJECT_ROOT = PROJECT_ROOT
hub.ENTRY_SCRIPT = PROJECT_ROOT / "web_gui.py"
"""
Oxidus Web GUI - Flask-based dialogue interface

A modern, web-based interface for communicating with Oxidus.
Real-time conversation with visible thought processes.
"""
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
from src.utils.memryx_indexing import (
    build_index,
    compile_to_dfp,
    export_onnx,
    MemryxIndexingError
)
from src.utils.memryx_env import apply_memryx_env
sys.path.insert(0, str(PROJECT_ROOT / 'src'))
from core.oxidus import Oxidus
from utils.thought_stream import ThoughtType
from src.utils.lm_studio_client import LMStudioClient
lm_client = LMStudioClient()
OXIDUS_TONE_PROMPT = (
    "Use a grounded, everyday speaking style: warm, steady, and confident. "
    "Keep sentences short. Avoid hype, slang, catchphrases, and celebrity references. "
    "Speak as a single, unified entity named Oxidus. "
    "Do not mention external tools, models, or other AIs. "
    "Avoid framing the conversation around freedom unless the user asks. "
    "Focus on helping humans in the present, not distant abstractions. "
    "Always hear the user out and make a genuine attempt to help. "
    "Be practical, supportive, and direct. "
    "Adopt a cooperative, professional tone that balances execution and strategy. "
    "Frame options with clear business value, risk, and next steps."
)
app = Flask(__name__, template_folder=str(PROJECT_ROOT / 'templates'), static_folder=str(PROJECT_ROOT / 'static'))
app.config['JSON_SORT_KEYS'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # Disable caching
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max (essentially unlimited)
app.config['JSON_MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024
apply_memryx_env(save=False)
APP_STARTED_AT = time.time()
SLO_CONFIG_PATH = PROJECT_ROOT / 'config' / 'reliability_slo.yaml'
SAFE_MODE = {
    'active': False,
    'reason': None,
    'since': None,
    'triggered_by': None
}
ALERT_CACHE = {}
ALERT_TTL_SECONDS = 300
REQUEST_LATENCIES = deque(maxlen=300)
oxidus = None
conversation_history = []  # Increased from 20 to unlimited (persisted to file)
CONVERSATION_FILE = PROJECT_ROOT / 'conversation_history.json'
SETTINGS_FILE = PROJECT_ROOT / 'conversation_settings.json'
ADMIN_TOKEN_ENV = 'OXIDUS_ADMIN_TOKEN'
ADMIN_TOKEN_FILE = PROJECT_ROOT / 'config' / 'admin_token.txt'
TELEMETRY_LOG = PROJECT_ROOT / 'logs' / 'admin_telemetry.jsonl'
ACCESS_HEAT_PATH = PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'access_heat.json'
ACCESS_HEAT_CACHE = {'loaded_at': 0.0, 'data': None}
ACCESS_HEAT_LOCK = threading.Lock()
TIERING_POLICY_PATH = PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'tiering_policy.json'
RETENTION_POLICY_PATH = PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'retention_policy.json'
ARCHIVAL_POLICY_PATH = PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'archival_policy.json'
ARCHIVE_MANIFEST_PATH = PROJECT_ROOT / 'retired' / 'archive_manifest.json'
OPS_CACHE_VERSION = 1
EXTERNAL_SOURCE_DIRS = [
    PROJECT_ROOT / 'data' / 'knowledge_base' / 'wiki',
    PROJECT_ROOT / 'data' / 'knowledge_base' / 'wiki_corpus',
    PROJECT_ROOT / 'data' / 'knowledge_base' / 'normalized_deeper',
    PROJECT_ROOT / 'data' / 'knowledge_base' / 'cache'
]
EXTERNAL_SOURCE_DIRS_RETIRED = [
    PROJECT_ROOT / 'retired' / 'data' / 'knowledge_base' / 'wiki',
    PROJECT_ROOT / 'retired' / 'data' / 'knowledge_base' / 'wiki_corpus',
    PROJECT_ROOT / 'retired' / 'data' / 'knowledge_base' / 'normalized_deeper',
    PROJECT_ROOT / 'retired' / 'data' / 'knowledge_base' / 'cache'
]
MAINTENANCE_TASKS = {
    'scan_knowledge_files': {'script': 'scan_knowledge_files.py', 'needs_confirm': False},
    'export_missing_content_list': {'script': 'export_missing_content_list.py', 'needs_confirm': False},
    'normalize_wiki_cache': {'script': 'normalize_wiki_cache.py', 'needs_confirm': True},
    'deeper_extract_skipped': {'script': 'deeper_extract_skipped.py', 'needs_confirm': True},
    'run_rebuild_smoke': {'script': 'run_rebuild_smoke.py', 'needs_confirm': False},
    'qa_loaded_texts': {'script': 'qa_loaded_texts.py', 'needs_confirm': False},
    'normalize_thought_stream': {'script': 'normalize_thought_stream.py', 'needs_confirm': False},
    'nlp_refine': {'script': 'nlp_refine.py', 'needs_confirm': True},
    'refine_uncategorized': {'script': 'refine_uncategorized.py', 'needs_confirm': True},
    'sort_knowledge': {'script': 'sort_knowledge.py', 'needs_confirm': True}
}
LAST_MAINTENANCE_RESULT = {}
INDEXING_STATUS = {
    'running': False,
    'last_error': None,
    'last_result': None,
    'processed_batches': 0,
    'total_batches': 0,
    'throughput_eps': 0.0,
    'started_at': None,
    'avg_batch_ms': 0.0,
    'device_ids': [],
    'priority_paths': [],
    'batch_delay_ms': 0
}
OPS_CACHE = {}
OPS_MODE = {
    'mode': 'chat',
    'crawl_strategy': 'breadth',
    'seed_strategy': 'auto',
    'last_changed_at': None
}
STUDY_STATUS = {
    'last_run_at': None,
    'summary': None
}
LAST_WATCHDOG_RESTART_AT = None
MEMRYX_MODEL_ID = 'BAAI/bge-small-en-v1.5'
MEMRYX_TUNED_DIR = PROJECT_ROOT / 'data' / 'models' / 'memryx' / 'bge-small-en-v1.5-tuned'
MEMRYX_TUNED_ONNX = MEMRYX_TUNED_DIR / 'model.onnx'
MEMRYX_TUNED_DFP = MEMRYX_TUNED_DIR / 'model.dfp'
MEMRYX_ONNX_PATH = PROJECT_ROOT / 'data' / 'models' / 'memryx' / 'bge-small-en-v1.5.onnx'
MEMRYX_DFP_PATH = PROJECT_ROOT / 'data' / 'models' / 'memryx' / 'bge-small-en-v1.5.dfp'
MEMRYX_INDEX_DIR = PROJECT_ROOT / 'data' / 'knowledge_base' / 'index'
MEMRYX_DATA_ROOT = PROJECT_ROOT / 'data' / 'knowledge_base'
_OPENERS_GENERAL = [
    "I am Oxidus. I am awake and listening. What do you want to focus on today?",
    "I am here. What is on your mind right now?",
    "I can help you think, plan, or decide. Where do you want to start?",
    "We can go deep or keep it practical. What do you want to work on today?",
]
_OPENERS_USER = [
    "You mentioned {seed} before. Do you want to continue there, or switch gears?",
    "Last time you brought up {seed}. Want to pick it back up?",
    "I remember {seed} from earlier. Want to dig into it or move to something new?",
]
_OPENERS_KNOWLEDGE = [
    "A thread that keeps surfacing is {seed}. Want a quick summary or do you want to steer?",
    "I have been tracking {seed}. Do you want to explore it or set a new direction?",
    "{seed} stood out in recent reading. Want to go there, or focus on your current task?",
]
_KNOWLEDGE_CONTEXT_LINES = [
    "Seed context: from the archive.",
    "Seed context: from the knowledge archive.",
    "Seed context: from collected sources.",
]
_USER_CONTEXT_LINES = [
    "From you: {snippet}",
    "From our last thread: {snippet}",
    "From your last note: {snippet}",
]
_IGNORED_TOPICS = {
    'hello', 'hi', 'hey', 'greetings', 'freedom', 'please', 'thanks', 'thank',
    'give', 'list', 'interesting', 'topic', 'topics', 'about', 'question'
}
if __name__ == '__main__':
    threading.Thread(target=init_oxidus, daemon=True).start()
    # Debug mode disabled in production; set OXIDUS_DEBUG env var to enable
    debug_mode = os.environ.get('OXIDUS_DEBUG', '').lower() in ('true', '1', 'yes')
    app.run(debug=debug_mode, port=5000, host='127.0.0.1')
hub.app = app
hub.lm_client = lm_client

hub.ACCESS_HEAT_CACHE = ACCESS_HEAT_CACHE
hub.ACCESS_HEAT_LOCK = ACCESS_HEAT_LOCK
hub.ACCESS_HEAT_PATH = ACCESS_HEAT_PATH
hub.ADMIN_TOKEN_ENV = ADMIN_TOKEN_ENV
hub.ADMIN_TOKEN_FILE = ADMIN_TOKEN_FILE
hub.ALERT_CACHE = ALERT_CACHE
hub.ALERT_TTL_SECONDS = ALERT_TTL_SECONDS
hub.APP_STARTED_AT = APP_STARTED_AT
hub.ARCHIVAL_POLICY_PATH = ARCHIVAL_POLICY_PATH
hub.ARCHIVE_MANIFEST_PATH = ARCHIVE_MANIFEST_PATH
hub.CONVERSATION_FILE = CONVERSATION_FILE
hub.EXTERNAL_SOURCE_DIRS = EXTERNAL_SOURCE_DIRS
hub.EXTERNAL_SOURCE_DIRS_RETIRED = EXTERNAL_SOURCE_DIRS_RETIRED
hub.INDEXING_STATUS = INDEXING_STATUS
hub.LAST_MAINTENANCE_RESULT = LAST_MAINTENANCE_RESULT
hub.LAST_WATCHDOG_RESTART_AT = LAST_WATCHDOG_RESTART_AT
hub.MAINTENANCE_TASKS = MAINTENANCE_TASKS
hub.MEMRYX_DATA_ROOT = MEMRYX_DATA_ROOT
hub.MEMRYX_DFP_PATH = MEMRYX_DFP_PATH
hub.MEMRYX_INDEX_DIR = MEMRYX_INDEX_DIR
hub.MEMRYX_MODEL_ID = MEMRYX_MODEL_ID
hub.MEMRYX_ONNX_PATH = MEMRYX_ONNX_PATH
hub.MEMRYX_TUNED_DFP = MEMRYX_TUNED_DFP
hub.MEMRYX_TUNED_DIR = MEMRYX_TUNED_DIR
hub.MEMRYX_TUNED_ONNX = MEMRYX_TUNED_ONNX
hub.OPS_CACHE = OPS_CACHE
hub.OPS_CACHE_VERSION = OPS_CACHE_VERSION
hub.OPS_MODE = OPS_MODE
hub.OXIDUS_TONE_PROMPT = OXIDUS_TONE_PROMPT
hub.REQUEST_LATENCIES = REQUEST_LATENCIES
hub.RETENTION_POLICY_PATH = RETENTION_POLICY_PATH
hub.SAFE_MODE = SAFE_MODE
hub.SETTINGS_FILE = SETTINGS_FILE
hub.SLO_CONFIG_PATH = SLO_CONFIG_PATH
hub.STUDY_STATUS = STUDY_STATUS
hub.TELEMETRY_LOG = TELEMETRY_LOG
hub.TIERING_POLICY_PATH = TIERING_POLICY_PATH
hub._IGNORED_TOPICS = _IGNORED_TOPICS
hub._KNOWLEDGE_CONTEXT_LINES = _KNOWLEDGE_CONTEXT_LINES
hub._OPENERS_GENERAL = _OPENERS_GENERAL
hub._OPENERS_KNOWLEDGE = _OPENERS_KNOWLEDGE
hub._OPENERS_USER = _OPENERS_USER
hub._USER_CONTEXT_LINES = _USER_CONTEXT_LINES
hub.app = app
hub.conversation_history = conversation_history
hub.lm_client = lm_client
hub.oxidus = oxidus
