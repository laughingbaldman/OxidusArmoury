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

def _safe_load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        with path.open('r', encoding='utf-8') as handle:
            return json.load(handle)
    except Exception:
        return None
hub._safe_load_json = _safe_load_json

def _warm_ops_cache() -> None:
    if not hub.oxidus:
        return
    hub.OPS_CACHE[hub._cache_key('ops_summary')] = {'ts': time.time(), 'value': hub._build_ops_summary(include_admin=False)}
    hub.OPS_CACHE[hub._cache_key('admin_ops_summary')] = {'ts': time.time(), 'value': hub._build_ops_summary(include_admin=True)}
hub._warm_ops_cache = _warm_ops_cache

def _record_request_latency(response):
    start = getattr(g, 'request_start', None)
    if start is not None:
        elapsed_ms = round((time.time() - start) * 1000, 2)
        hub.REQUEST_LATENCIES.append(elapsed_ms)
    return response
hub._record_request_latency = _record_request_latency

def _load_reliability_slo() -> dict:
    defaults = {'uptime_slo_pct': 99.5, 'api_latency_p95_ms': 800, 'ingestion_throughput_eps_min': 50.0, 'crawler_pages_per_min_min': 5.0, 'index_staleness_hours': 72.0, 'storage_free_gb_min': 5.0, 'alert_dedupe_seconds': hub.ALERT_TTL_SECONDS}
    if not hub.SLO_CONFIG_PATH.exists():
        return defaults
    try:
        payload = yaml.safe_load(hub.SLO_CONFIG_PATH.read_text(encoding='utf-8')) or {}
    except Exception:
        return defaults
    reliability = payload.get('reliability', {}) if isinstance(payload, dict) else {}
    merged = dict(defaults)
    for key, value in reliability.items():
        if value is not None:
            merged[key] = value
    return merged
hub._load_reliability_slo = _load_reliability_slo

def _latency_stats() -> dict:
    if not hub.REQUEST_LATENCIES:
        return {'count': 0, 'p50_ms': None, 'p95_ms': None}
    values = sorted(hub.REQUEST_LATENCIES)
    size = len(values)

    def _percentile(pct: float) -> float:
        if size == 1:
            return values[0]
        rank = max(1, math.ceil(pct * size)) - 1
        rank = min(rank, size - 1)
        return values[rank]
    return {'count': size, 'p50_ms': round(_percentile(0.5), 2), 'p95_ms': round(_percentile(0.95), 2)}
hub._latency_stats = _latency_stats

def _storage_health() -> dict:
    base_dir = hub.PROJECT_ROOT
    kb_root = hub._knowledge_base_root()
    target = kb_root if kb_root.exists() else base_dir
    try:
        usage = shutil.disk_usage(target)
        free_gb = round(usage.free / 1024 ** 3, 2)
        total_gb = round(usage.total / 1024 ** 3, 2)
        used_pct = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
    except Exception:
        free_gb = None
        total_gb = None
        used_pct = None
    return {'path': str(target), 'free_gb': free_gb, 'total_gb': total_gb, 'used_pct': used_pct}
hub._storage_health = _storage_health

def _enter_safe_mode(reason: str, triggered_by: str) -> None:
    if hub.SAFE_MODE.get('active') and hub.SAFE_MODE.get('reason') == reason:
        return
    hub.SAFE_MODE.update({'active': True, 'reason': reason, 'since': time.time(), 'triggered_by': triggered_by})
    hub._log_telemetry('safe_mode_enabled', {'reason': reason, 'triggered_by': triggered_by})
    if hub.oxidus and getattr(hub.oxidus, 'wikipedia_crawler', None):
        try:
            hub.oxidus.stop_wiki_crawl()
        except Exception:
            pass
hub._enter_safe_mode = _enter_safe_mode

def _exit_safe_mode() -> None:
    hub.SAFE_MODE.update({'active': False, 'reason': None, 'since': None, 'triggered_by': None})
    hub._log_telemetry('safe_mode_cleared')
hub._exit_safe_mode = _exit_safe_mode

def _block_if_safe_mode(action: str):
    if not hub.SAFE_MODE.get('active'):
        return None
    default_device_ids = hub.get_memryx_devices().get('device_ids') or []
    default_num_chips = max(len(default_device_ids), 1)
    return (jsonify({'success': False, 'error': 'Safe mode enabled', 'action': action, 'detail': hub.SAFE_MODE.get('reason')}), 423)
hub._block_if_safe_mode = _block_if_safe_mode

