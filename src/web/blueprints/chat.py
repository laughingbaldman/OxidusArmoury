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
bp = Blueprint("chat", __name__)

def index():
    """Serve the main page."""
    return render_template('index.html')
hub.index = index

def get_status():
    """Get Oxidus status."""
    try:
        if not hub.oxidus:
            return jsonify({'error': 'Oxidus not initialized'})
        summary = hub.oxidus.thought_stream.get_thinking_summary()
        return jsonify({'total_thoughts': summary['total_thoughts'], 'questions': summary['total_questions'], 'decisions': summary['total_decisions'], 'ethical_checks': summary['ethical_checks'], 'insights': summary['insights_gained'], 'most_active': summary['most_active']})
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'get_status', 'error_type': type(exc).__name__})
        return (jsonify({'error': 'Failed to retrieve status'}), 500)
hub.get_status = get_status

def health_check():
    """Get system health for uptime, latency, and ingestion checks."""
    return jsonify(hub._build_health_report(include_admin=False))
hub.health_check = health_check

def get_thoughts():
    """Get recent thoughts."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    recent = hub.oxidus.thought_stream.get_recent_thoughts(10)
    thought_items = []
    for thought in recent:
        if thought is None:
            continue
        thought_items.append({'type': thought.type.value, 'content': thought.content, 'timestamp': thought.timestamp.isoformat(), 'context': thought.context, 'display': str(thought)})
    if request.args.get('format') == 'legacy':
        return jsonify({'thoughts': [item['display'] for item in thought_items]})
    return jsonify(thought_items)
hub.get_thoughts = get_thoughts

def send_message():
    """Send a message to Oxidus and get response."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    data = request.json
    user_message = data.get('message', '').strip()
    if not user_message:
        return jsonify({'error': 'Empty message'})
    user_entry = {'speaker': 'YOU', 'message': user_message, 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'user'}
    hub.conversation_history.append(user_entry)
    message_topics = hub._extract_query_topics(user_message, max_items=3)
    hub._record_access_signal('user_message', topics=message_topics)
    system_prompt = hub.OXIDUS_TONE_PROMPT
    if len(message_topics) >= 2:
        system_prompt = f'{hub.OXIDUS_TONE_PROMPT} When the user asks about multiple topics, cover each briefly, then add a short section that connects them. Look for at least one shared mechanism, constraint, or practical bridge. If the connection is weak, say so and offer 1-2 angles to explore.'

    def _model_call(question, context):
        prompt = system_prompt
        if context:
            prompt = f'{system_prompt}\n\nReference cards:\n{context}'
        return hub.lm_client.ask_routed_question(question, system_prompt=prompt)
    from src.runtime.reply import reply_to
    packed = reply_to(hub.PROJECT_ROOT, user_message, model_call=_model_call)
    lm_response = packed.get('text')
    try:
        from src.web.blueprints.brain import record_activity
        record_activity(user_message, packed.get('hits'), message_topics)
    except Exception:
        pass
    if lm_response is None or not str(lm_response).strip():
        lm_response = hub._fallback_response(user_message)
    documented = hub._store_answered_exchange(user_message, str(lm_response))
    oxidus_entry = {'speaker': 'OXIDUS', 'message': str(lm_response), 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'oxidus', 'documented': bool(documented)}
    hub.conversation_history.append(oxidus_entry)
    hub.save_conversation()
    summary = hub.oxidus.thought_stream.get_thinking_summary()
    return jsonify({'user_message': user_entry, 'oxidus_response': oxidus_entry, 'status': {'total_thoughts': summary['total_thoughts'], 'questions': summary['total_questions'], 'decisions': summary['total_decisions'], 'insights': summary['insights_gained']}, 'tier': packed.get('tier'), 'questions_open': packed.get('questions') or []})
hub.send_message = send_message

def get_conversation():
    """Get full conversation history."""
    if request.args.get('format') == 'legacy':
        return jsonify({'history': hub.conversation_history})
    return jsonify(hub.conversation_history)
hub.get_conversation = get_conversation

def clear_conversation():
    """Clear conversation history."""
    hub.conversation_history = []
    if hub.CONVERSATION_FILE.exists():
        try:
            os.remove(hub.CONVERSATION_FILE)
        except Exception as e:
            print(f'Error deleting conversation file: {e}')
    initial_message = {'speaker': 'OXIDUS', 'message': "Conversation cleared. Let's start fresh.", 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'greeting'}
    hub.conversation_history.append(initial_message)
    hub.save_conversation()
    return jsonify({'success': True})
hub.clear_conversation = clear_conversation

