"""Small URL checks shared by the core and tests."""
from urllib.parse import urlparse


def is_wikipedia_url(url: str) -> bool:
    """True only for wikipedia.org or a real *.wikipedia.org subdomain.

    Blocks look-alikes such as wikipedia.org.evil.com and notwikipedia.org.
    """
    try:
        domain = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    parts = domain.split(".")
    return domain == "wikipedia.org" or (len(parts) >= 3 and parts[-2] == "wikipedia" and parts[-1] == "org")