def _build_health_report(include_admin: bool=False) -> dict:
    slo = hub._load_reliability_slo()
    latency = hub._latency_stats()
    uptime_seconds = int(time.time() - hub.APP_STARTED_AT)
    crawler = None
    if hub.oxidus:
        try:
            crawler = hub.oxidus.wiki_crawl_status()
        except Exception:
            crawler = None
    lm_status = {'available': False}
    try:
        from src.utils.lm_studio_client import get_lm_studio_client
        client = get_lm_studio_client()
        client.ensure_model_selected()
        available = client.is_available()
        expected_model = client.primary_model
        active_model = client.model
        lm_status = {'available': available, 'model': active_model, 'expected_model': expected_model, 'model_match': bool(available and active_model == expected_model)}
    except Exception:
        lm_status = {'available': False}
    memryx_status = hub.get_memryx_status()
    memryx_devices = {'device_ids': [], 'source': 'unknown', 'errors': []}
    if include_admin:
        memryx_devices = hub.get_memryx_devices()
    storage = hub._storage_health()
    indexing = dict(hub.INDEXING_STATUS)
    index_meta = hub._load_index_meta()
    index_staleness_hours = None
    index_ts = hub._parse_indexed_at(index_meta) or index_meta.get('file_mtime')
    if index_ts:
        index_staleness_hours = round((time.time() - float(index_ts)) / 3600, 2)
    crawler_pages_per_min = None
    crawler_health = (crawler or {}).get('health') or {}
    started_at = (crawler or {}).get('started_at') or crawler_health.get('started_at')
    pages = (crawler or {}).get('pages_crawled') or 0
    if started_at and pages:
        elapsed_min = max((time.time() - started_at) / 60.0, 0.01)
        crawler_pages_per_min = round(pages / elapsed_min, 2)
    if crawler_health.get('stalled'):
        hub._enter_safe_mode('Crawler stalled', 'crawler')
    if indexing.get('last_error'):
        error_msg = indexing.get('last_error')
        if error_msg == 'No Indexing Available':
            if hub.SAFE_MODE.get('active') and hub.SAFE_MODE.get('triggered_by') == 'indexing':
                hub._exit_safe_mode()
        else:
            hub._enter_safe_mode(f'Indexing error: {error_msg}', 'indexing')
    alerts = []
    if hub.SAFE_MODE.get('active'):
        alerts.append({'code': 'safe_mode', 'level': 'critical', 'message': hub.SAFE_MODE.get('reason')})
        hub._maybe_record_alert('safe_mode', 'critical', hub.SAFE_MODE.get('reason') or 'Safe mode enabled')
    if crawler_health.get('stalled'):
        alerts.append({'code': 'crawler_stalled', 'level': 'critical', 'message': 'Crawler stalled'})
        hub._maybe_record_alert('crawler_stalled', 'critical', 'Crawler stalled')
    elif crawler_health.get('last_error'):
        alerts.append({'code': 'crawler_error', 'level': 'warning', 'message': crawler_health.get('last_error')})
        hub._maybe_record_alert('crawler_error', 'warning', crawler_health.get('last_error'))
    if indexing.get('last_error'):
        error_msg = indexing.get('last_error')
        if error_msg == 'No Indexing Available':
            alerts.append({'code': 'indexing_info', 'level': 'info', 'message': error_msg})
        else:
            alerts.append({'code': 'indexing_error', 'level': 'critical', 'message': error_msg})
            hub._maybe_record_alert('indexing_error', 'critical', error_msg)
    if indexing.get('running') and indexing.get('processed_batches', 0) > 0 and (indexing.get('throughput_eps', 0) < slo.get('ingestion_throughput_eps_min', 0)):
        alerts.append({'code': 'indexing_throughput', 'level': 'warning', 'message': 'Indexing throughput below SLO'})
        hub._maybe_record_alert('indexing_throughput', 'warning', 'Indexing throughput below SLO')
    if not indexing.get('running') and index_staleness_hours is not None:
        threshold = float(slo.get('index_staleness_hours', 0) or 0)
        if threshold > 0 and index_staleness_hours > threshold:
            alerts.append({'code': 'index_staleness', 'level': 'warning', 'message': 'Index staleness above threshold'})
            hub._maybe_record_alert('index_staleness', 'warning', 'Index staleness above threshold', {'staleness_hours': index_staleness_hours, 'threshold_hours': threshold})
    if crawler_health.get('running') and crawler_pages_per_min is not None:
        if crawler_pages_per_min < slo.get('crawler_pages_per_min_min', 0):
            alerts.append({'code': 'crawler_throughput', 'level': 'warning', 'message': 'Crawler throughput below SLO'})
            hub._maybe_record_alert('crawler_throughput', 'warning', 'Crawler throughput below SLO')
    if latency.get('p95_ms') is not None and latency.get('p95_ms') > slo.get('api_latency_p95_ms', 0):
        alerts.append({'code': 'latency_p95', 'level': 'warning', 'message': 'API latency above SLO'})
        hub._maybe_record_alert('latency_p95', 'warning', 'API latency above SLO')
    if storage.get('free_gb') is not None and storage.get('free_gb') < slo.get('storage_free_gb_min', 0):
        alerts.append({'code': 'storage_low', 'level': 'critical', 'message': 'Storage free space below SLO'})
        hub._maybe_record_alert('storage_low', 'critical', 'Storage free space below SLO')
        hub._enter_safe_mode('Storage free space below SLO', 'storage')
    if not lm_status.get('available'):
        alerts.append({'code': 'lm_offline', 'level': 'warning', 'message': 'LM Studio offline'})
        hub._maybe_record_alert('lm_offline', 'warning', 'LM Studio offline')
    elif not lm_status.get('model_match'):
        expected = lm_status.get('expected_model') or 'unknown'
        active = lm_status.get('model') or 'unknown'
        message = f'LM Studio model mismatch (expected {expected}, got {active})'
        alerts.append({'code': 'lm_model_mismatch', 'level': 'warning', 'message': message})
        hub._maybe_record_alert('lm_model_mismatch', 'warning', message, {'expected_model': expected, 'active_model': active})
    if include_admin and (not (memryx_devices.get('device_ids') or [])):
        alerts.append({'code': 'memryx_devices', 'level': 'warning', 'message': 'No MemryX devices detected'})
        hub._maybe_record_alert('memryx_devices', 'warning', 'No MemryX devices detected')
    indexing['index_meta'] = index_meta
    indexing['staleness_hours'] = index_staleness_hours
    indexing['indexed_at_utc'] = hub._compute_index_staleness(index_meta).get('indexed_at_utc')
    return {'uptime_seconds': uptime_seconds, 'latency': latency, 'slo': slo, 'safe_mode': dict(hub.SAFE_MODE), 'alerts': alerts, 'crawler': crawler, 'indexing': indexing, 'lm_studio': lm_status, 'memryx': {'status': memryx_status, 'devices': memryx_devices if include_admin else None}, 'storage': storage, 'crawler_pages_per_min': crawler_pages_per_min, 'gui': {'running': True, 'port': 5000, 'mode': 'flask'}}