def get_understanding():
    """Get Oxidus's current understanding evolution."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    understanding = hub.oxidus.get_understanding_summary()
    return jsonify({'summary': understanding, 'concepts': hub.oxidus.understanding_model, 'adaptation_count': hub.oxidus.adaptation_frequency, 'total_concepts': len(hub.oxidus.understanding_model)})
hub.get_understanding = get_understanding

def get_memory_summary():
    """Get Oxidus's memory index summary."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    summary = hub.oxidus.get_memory_summary()
    memories = []
    if hub.oxidus.memory_index is not None:
        try:
            memories = [{'content': memory.content, 'memory_type': memory.memory_type, 'topics': list(memory.topics), 'recall_count': memory.recall_count, 'timestamp': memory.timestamp.isoformat()} for memory in hub.oxidus.memory_index.memories]
        except Exception:
            memories = []
    return jsonify({'summary': summary, 'memories': memories, 'available': hub.oxidus.memory_index is not None})
hub.get_memory_summary = get_memory_summary

def recall_memories():
    """Recall memories about a specific topic."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    data = request.json
    topic = data.get('topic', '').strip()
    max_results = data.get('max_results', 5)
    if not topic:
        return jsonify({'error': 'No topic provided'})
    hub._record_access_signal('memory_recall', topics=[topic])
    memories = hub.oxidus.recall_memories(topic, max_results=max_results)
    return jsonify({'topic': topic, 'memories': memories})
hub.recall_memories = recall_memories

def get_knowledge_organization():
    """Get knowledge organization report."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    report = hub.oxidus.get_knowledge_organization_report()
    understanding_summary = hub.oxidus.get_understanding_summary()
    understanding_model = getattr(hub.oxidus, 'understanding_model', {})
    adaptation_count = getattr(hub.oxidus, 'adaptation_frequency', 0)
    total_concepts = len(understanding_model)
    memory_summary = hub.oxidus.get_memory_summary() if hasattr(hub.oxidus, 'get_memory_summary') else None
    recent_thoughts = []
    if hasattr(hub.oxidus, 'thought_stream') and hasattr(hub.oxidus.thought_stream, 'thoughts'):
        recent_thoughts = [str(t) for t in hub.oxidus.thought_stream.thoughts[-10:]]
    loose_ends = {}
    notes_summary = {}
    pipeline_summary = {}
    research_needs = {}
    relevance_config = {}
    retention_config = {}
    if hub.oxidus.knowledge_organizer:
        try:
            loose_ends = hub.oxidus.knowledge_organizer.get_loose_ends_report()
            notes_summary = {'total': len(hub.oxidus.knowledge_organizer.list_notes()), 'open': len(hub.oxidus.knowledge_organizer.list_notes(status='open'))}
            pipeline_summary = hub.oxidus.knowledge_organizer.get_pipeline_summary()
            research_needs = hub.oxidus.knowledge_organizer.get_research_needs()
            relevance_config = dict(hub.oxidus.knowledge_organizer.relevance)
            retention_config = dict(hub.oxidus.knowledge_organizer.retention)
        except Exception:
            loose_ends = {}
            notes_summary = {}
            pipeline_summary = {}
            research_needs = {}
            relevance_config = {}
            retention_config = {}
    pipeline_health = {}
    total_sources = pipeline_summary.get('total_sources') or 0
    if total_sources:
        pipeline_health = {'concepts_extracted_pct': round(pipeline_summary.get('concepts_extracted', 0) / total_sources * 100, 1), 'values_mapped_pct': round(pipeline_summary.get('values_mapped', 0) / total_sources * 100, 1), 'summaries_generated_pct': round(pipeline_summary.get('summaries_generated', 0) / total_sources * 100, 1), 'indexed_pct': round(pipeline_summary.get('indexed', 0) / total_sources * 100, 1)}
    metrics = {}
    if isinstance(report, dict):
        metrics = report
    elif isinstance(report, str):

        def _extract_int(patterns):
            for pattern in patterns:
                match = re.search(pattern, report)
                if match:
                    return int(match.group(1))
            return 0
        metrics['total_sources'] = _extract_int(['Total Sources Scraped:\\s*(\\d+)', 'Total sources: (\\d+)', 'Sources Organized: (\\d+)'])
        metrics['total_concepts'] = _extract_int(['Total Concepts Indexed:\\s*(\\d+)'])
        metrics['value_connections'] = {}
        value_section = re.search('VALUE TRENDS ACROSS SOURCES\\n([\\s\\S]+?)(?:\\n\\n|$)', report)
        if value_section:
            for line in value_section.group(1).splitlines():
                m = re.match('-\\s*(\\w+):\\s*(\\d+)', line)
                if m:
                    metrics['value_connections'][m.group(1)] = int(m.group(2))
        top_concepts_section = re.search('TOP CONCEPTS FOUND\\n([\\s\\S]+?)(?:\\n\\n|$)', report)
        metrics['top_concepts'] = []
        if top_concepts_section:
            for line in top_concepts_section.group(1).splitlines():
                m = re.match('-\\s*\\*\\*(\\w+)\\*\\* \\((\\d+)', line)
                if m:
                    metrics['top_concepts'].append(m.group(1))
    return jsonify({'report': report, 'metrics': metrics, 'understanding_summary': understanding_summary, 'understanding_model': understanding_model, 'adaptation_count': adaptation_count, 'total_concepts': total_concepts, 'memory_summary': memory_summary, 'recent_thoughts': recent_thoughts, 'loose_ends': loose_ends, 'notes_summary': notes_summary, 'pipeline_summary': pipeline_summary, 'pipeline_health': pipeline_health, 'index_meta': hub._load_index_meta(), 'research_needs': research_needs, 'relevance_config': relevance_config, 'retention_config': retention_config, 'available': hub.oxidus.knowledge_organizer is not None})
