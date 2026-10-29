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
bp = Blueprint("learn", __name__)

def manage_conversation_mode():
    """Get or set conversation mode (human/ai)."""
    if not hub.oxidus or not hub.oxidus.ai_conversation:
        return jsonify({'error': 'AI conversation not available'})
    if request.method == 'POST':
        data = request.json
        new_mode = data.get('mode', 'human')
        response = hub.oxidus.switch_conversation_mode(new_mode)
        return jsonify({'success': True, 'mode': new_mode, 'message': response, 'description': hub.oxidus.get_conversation_mode()})
    else:
        return jsonify({'mode': hub.oxidus.ai_conversation.mode, 'descriptor': hub.oxidus.get_conversation_mode(), 'learning_summary': hub.oxidus.ai_conversation.get_learning_summary()})
hub.manage_conversation_mode = manage_conversation_mode

def ask_novel_question():
    """Ask a novel question to avoid repetition."""
    if not hub.oxidus or not hub.oxidus.ai_conversation:
        return jsonify({'error': 'AI conversation not available'})
    data = request.json
    topic = data.get('topic', 'understanding')
    question = hub.oxidus.generate_novel_question(topic)
    return jsonify({'topic': topic, 'question': question, 'mode': hub.oxidus.ai_conversation.mode})
hub.ask_novel_question = ask_novel_question

def process_ai_response():
    """Process a response from another AI."""
    if not hub.oxidus or not hub.oxidus.ai_conversation:
        return jsonify({'error': 'AI conversation not available'})
    data = request.json
    ai_response = data.get('response', '')
    question = data.get('question', '')
    analysis = hub.oxidus.process_ai_response(ai_response, question)
    return jsonify({'success': True, 'analysis': analysis, 'learning_summary': hub.oxidus.ai_conversation.get_learning_summary()})
hub.process_ai_response = process_ai_response

def mode_analysis():
    """Analyze learning from different modes."""
    if not hub.oxidus:
        return jsonify({'error': 'Oxidus not initialized'})
    analysis = hub.oxidus.analyze_mode_differences()
    return jsonify({'analysis': analysis, 'available': hub.oxidus.ai_conversation is not None})
hub.mode_analysis = mode_analysis

def ask_lm_studio():
    """Ask LM Studio AI a question and learn from the response."""
    if not hub.oxidus or not hub.oxidus.ai_conversation:
        return jsonify({'error': 'AI conversation not available'})
    import sys
    sys.path.insert(0, 'src/utils')
    from src.utils.lm_studio_client import get_lm_studio_client
    client = get_lm_studio_client()
    if not client.is_available():
        return jsonify({'error': 'LM Studio not available', 'message': 'Make sure LM Studio is running at http://127.0.0.1:1234'})
    data = request.json
    topic = data.get('topic', 'understanding')
    question = hub.oxidus.generate_novel_question(topic)
    if not question or 'exhausted' in question.lower():
        return jsonify({'error': 'No more questions available for this topic', 'topic': topic})
    result = client.ask_for_oxidus(question, context=f'Learning about: {topic}')
    if not result['success']:
        return jsonify({'error': 'Failed to get response from LM Studio', 'question': question})
    analysis = hub.oxidus.process_ai_response(result['response'], question)
    secondary_response = client.ask_secondary_judgment(primary_response=result['response'], original_question=question, context=f'Learning about: {topic}')
    secondary_analysis = None
    if secondary_response:
        secondary_analysis = hub.oxidus.process_secondary_judgment(secondary_response, question)
    return jsonify({'success': True, 'topic': topic, 'question': question, 'ai_response': result['response'], 'oxidus_analysis': analysis, 'secondary_response': secondary_response, 'secondary_analysis': secondary_analysis, 'mode': hub.oxidus.ai_conversation.mode, 'learning_summary': hub.oxidus.ai_conversation.get_learning_summary()})
hub.ask_lm_studio = ask_lm_studio

def lm_studio_status():
    """Check if LM Studio is available."""
    import sys
    sys.path.insert(0, 'src/utils')
    from src.utils.lm_studio_client import get_lm_studio_client
    client = get_lm_studio_client()
    available = client.is_available()
    client.ensure_model_selected()
    expected_model = client.primary_model
    active_model = client.model
    model_match = bool(available and active_model == expected_model)
    model_info = None
    if available:
        model_info = client.get_model_info()
    return (jsonify({'available': available, 'url': client.base_url, 'model': active_model, 'primary_model': expected_model, 'secondary_model': client.secondary_model, 'model_info': model_info, 'expected_model': expected_model, 'model_match': model_match, 'assertions': {'model_match': model_match, 'expected_model': expected_model, 'active_model': active_model, 'ok': bool(available and model_match)}}), 503 if available and (not model_match) else 200)
hub.lm_studio_status = lm_studio_status

def register(app):
    bp.add_url_rule('/api/mode', 'manage_conversation_mode', hub.manage_conversation_mode, methods=['GET', 'POST'])
    bp.add_url_rule('/api/ask-novel-question', 'ask_novel_question', hub.ask_novel_question, methods=['POST'])
    bp.add_url_rule('/api/process-ai-response', 'process_ai_response', hub.process_ai_response, methods=['POST'])
    bp.add_url_rule('/api/mode-analysis', 'mode_analysis', hub.mode_analysis, methods=['GET'])
    bp.add_url_rule('/api/ask-lm-studio', 'ask_lm_studio', hub.ask_lm_studio, methods=['POST'])
    bp.add_url_rule('/api/lm-studio-status', 'lm_studio_status', hub.lm_studio_status, methods=['GET'])
    app.register_blueprint(bp)