hub._build_health_report = _build_health_report

def get_memryx_status() -> dict:
    """Return MemryX installation and Python binding status."""
    root = Path(os.environ.get('MEMRYX_HOME', 'C:/Program Files/MemryX'))
    python_dir = root / 'python'
    binding_files = []
    if python_dir.exists():
        binding_files.extend(list(python_dir.glob('*.pyd')))
        binding_files.extend(list(python_dir.glob('*.so')))
    module_name = None
    module_importable = False
    module_path = None
    import_error = None
    prefer_mxa = any((p.name.lower().startswith('mxa.') for p in binding_files))
    import_order = ('mxa', 'memryx') if prefer_mxa else ('memryx', 'mxa')
    for name in import_order:
        try:
            module = importlib.import_module(name)
            module_name = name
            module_importable = True
            module_path = getattr(module, '__file__', None)
            import_error = None
            break
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'module_import', 'module': name, 'error_type': type(exc).__name__})
            import_error = 'Import failed'
    if not module_importable and python_dir.exists():
        if str(python_dir) not in sys.path:
            sys.path.insert(0, str(python_dir))
        for name in import_order:
            try:
                module = importlib.import_module(name)
                module_name = name
                module_importable = True
                module_path = getattr(module, '__file__', None)
                import_error = None
                break
            except Exception as exc:
                hub._log_telemetry('error', {'operation': 'module_import_syspath', 'module': name, 'error_type': type(exc).__name__})
                import_error = 'Import failed'
    return {'installed': root.exists() or module_importable, 'root': str(root), 'driver_present': (root / 'driver').exists(), 'python_binding': bool(binding_files) or module_importable, 'python_dir': str(python_dir), 'module_name': module_name, 'module_importable': module_importable, 'module_path': module_path, 'import_error': import_error}
hub.get_memryx_status = get_memryx_status