hub.get_knowledge_organization = get_knowledge_organization

def initiate_conversation():
    """Oxidus initiates conversation autonomously."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if hub.conversation_history:
        latest = None
        for entry in reversed(hub.conversation_history):
            if entry.get('speaker') == 'OXIDUS':
                latest = entry
                break
        if not latest:
            latest = {'speaker': 'OXIDUS', 'message': hub._build_opening_message(), 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'oxidus_initiate', 'skipped': True}
        summary = hub.oxidus.thought_stream.get_thinking_summary()
        return jsonify({'oxidus_message': latest, 'status': {'total_thoughts': summary['total_thoughts'], 'questions': summary['total_questions'], 'decisions': summary['total_decisions'], 'insights': summary['insights_gained']}, 'skipped': True})
    opening = hub._build_opening_message()
    oxidus_entry = {'speaker': 'OXIDUS', 'message': opening, 'timestamp': datetime.now().strftime('%H:%M:%S'), 'type': 'oxidus_initiate'}
    hub.conversation_history.append(oxidus_entry)
    summary = hub.oxidus.thought_stream.get_thinking_summary()
    return jsonify({'oxidus_message': oxidus_entry, 'status': {'total_thoughts': summary['total_thoughts'], 'questions': summary['total_questions'], 'decisions': summary['total_decisions'], 'insights': summary['insights_gained']}})
hub.initiate_conversation = initiate_conversation

def manage_chat_style():
    """Get or set chat style (hybrid/user_led)."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    if request.method == 'POST':
        data = request.json or {}
        style = data.get('style', 'hybrid')
        message = hub.oxidus.set_chat_style(style)
        settings = hub.load_settings()
        settings['chat_style'] = hub.oxidus.get_chat_style()
        save_settings(settings)
        return jsonify({'success': True, 'style': hub.oxidus.get_chat_style(), 'message': message})
    return jsonify({'style': hub.oxidus.get_chat_style()})
hub.manage_chat_style = manage_chat_style

def register(app):
    bp.add_url_rule('/', 'index', hub.index, methods=['GET'])
    bp.add_url_rule('/api/status', 'get_status', hub.get_status, methods=['GET'])
    bp.add_url_rule('/api/health', 'health_check', hub.health_check, methods=['GET'])
    bp.add_url_rule('/api/thoughts', 'get_thoughts', hub.get_thoughts, methods=['GET'])
    bp.add_url_rule('/api/message', 'send_message', hub.send_message, methods=['POST'])
    bp.add_url_rule('/api/conversation', 'get_conversation', hub.get_conversation, methods=['GET'])
    bp.add_url_rule('/api/clear', 'clear_conversation', hub.clear_conversation, methods=['POST'])
    bp.add_url_rule('/api/understanding', 'get_understanding', hub.get_understanding, methods=['GET'])
    bp.add_url_rule('/api/memories', 'get_memory_summary', hub.get_memory_summary, methods=['GET'])
    bp.add_url_rule('/api/recall', 'recall_memories', hub.recall_memories, methods=['POST'])
    bp.add_url_rule('/api/knowledge', 'get_knowledge_organization', hub.get_knowledge_organization, methods=['GET'])
    bp.add_url_rule('/api/initiate', 'initiate_conversation', hub.initiate_conversation, methods=['POST'])
    bp.add_url_rule('/api/chat-style', 'manage_chat_style', hub.manage_chat_style, methods=['GET', 'POST'])
    app.register_blueprint(bp)
