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

def save_conversation():
    """Save conversation history to file for persistence."""
    try:
        with open(hub.CONVERSATION_FILE, 'w', encoding='utf-8') as f:
            json.dump(hub.conversation_history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f'Error saving conversation: {e}')
hub.save_conversation = save_conversation

def load_conversation():
    """Load conversation history from file if it exists."""
    if hub.CONVERSATION_FILE.exists():
        try:
            with open(hub.CONVERSATION_FILE, 'r', encoding='utf-8') as f:
                hub.conversation_history = json.load(f)
                print(f'Loaded {len(hub.conversation_history)} messages from conversation history')
        except Exception as e:
            print(f'Error loading conversation: {e}')
            hub.conversation_history = []
    else:
        hub.conversation_history = []
hub.load_conversation = load_conversation

def _extract_user_topics(max_items: int=6) -> list:
    if not hub.conversation_history:
        return []
    stopwords = {'the', 'and', 'that', 'with', 'from', 'this', 'have', 'what', 'your', 'about', 'when', 'where', 'which', 'their', 'there', 'would', 'could', 'should', 'just', 'like', 'want', 'need', 'been', 'into', 'than', 'then', 'them', 'they', 'you', 'are', 'was', 'were', 'will', 'can', 'how', 'why', 'who', 'for', 'not', 'but'}
    counts = {}
    recent = hub.conversation_history[-50:]
    for entry in reversed(recent):
        if entry.get('speaker') != 'YOU':
            continue
        message = entry.get('message') or ''
        for word in re.findall("[A-Za-z][A-Za-z\\-']{2,}", message.lower()):
            if word in stopwords or word in hub._IGNORED_TOPICS:
                continue
            counts[word] = counts.get(word, 0) + 1
    if not counts:
        return []
    sorted_items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [word for word, _ in sorted_items[:max_items]]
hub._extract_user_topics = _extract_user_topics

def _extract_query_topics(message: str, max_items: int=3) -> list:
    if not message:
        return []
    if len(message) > 10000:
        message = message[:10000]
    cleaned = ' '.join(message.split()).strip()
    if not cleaned:
        return []
    lowered = cleaned.lower()
    parts = []
    if 'between ' in lowered and ' and ' in lowered:
        tail = lowered.split('between ', 1)[1]
        left, right = tail.split(' and ', 1)
        parts = [left.strip(), right.strip()]
    else:
        segment = lowered
        for trigger in ('about ', 'on ', 'regarding ', 're:', 'concerning '):
            marker = segment.find(trigger)
            if marker != -1:
                segment = segment[marker + len(trigger):]
                break
        normalized = segment.replace(',', ' and ')
        normalized = normalized.replace(' versus ', ' and ')
        normalized = normalized.replace(' vs ', ' and ')
        normalized = normalized.replace(' plus ', ' and ')
        parts = [chunk.strip() for chunk in normalized.split(' and ') if chunk.strip()]
    leading_phrases = ('tell me about', 'tell me', 'what about', 'explain', 'describe', 'overview of', 'give me', 'compare', 'difference between', 'between')
    stopwords = {'the', 'and', 'that', 'with', 'from', 'this', 'have', 'what', 'your', 'about', 'when', 'where', 'which', 'their', 'there', 'would', 'could', 'should', 'just', 'like', 'want', 'need', 'been', 'into', 'than', 'then', 'them', 'they', 'you', 'are', 'was', 'were', 'will', 'can', 'how', 'why', 'who', 'for', 'not', 'but'}
    topics = []
    seen = set()
    for part in parts:
        chunk = part.strip(' .!?;:')
        if not chunk:
            continue
        chunk_lower = chunk.lower()
        for phrase in leading_phrases:
            if chunk_lower.startswith(phrase + ' '):
                chunk = chunk[len(phrase):].strip()
                chunk_lower = chunk.lower()
                break
        tokens = re.findall("[A-Za-z][A-Za-z\\-']+", chunk_lower)
        if not tokens or all((token in stopwords for token in tokens)):
            continue
        key = ' '.join(tokens)
        if key in seen:
            continue
        seen.add(key)
        topics.append(chunk)
        if len(topics) >= max_items:
            break
    return topics
hub._extract_query_topics = _extract_query_topics