def get_memryx_devices() -> dict:
    """Attempt to list MX3 device IDs via runtime API, fallback to mx_bench."""

    def _truncate(text: Optional[str], limit: int=1200) -> Optional[str]:
        if text is None:
            return None
        value = str(text)
        return value if len(value) <= limit else value[:limit] + '...'

    def _find_mx_bench(root: Path) -> Optional[str]:
        if not root.exists():
            return None
        for name in ('mx_bench.exe', 'mx_bench'):
            try:
                for path in root.rglob(name):
                    if path.is_file():
                        return str(path)
            except Exception:
                continue
        return None

    def _prefer_wsl_mx_bench() -> bool:
        return os.environ.get('OXIDUS_WSL_MX_BENCH', '0').lower() in {'1', 'true', 'yes', 'on'}
    module = None
    errors = []
    attempts = []
    memryx_root = Path(os.environ.get('MEMRYX_HOME', 'C:/Program Files/MemryX'))
    python_dir = memryx_root / 'python'
    mx_bench_path = shutil.which('mx_bench')
    accl_bench_cmd = None
    if sys.platform == 'win32':
        candidate = memryx_root / 'acclBench' / 'acclBench.exe'
        accl_bench_cmd = str(candidate) if candidate.exists() else None
    else:
        accl_bench_cmd = shutil.which('acclBench')
    if not mx_bench_path:
        mx_bench_path = _find_mx_bench(memryx_root)
    if python_dir.exists() and str(python_dir) not in sys.path:
        sys.path.insert(0, str(python_dir))
    diagnostics = {'memryx_home': str(memryx_root), 'python_dir': str(python_dir), 'python_dir_exists': python_dir.exists(), 'python_dir_in_sys_path': str(python_dir) in sys.path, 'mx_bench_path': mx_bench_path, 'mx_bench_wsl': None, 'accl_bench_path': accl_bench_cmd or '', 'accl_bench_exists': bool(accl_bench_cmd), 'platform': sys.platform, 'python_executable': sys.executable}
    prefer_mxa = any((p.name.lower().startswith('mxa.') for p in python_dir.glob('*.pyd'))) if python_dir.exists() else False
    import_order = ('mxa', 'memryx') if prefer_mxa else ('memryx', 'mxa')
    module = None
    for name in import_order:
        try:
            module = __import__(name)
            attempts.append({'name': f'import_{name}', 'success': True})
            diagnostics['module_path'] = getattr(module, '__file__', None)
            break
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'module_import', 'module': name, 'error_type': type(exc).__name__})
            attempts.append({'name': f'import_{name}', 'success': False, 'error': 'Import failed'})
    if module is None and python_dir.exists():
        diagnostics['python_dir_in_sys_path'] = str(python_dir) in sys.path
        for name in import_order:
            try:
                module = __import__(name)
                attempts.append({'name': f'import_{name}_from_memryx_home', 'success': True})
                diagnostics['module_path'] = getattr(module, '__file__', None)
                break
            except Exception as exc:
                hub._log_telemetry('error', {'operation': 'module_import', 'module': name, 'error_type': type(exc).__name__})
                attempts.append({'name': f'import_{name}_from_memryx_home', 'success': False, 'error': 'Import failed'})
                errors.append(f'{name} import failed')
        if module is None:
            module = None

    def _try_call(obj, names):
        for name in names:
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    continue
        return None
    device_ids = None
    source = 'unknown'
    if module:
        device_ids = _try_call(module, ['get_device_ids', 'list_devices', 'device_ids'])
        if device_ids is None and hasattr(module, 'accl'):
            device_ids = _try_call(module.accl, ['get_device_ids', 'list_devices', 'device_ids'])
        if device_ids is not None:
            source = 'runtime'
            attempts.append({'name': 'runtime_device_query', 'success': True})
        else:
            attempts.append({'name': 'runtime_device_query', 'success': False, 'error': 'no device ids returned'})
    if device_ids is None and mx_bench_path:
        try:
            cmd = [mx_bench_path, '--hello']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                ids = []
                for line in result.stdout.splitlines():
                    match = re.match('\\s*(\\d+)\\s*\\|', line)
                    if match:
                        ids.append(int(match.group(1)))
                if ids:
                    device_ids = ids
                    source = 'mx_bench'
                    attempts.append({'name': 'mx_bench', 'success': True, 'returncode': result.returncode})
                else:
                    attempts.append({'name': 'mx_bench', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
            else:
                errors.append(result.stderr.strip() or 'mx_bench failed')
                attempts.append({'name': 'mx_bench', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'mx_bench', 'error_type': type(exc).__name__})
            errors.append('mx_bench failed')
            attempts.append({'name': 'mx_bench', 'success': False, 'error': 'Benchmark failed'})
    if device_ids is None:
        try:
            if accl_bench_cmd:
                result = subprocess.run([accl_bench_cmd, '--hello'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    ids = []
                    for line in result.stdout.splitlines():
                        match = re.match('\\s*(\\d+)\\s*\\|', line)
                        if match:
                            ids.append(int(match.group(1)))
                    if ids:
                        device_ids = ids
                        source = 'acclBench'
                        attempts.append({'name': 'acclBench', 'success': True, 'returncode': result.returncode})
                    else:
                        attempts.append({'name': 'acclBench', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
                else:
                    errors.append(result.stderr.strip() or 'acclBench failed')
                    attempts.append({'name': 'acclBench', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
            else:
                attempts.append({'name': 'acclBench', 'success': False, 'error': 'acclBench not found'})
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'acclBench', 'error_type': type(exc).__name__})
            errors.append('acclBench not available')
            attempts.append({'name': 'acclBench', 'success': False, 'error': 'Benchmark failed'})
    if device_ids is None and sys.platform == 'win32' and _prefer_wsl_mx_bench():
        try:
            venv = os.environ.get('OXIDUS_WSL_MX_VENV', '').strip() or '$HOME/mx'
            activate = f'source {venv}/bin/activate >/dev/null 2>&1 || true'
            cmd = f'{activate} && mx_bench --hello'
            result = subprocess.run(['wsl', '-e', 'bash', '-lc', cmd], capture_output=True, text=True, timeout=15)
            diagnostics['mx_bench_wsl'] = 'wsl mx_bench --hello'
            if result.returncode == 0:
                ids = []
                for line in result.stdout.splitlines():
                    match = re.match('\\s*(\\d+)\\s*\\|', line)
                    if match:
                        ids.append(int(match.group(1)))
                if ids:
                    device_ids = ids
                    source = 'mx_bench_wsl'
                    attempts.append({'name': 'mx_bench_wsl', 'success': True, 'returncode': result.returncode})
                else:
                    attempts.append({'name': 'mx_bench_wsl', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
            else:
                errors.append(result.stderr.strip() or 'mx_bench failed in WSL')
                attempts.append({'name': 'mx_bench_wsl', 'success': False, 'returncode': result.returncode, 'stdout': _truncate(result.stdout), 'stderr': _truncate(result.stderr)})
        except FileNotFoundError as exc:
            hub._log_telemetry('error', {'operation': 'mx_bench_wsl', 'error_type': 'FileNotFoundError'})
            errors.append('wsl not available for mx_bench')
            attempts.append({'name': 'mx_bench_wsl', 'success': False, 'error': 'WSL not available'})
        except Exception as exc:
            hub._log_telemetry('error', {'operation': 'mx_bench_wsl', 'error_type': type(exc).__name__})
            errors.append('mx_bench not available in WSL')
            attempts.append({'name': 'mx_bench_wsl', 'success': False, 'error': 'Benchmark failed'})
    if device_ids:
        errors = [e for e in errors if 'mx_bench' not in e.lower() and 'wsl' not in e.lower()]
    return {'device_ids': device_ids or [], 'source': source, 'errors': errors, 'attempts': attempts, 'diagnostics': diagnostics}
hub.get_memryx_devices = get_memryx_devices

def _load_index_meta() -> dict:
    meta_path = hub.MEMRYX_INDEX_DIR / 'index_meta.json'
    if not meta_path.exists():
        return {}
    try:
        with meta_path.open('r', encoding='utf-8') as f:
            meta = json.load(f)
        try:
            meta['file_mtime'] = meta_path.stat().st_mtime
        except Exception:
            meta.setdefault('file_mtime', None)
        return meta
    except Exception:
        return {}
hub._load_index_meta = _load_index_meta

def _parse_indexed_at(index_meta: dict) -> Optional[float]:
    indexed_at = index_meta.get('indexed_at') or index_meta.get('updated_utc')
    if isinstance(indexed_at, (int, float)):
        return float(indexed_at)
    if isinstance(indexed_at, str):
        raw = indexed_at.strip()
        if raw:
            try:
                return datetime.strptime(raw, '%Y-%m-%d %H:%M:%S').timestamp()
            except Exception:
                pass
            try:
                if raw.endswith('Z'):
                    raw = raw[:-1] + '+00:00'
                return datetime.fromisoformat(raw).timestamp()
            except Exception:
                pass
    return None
hub._parse_indexed_at = _parse_indexed_at

def _compute_index_staleness(index_meta: dict) -> dict:
    index_ts = hub._parse_indexed_at(index_meta) or index_meta.get('file_mtime')
    if not index_ts:
        return {'indexed_at_utc': None, 'staleness_hours': None}
    index_ts = float(index_ts)
    indexed_at_utc = datetime.fromtimestamp(index_ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    staleness_hours = round((time.time() - index_ts) / 3600, 2)
    return {'indexed_at_utc': indexed_at_utc, 'staleness_hours': staleness_hours}
hub._compute_index_staleness = _compute_index_staleness

def _log_indexing(message: str) -> None:
    try:
        log_dir = hub.PROJECT_ROOT / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / 'memryx_indexing.log'
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with log_path.open('a', encoding='utf-8') as log:
            log.write(f'[{timestamp}] {message}\n')
    except Exception:
        pass
hub._log_indexing = _log_indexing

def _run_memryx_indexing(payload: dict) -> None:
    hub.INDEXING_STATUS['running'] = True
    hub.INDEXING_STATUS['last_error'] = None
    hub.INDEXING_STATUS['last_result'] = None
    hub.INDEXING_STATUS['processed_batches'] = 0
    hub.INDEXING_STATUS['total_batches'] = 0
    hub.INDEXING_STATUS['throughput_eps'] = 0.0
    hub.INDEXING_STATUS['started_at'] = time.time()
    hub.INDEXING_STATUS['avg_batch_ms'] = 0.0
    hub.INDEXING_STATUS['device_ids'] = []
    hub.INDEXING_STATUS['priority_paths'] = []
    hub.INDEXING_STATUS['batch_delay_ms'] = 0
    hub._log_indexing('Indexing started')
    if hub.MEMRYX_TUNED_ONNX.exists():
        default_onnx = hub.MEMRYX_TUNED_ONNX
        default_dfp = hub.MEMRYX_TUNED_DFP
        default_model = str(hub.MEMRYX_TUNED_DIR)
    else:
        default_onnx = hub.MEMRYX_ONNX_PATH
        default_dfp = hub.MEMRYX_DFP_PATH
        default_model = hub.MEMRYX_MODEL_ID
    model_id = payload.get('model_id') or default_model
    onnx_path = Path(payload.get('onnx_path') or default_onnx)
    dfp_path = Path(payload.get('dfp_path') or default_dfp)
    data_root = Path(payload.get('data_root') or hub.MEMRYX_DATA_ROOT)
    output_dir = Path(payload.get('output_dir') or hub.MEMRYX_INDEX_DIR)
    max_tokens = int(payload.get('max_tokens') or 192)
    batch_size = int(payload.get('batch_size') or 48)
    device_ids = payload.get('device_ids') or []
    if not device_ids:
        device_ids = hub.get_memryx_devices().get('device_ids') or []
    if not device_ids:
        device_ids = [0]
    num_chips = int(payload.get('num_chips') or max(len(device_ids), 1))
    prefer_async = bool(payload.get('prefer_async', True))
    priority_paths = payload.get('priority_paths') or hub._build_index_priority_paths()
    batch_delay_ms = float(payload.get('batch_delay_ms') or 0)
    batch_delay_s = max(batch_delay_ms, 0) / 1000.0
    hub.INDEXING_STATUS['device_ids'] = device_ids
    hub.INDEXING_STATUS['priority_paths'] = priority_paths
    hub.INDEXING_STATUS['batch_delay_ms'] = batch_delay_ms
    try:

        def _should_fallback_to_cpu(error_text: str) -> bool:
            text = (error_text or '').lower()
            markers = ['unsupported nodes', 'operatorerror', 'mx_nc not found', 'decodeerror', 'compilererror']
            return any((marker in text for marker in markers))
        if not onnx_path.exists():
            export_onnx(model_id=model_id, onnx_path=onnx_path, max_tokens=max_tokens)
        force_cpu = os.environ.get('OXIDUS_FORCE_CPU', '').lower() in ('1', 'true', 'yes', 'on')
        use_memryx = not force_cpu
        if force_cpu:
            hub._log_indexing('CPU mode forced via OXIDUS_FORCE_CPU environment variable')
        if use_memryx and (not dfp_path.exists()):
            try:
                compile_to_dfp(onnx_path, dfp_path, num_chips=num_chips, max_tokens=max_tokens)
            except Exception as compile_exc:
                error_type = type(compile_exc).__name__
                error_msg = str(compile_exc)
                if isinstance(compile_exc, MemryxIndexingError) or _should_fallback_to_cpu(error_type) or _should_fallback_to_cpu(error_msg):
                    use_memryx = False
                    hub._log_indexing(f'MemryX compile fallback to CPU: {error_type}')
                else:
                    raise

        def _progress(processed, total, elapsed):
            hub.INDEXING_STATUS['processed_batches'] = processed
            hub.INDEXING_STATUS['total_batches'] = total
            if elapsed > 0:
                hub.INDEXING_STATUS['throughput_eps'] = round(processed * batch_size / elapsed, 2)
            hub.INDEXING_STATUS['avg_batch_ms'] = round(elapsed / max(processed, 1) * 1000, 2)
            if processed == 1 or processed % 10 == 0 or processed == total:
                hub._log_indexing(f'Progress {processed}/{total} | {hub.INDEXING_STATUS['throughput_eps']} eps | {hub.INDEXING_STATUS['avg_batch_ms']} ms/batch')
        try:
            result = build_index(model_id=model_id, onnx_path=onnx_path, dfp_path=dfp_path, data_root=data_root, output_dir=output_dir, max_tokens=max_tokens, batch_size=batch_size, device_ids=device_ids, use_memryx=use_memryx, prefer_async=prefer_async, progress_cb=_progress, priority_paths=priority_paths, batch_delay_s=batch_delay_s)
        except Exception as run_exc:
            error_type = type(run_exc).__name__
            error_msg = str(run_exc)
            if use_memryx and (isinstance(run_exc, MemryxIndexingError) or _should_fallback_to_cpu(error_type) or _should_fallback_to_cpu(error_msg)):
                hub._log_indexing(f'MemryX runtime fallback to CPU: {error_type}')
                result = build_index(model_id=model_id, onnx_path=onnx_path, dfp_path=dfp_path, data_root=data_root, output_dir=output_dir, max_tokens=max_tokens, batch_size=batch_size, device_ids=device_ids, use_memryx=False, prefer_async=False, progress_cb=_progress, priority_paths=priority_paths, batch_delay_s=batch_delay_s)
            else:
                raise
        hub._log_indexing(f'Indexing done: {result.get('vectors', 0)} vectors | {result.get('dimensions', 0)} dim')
        hub.INDEXING_STATUS['last_result'] = result
        hub._invalidate_cache('ops_summary')
        hub._invalidate_cache('admin_ops_summary')
        hub._invalidate_cache('tiering_overview')
        hub._warm_ops_cache()
    except Exception as exc:
        hub._log_telemetry('error', {'operation': 'indexing', 'error_type': type(exc).__name__})
        if isinstance(exc, MemryxIndexingError):
            error_msg = str(exc).lower()
            if 'no documents' in error_msg or 'not found to index' in error_msg:
                hub.INDEXING_STATUS['last_error'] = 'No Indexing Available'
                hub._log_indexing('No documents found to index')
                if hub.SAFE_MODE.get('active') and hub.SAFE_MODE.get('triggered_by') == 'indexing':
                    hub._exit_safe_mode()
            else:
                hub.INDEXING_STATUS['last_error'] = 'Indexing failed'
                hub._log_indexing(f'Indexing error: {type(exc).__name__}')
                hub._enter_safe_mode(f'Indexing error: {type(exc).__name__}', 'indexing')
        else:
            hub.INDEXING_STATUS['last_error'] = 'Indexing failed'
            hub._log_indexing(f'Indexing error: {type(exc).__name__}')
            hub._enter_safe_mode(f'Indexing error: {type(exc).__name__}', 'indexing')
    finally:
        hub.INDEXING_STATUS['running'] = False
hub._run_memryx_indexing = _run_memryx_indexing

def run_maintenance_task(task: str, data: dict) -> dict:
    task_info = hub.MAINTENANCE_TASKS.get(task)
    if not task_info:
        return {'success': False, 'error': 'Unknown maintenance task'}
    if task_info.get('needs_confirm') and (not data.get('confirm')):
        return {'success': False, 'error': 'Confirmation required for this task'}
    script_path = hub.PROJECT_ROOT / 'scripts' / task_info['script']
    if not script_path.exists():
        return {'success': False, 'error': f'Script not found: {script_path}'}
    args = []
    data_dir = hub.PROJECT_ROOT / 'data'
    allowed_base = data_dir.resolve()
    scripts_dir = (hub.PROJECT_ROOT / 'scripts').resolve()
    script_path = script_path.resolve()
    if task == 'normalize_thought_stream':
        input_path = data.get('input_path') or hub._latest_thought_stream()
        output_path = data.get('output_path')
        if not input_path:
            return {'success': False, 'error': 'No thought stream file found'}
        input_resolved = hub._resolve_path_under_base(str(input_path), allowed_base)
        if not input_resolved:
            return {'success': False, 'error': 'Invalid input path'}
        args.append(str(input_resolved))
        if output_path:
            output_resolved = hub._resolve_path_under_base(str(output_path), allowed_base)
            if not output_resolved:
                return {'success': False, 'error': 'Invalid output path'}
            args.append(str(output_resolved))
    elif task == 'nlp_refine':
        root = data.get('root')
        if not root:
            return {'success': False, 'error': 'root is required'}
        root_resolved = hub._resolve_path_under_base(str(root), allowed_base)
        if not root_resolved:
            return {'success': False, 'error': 'Invalid root path'}
        args.extend(['--root', str(root_resolved)])
    elif task == 'refine_uncategorized':
        src = data.get('src')
        root = data.get('root')
        if not src or not root:
            return {'success': False, 'error': 'src and root are required'}
        src_resolved = hub._resolve_path_under_base(str(src), allowed_base)
        root_resolved = hub._resolve_path_under_base(str(root), allowed_base)
        if not src_resolved or not root_resolved:
            return {'success': False, 'error': 'Invalid path'}
        args.extend(['--src', str(src_resolved), '--root', str(root_resolved)])
    elif task == 'sort_knowledge':
        source = data.get('source')
        out_dir = data.get('out')
        if not source or not out_dir:
            return {'success': False, 'error': 'source and out are required'}
        source_resolved = hub._resolve_path_under_base(str(source), allowed_base)
        out_resolved = hub._resolve_path_under_base(str(out_dir), allowed_base)
        if not source_resolved or not out_resolved:
            return {'success': False, 'error': 'Invalid path'}
        args.extend(['--source', str(source_resolved), '--out', str(out_resolved)])
    try:
        if not hub._is_path_safe(script_path, scripts_dir):
            return {'success': False, 'error': 'Invalid script path'}
        dangerous_chars = set(';&|`$()<>{}[]!*?~')
        for arg in args:
            if any((char in str(arg) for char in dangerous_chars)):
                return {'success': False, 'error': 'Invalid argument characters'}
        result = subprocess.run([sys.executable, str(script_path), *args], capture_output=True, text=True, timeout=120, shell=False)
        result_payload = {'success': result.returncode == 0, 'returncode': result.returncode, 'stdout': result.stdout[-8000:], 'stderr': result.stderr[-4000:]}
        hub.LAST_MAINTENANCE_RESULT.update({'task': task, 'timestamp': datetime.now().isoformat(), **result_payload})
        return result_payload
    except subprocess.TimeoutExpired:
        result_payload = {'success': False, 'error': 'Task timed out'}
        hub.LAST_MAINTENANCE_RESULT.update({'task': task, 'timestamp': datetime.now().isoformat(), **result_payload})
        return result_payload
    except Exception as e:
        hub._log_telemetry('error', {'operation': 'maintenance_task', 'task': task, 'error_type': type(e).__name__})
        result_payload = {'success': False, 'error': 'Failed to complete maintenance task'}
        hub.LAST_MAINTENANCE_RESULT.update({'task': task, 'timestamp': datetime.now().isoformat(), **result_payload})
        return result_payload
hub.run_maintenance_task = run_maintenance_task

def _load_wiki_corpus_index() -> list:
    index_path = hub.PROJECT_ROOT / 'data' / 'knowledge_base' / 'metadata' / 'indexes' / 'wiki_file_topic_index.json'
    payload = hub._safe_load_json(index_path) or {}
    return payload.get('files') or []
hub._load_wiki_corpus_index = _load_wiki_corpus_index

def _build_ops_summary(include_admin: bool=False) -> dict:
    summary = hub.oxidus.thought_stream.get_thinking_summary() if hub.oxidus else {}
    lm_status = {'available': False}
    try:
        from src.utils.lm_studio_client import get_lm_studio_client
        client = get_lm_studio_client()
        lm_status = {'available': client.is_available(), 'model': client.model, 'primary_model': client.primary_model, 'secondary_model': client.secondary_model}
    except Exception:
        lm_status = {'available': False}
    memryx_status = hub.get_memryx_status()
    memryx_devices = {'device_ids': [], 'source': 'unknown', 'errors': []}
    if include_admin:
        memryx_devices = hub.get_memryx_devices()
    knowledge_summary = {'available': False, 'metrics': {}, 'coverage': {}, 'pipeline': {}, 'pipeline_health': {}, 'notes': {}, 'loose_ends': {}, 'governance': hub._load_kb_governance_summary()}
    if hub.oxidus and hub.oxidus.knowledge_organizer:
        try:
            organizer = hub.oxidus.knowledge_organizer
            pipeline = organizer.get_pipeline_summary()
            total_sources = pipeline.get('total_sources') or 0
            pipeline_health = {}
            if total_sources:
                pipeline_health = {'concepts_extracted_pct': round(pipeline.get('concepts_extracted', 0) / total_sources * 100, 1), 'values_mapped_pct': round(pipeline.get('values_mapped', 0) / total_sources * 100, 1), 'summaries_generated_pct': round(pipeline.get('summaries_generated', 0) / total_sources * 100, 1), 'indexed_pct': round(pipeline.get('indexed', 0) / total_sources * 100, 1)}
            loose_ends = organizer.get_loose_ends_report()
            coverage = organizer.get_coverage_report()
            freshness = organizer.get_freshness_report()
            knowledge_summary = {'available': True, 'metrics': {'total_sources': len(organizer.scraped_sources), 'total_concepts': len(organizer.concept_index)}, 'coverage': coverage, 'freshness': freshness, 'pipeline': pipeline, 'pipeline_health': pipeline_health, 'notes': {'total': len(organizer.list_notes()), 'open': len(organizer.list_notes(status='open'))}, 'loose_ends': {'open_threads': (loose_ends.get('open_threads') or [])[:8], 'underexplored_values': (loose_ends.get('underexplored_values') or [])[:8], 'review_queue': (loose_ends.get('review_queue') or [])[:6]}, 'governance': hub._load_kb_governance_summary()}
        except Exception:
            knowledge_summary = {'available': True, 'metrics': {}, 'coverage': {}, 'freshness': {}, 'pipeline': {}, 'pipeline_health': {}, 'notes': {}, 'loose_ends': {}, 'governance': hub._load_kb_governance_summary()}
    if include_admin:
        freshness = knowledge_summary.get('freshness') or {}
        global_freshness = freshness.get('global') or {}
        domain_freshness = freshness.get('domains') or {}
        stale_domains = sorted([{'domain': domain, 'age_hours': data.get('age_hours')} for domain, data in domain_freshness.items() if data.get('age_hours') is not None], key=lambda item: item.get('age_hours', 0), reverse=True)[:5]
        hub._maybe_record_snapshot('freshness_snapshot', 'freshness_snapshot', {'global_age_hours': global_freshness.get('age_hours'), 'global_last_ingest_at': global_freshness.get('last_ingest_at'), 'stale_domains': stale_domains, 'domain_count': len(domain_freshness)})
        index_meta = hub._load_index_meta()
        staleness = hub._compute_index_staleness(index_meta)
        hub._maybe_record_snapshot('index_staleness_snapshot', 'index_staleness_snapshot', {'staleness_hours': staleness.get('staleness_hours'), 'indexed_at_utc': staleness.get('indexed_at_utc')})
    payload = {'thoughts': {'total_thoughts': summary.get('total_thoughts', 0), 'questions': summary.get('total_questions', 0), 'decisions': summary.get('total_decisions', 0), 'ethical_checks': summary.get('ethical_checks', 0), 'insights': summary.get('insights_gained', 0)}, 'lm_studio': lm_status, 'memryx': {'status': memryx_status, 'devices': {'device_ids': memryx_devices.get('device_ids', []), 'source': memryx_devices.get('source'), 'errors': memryx_devices.get('errors', [])}}, 'knowledge': knowledge_summary, 'ops_mode': {'mode': hub.OPS_MODE.get('mode'), 'crawl_strategy': hub.OPS_MODE.get('crawl_strategy'), 'seed_strategy': hub.OPS_MODE.get('seed_strategy'), 'last_changed_at': hub.OPS_MODE.get('last_changed_at')}, 'reliability': {'slo': hub._load_reliability_slo(), 'latency': hub._latency_stats(), 'safe_mode': dict(hub.SAFE_MODE), 'alerts': hub._build_health_report(include_admin=False).get('alerts', []), 'storage': hub._storage_health()}, 'timestamp': datetime.now().isoformat()}
    if include_admin:
        payload['memryx']['health'] = hub._build_memryx_health_report()
        payload['indexing_metrics'] = hub._build_indexing_metrics()
        payload['tiering'] = hub._get_cached(hub._cache_key('tiering_overview'), 15.0, lambda: hub._build_tiering_overview(max_candidates=50))
    return payload
hub._build_ops_summary = _build_ops_summary

def _hot_index_roots() -> list:
    kb_root = hub._knowledge_base_root()
    return [kb_root / 'normalized_deeper' / 'sorted', kb_root / 'wiki_corpus', kb_root / 'notes']
hub._hot_index_roots = _hot_index_roots

def _build_index_priority_paths(limit: int=100) -> list:
    kb_root = hub._knowledge_base_root().resolve()
    paths = []
    for root in hub._hot_index_roots():
        if root.exists():
            paths.append(str(root.resolve()))
    heat = hub._load_access_heat()
    heat_paths = heat.get('paths') or {}
    ranked = sorted(heat_paths.items(), key=lambda item: int((item[1] or {}).get('count', 0) or 0), reverse=True)
    for rel_path, entry in ranked[:limit]:
        if not rel_path:
            continue
        candidate = hub._resolve_relative_path_under_base(str(rel_path), kb_root)
        if candidate and candidate.exists():
            paths.append(str(candidate))
    seen = set()
    deduped = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped
hub._build_index_priority_paths = _build_index_priority_paths

def _build_memryx_health_report() -> dict:
    status = hub.get_memryx_status()
    devices = hub.get_memryx_devices()
    diagnostics = devices.get('diagnostics') or {}
    hints = []
    if not status.get('installed'):
        hints.append('MemryX not detected on this Windows host. Install the Windows SDK/runtime or set MEMRYX_HOME.')
    if status.get('installed') and (not status.get('module_importable')):
        hints.append('Python binding not importable. Ensure MEMRYX_HOME/python is on PYTHONPATH or installed in this venv.')
    if not diagnostics.get('mx_bench_path') and diagnostics.get('accl_bench_exists') is False:
        hints.append('mx_bench not on PATH for this process (WSL installs are not visible to Windows).')
    if diagnostics.get('accl_bench_exists') is False:
        hints.append('acclBench.exe not found under MEMRYX_HOME/acclBench.')
    if not devices.get('device_ids'):
        hints.append('No devices detected. Check USB connection, driver install, and power.')
    return {'status': status, 'devices': devices, 'hints': hints, 'generated_at': datetime.now().isoformat()}
hub._build_memryx_health_report = _build_memryx_health_report

def _build_indexing_metrics() -> dict:
    index_meta = hub._load_index_meta()
    staleness = hub._compute_index_staleness(index_meta)
    return {'running': hub.INDEXING_STATUS.get('running', False), 'processed_batches': hub.INDEXING_STATUS.get('processed_batches', 0), 'total_batches': hub.INDEXING_STATUS.get('total_batches', 0), 'throughput_eps': hub.INDEXING_STATUS.get('throughput_eps', 0.0), 'avg_batch_ms': hub.INDEXING_STATUS.get('avg_batch_ms', 0.0), 'batch_delay_ms': hub.INDEXING_STATUS.get('batch_delay_ms', 0), 'last_error': hub.INDEXING_STATUS.get('last_error'), 'index_staleness_hours': staleness.get('staleness_hours'), 'index_last_built_utc': staleness.get('indexed_at_utc')}
hub._build_indexing_metrics = _build_indexing_metrics

def _is_path_safe(path: Path, base: Path) -> bool:
    """Verify that path is within the base directory (prevents path traversal)."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False
hub._is_path_safe = _is_path_safe
