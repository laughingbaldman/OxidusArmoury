"""Oxidus web server. The pieces live under src/web."""
import os
import threading

from src.web.app import app
from src.web.hub import hub

init_oxidus = hub.init_oxidus
_is_path_safe = hub._is_path_safe
_resolve_path_under_base = hub._resolve_path_under_base
_resolve_relative_path_under_base = hub._resolve_relative_path_under_base
_extract_query_topics = hub._extract_query_topics

if __name__ == "__main__":
    threading.Thread(target=init_oxidus, daemon=True).start()
    debug_mode = os.environ.get("OXIDUS_DEBUG", "").lower() in ("true", "1", "yes")
    app.run(debug=debug_mode, port=5000, host="127.0.0.1")