def _is_trivial_user_message(message: str) -> bool:
    if not message:
        return True
    cleaned = re.sub('\\s+', ' ', message).strip().lower()
    if len(cleaned) < 12:
        return True
    if re.fullmatch('(hello|hi|hey|greetings)(\\s+there)?[!.]?', cleaned):
        return True
    words = re.findall("[A-Za-z][A-Za-z\\-']{2,}", cleaned)
    if not words:
        return True
    return all((word in hub._IGNORED_TOPICS for word in words))
hub._is_trivial_user_message = _is_trivial_user_message

def _latest_user_message() -> Optional[str]:
    for entry in reversed(hub.conversation_history):
        if entry.get('speaker') == 'YOU':
            message = (entry.get('message') or '').strip()
            return message or None
    return None
hub._latest_user_message = _latest_user_message

def _shorten_snippet(text: str, max_len: int=80) -> str:
    cleaned = re.sub('\\s+', ' ', text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return f'{cleaned[:max_len - 1].rstrip()}…'
hub._shorten_snippet = _shorten_snippet

def _normalize_topic_key(text: str) -> str:
    if not text:
        return ''
    cleaned = re.sub('[^a-z0-9]+', ' ', text.lower())
    return re.sub('\\s+', ' ', cleaned).strip()
hub._normalize_topic_key = _normalize_topic_key

def _title_from_wiki_filename(file_name: str) -> str:
    stem = Path(file_name).stem
    if '_' not in stem:
        return stem
    return stem.rsplit('_', 1)[0]
hub._title_from_wiki_filename = _title_from_wiki_filename

def _find_wiki_corpus_matches(topics: list, max_items: int=2) -> list:
    if not topics:
        return []
    index_entries = hub._load_wiki_corpus_index()
    if not index_entries:
        return []
    normalized_topics = [hub._normalize_topic_key(topic) for topic in topics if topic]
    normalized_topics = [topic for topic in normalized_topics if topic]
    if not normalized_topics:
        return []
    matches = []
    for entry in index_entries:
        file_name = entry.get('file_name') or ''
        if not file_name:
            continue
        title = hub._title_from_wiki_filename(file_name)
        title_key = hub._normalize_topic_key(title)
        if not title_key:
            continue
        for topic_key in normalized_topics:
            if topic_key in title_key or title_key in topic_key:
                relative_path = entry.get('relative_path')
                if not relative_path:
                    continue
                matches.append({'title': title, 'path': hub.PROJECT_ROOT / 'data' / 'knowledge_base' / relative_path})
                break
        if len(matches) >= max_items:
            break
    return matches
hub._find_wiki_corpus_matches = _find_wiki_corpus_matches

def _extract_wiki_snippet(path: Path, max_len: int=600) -> str:
    if not path.exists():
        return ''
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ''
    paragraph = []
    for line in lines:
        if line.startswith('#'):
            continue
        paragraph.append(line)
        if len(' '.join(paragraph)) >= max_len:
            break
    snippet = ' '.join(paragraph).strip()
    if len(snippet) > max_len:
        snippet = snippet[:max_len - 1].rstrip() + '...'
    return snippet
hub._extract_wiki_snippet = _extract_wiki_snippet

def _build_opening_message() -> str:
    user_topics = hub._extract_user_topics()
    knowledge_seeds = hub._collect_knowledge_seeds()
    user_context_line = hub._build_user_context_line(user_topics)
    if not user_topics and (not knowledge_seeds):
        opener = random.choice(hub._OPENERS_GENERAL)
        if user_context_line:
            return f'{opener}\n\n{user_context_line}'
        return opener
    if user_topics and knowledge_seeds:
        source = random.choices(['user', 'knowledge'], weights=[0.7, 0.3], k=1)[0]
    elif user_topics:
        source = 'user'
    else:
        source = 'knowledge'
    if source == 'user':
        seed = random.choice(user_topics)
        opener = random.choice(hub._OPENERS_USER).format(seed=seed)
        if user_context_line:
            return f'{opener}\n\n{user_context_line}'
        return opener
    seed = random.choice(knowledge_seeds)
    opener = random.choice(hub._OPENERS_KNOWLEDGE).format(seed=seed)
    context_line = random.choice(hub._KNOWLEDGE_CONTEXT_LINES)
    if user_context_line:
        return f'{opener}\n\n{context_line}\n{user_context_line}'
    return f'{opener}\n\n{context_line}'
hub._build_opening_message = _build_opening_message
