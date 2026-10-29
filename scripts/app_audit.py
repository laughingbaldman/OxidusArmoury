"""Crawl every route the Oxidus web app exposes and record what works.

GET routes without URL parameters are fetched from the running server
(default http://127.0.0.1:5000). Routes that change data (POST/PUT/DELETE)
are listed but not fired, so the audit never alters Oxidus's memory.

    python scripts/app_audit.py [--base URL] [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

STUB_HINTS = re.compile(r"not implemented|coming soon|todo:|placeholder|stub", re.I)
SKIP_GET = {"/static/<path:filename>"}
# GETs that start long jobs or stream forever.
SLOW_GET = re.compile(r"stream|events|sse|download|export|shutdown|restart", re.I)


def list_routes(base):
    """Ask the running server for its url_map (importing the app here is slow)."""
    import requests
    data = requests.get(base + "/api/app/routes", timeout=20).json()
    return data["routes"]


def classify(base, route, timeout):
    import requests
    rule = route["rule"]
    if "GET" not in route["methods"]:
        return "write", "not fired (changes data)", None
    if rule in SKIP_GET:
        return "skip", "static files", None
    if route["args"]:
        return "param", "needs " + ", ".join(route["args"]), None
    if SLOW_GET.search(rule):
        return "skip", "long-running or streaming; not fired", None
    t0 = time.time()
    try:
        r = requests.get(base + rule, timeout=timeout, stream=True)
        body = r.raw.read(200_000, decode_content=True) or b""
        r.close()
    except Exception as exc:  # timeout, connection reset
        return "broken", f"{type(exc).__name__}", round(time.time() - t0, 2)
    ms = round(time.time() - t0, 2)
    text = body.decode("utf-8", "replace")
    if r.status_code >= 500:
        return "broken", f"HTTP {r.status_code}: {text[:120]!r}", ms
    if r.status_code in (401, 403):
        return "working", f"HTTP {r.status_code} (admin token required)", ms
    if r.status_code == 404:
        return "broken", "HTTP 404", ms
    if r.status_code >= 400:
        return "broken", f"HTTP {r.status_code}: {text[:120]!r}", ms
    if r.status_code == 501 or STUB_HINTS.search(text[:4000]) and "json" in r.headers.get("content-type", ""):
        return "stub", f"HTTP {r.status_code}, looks unfinished", ms
    try:
        payload = json.loads(text)
        if isinstance(payload, dict) and payload.get("error"):
            return "broken", f"HTTP {r.status_code} error: {str(payload['error'])[:120]}", ms
    except ValueError:
        pass
    return "working", f"HTTP {r.status_code} {len(body)}B", ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--timeout", type=float, default=12)
    ap.add_argument("--json", default="logs/app_audit.json")
    a = ap.parse_args()
    routes = list_routes(a.base)
    for i, r in enumerate(routes, 1):
        status, note, secs = classify(a.base, r, a.timeout)
        r.update(status=status, note=note, seconds=secs)
        print(f"[{i}/{len(routes)}] {status:8} {','.join(r['methods']):10} {r['rule']}  {note}", flush=True)
    Path(a.json).parent.mkdir(exist_ok=True)
    Path(a.json).write_text(json.dumps(routes, indent=2), encoding="utf-8")
    counts = {}
    for r in routes:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("COUNTS", json.dumps(counts))


if __name__ == "__main__":
    main()
