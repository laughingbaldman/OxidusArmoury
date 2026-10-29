"""
Security fixes: make sure they hold and don't break functionality.

Run with pytest (normal) or directly: python test_security_fixes.py
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def gui():
    import web_gui
    return web_gui


@pytest.fixture(scope="module")
def base():
    path = ROOT / "data"
    path.mkdir(exist_ok=True)
    return path


def test_core_imports():
    from src.core.oxidus import Oxidus  # noqa: F401
    from src.utils.lm_studio_client import LMStudioClient  # noqa: F401


def test_path_validation(gui, base):
    assert gui._resolve_path_under_base("knowledge_base", base)
    assert not gui._resolve_path_under_base("../../../etc/passwd", base), "path traversal not blocked"
    assert gui._resolve_relative_path_under_base("knowledge_base/wiki_corpus", base)
    assert not gui._resolve_relative_path_under_base("/etc/passwd", base), "absolute path not blocked"


def test_query_topic_extraction(gui):
    assert isinstance(gui._extract_query_topics("tell me about machine learning and neural networks"), list)
    gui._extract_query_topics("a" * 20000)  # must not hang (ReDoS guard)
    assert gui._extract_query_topics("") == []


@pytest.mark.parametrize("url, expected", [
    ("https://en.wikipedia.org/wiki/Something", True),
    ("https://wikipedia.org/", True),
    ("https://en.wikipedia.org:443/wiki/Main_Page", True),
    ("https://wikipedia.org.evil.com/", False),
    ("https://notwikipedia.org/", False),
    ("https://evil.com/wikipedia.org", False),
    ("not a url", False),
])
def test_wikipedia_check(url, expected):
    """Spoofed Wikipedia domains must be rejected (this used to error: 'fixture url not found')."""
    from src.utils.url_safety import is_wikipedia_url
    assert is_wikipedia_url(url) is expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
