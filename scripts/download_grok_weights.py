#!/usr/bin/env python3
"""Tell Jonathon how to get Grok-1 weights. This script does not download 300 GB unless --download is passed.

Weights are Apache-2.0 and must stay out of git.
"""

from pathlib import Path
import argparse
import hashlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.grok_backend import WEIGHTS_NOTE, GrokBackend

MAGNET = (
    "magnet:?xt=urn:btih:5f96d43576e3d386c9ba65b883210a393b68210e"
    "&tr=https%3A%2F%2Facademictorrents.com%2Fannounce.php"
)
HF = "huggingface-cli download xai-org/grok-1 --repo-type model --include ckpt-0/* --local-dir data/models/grok-1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Grok-1 weight helper")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--verify", default="")
    args = parser.parse_args()
    print(WEIGHTS_NOTE)
    print()
    print("Magnet:")
    print(MAGNET)
    print()
    print("Hugging Face:")
    print(HF)
    print()
    status = GrokBackend(ROOT).status()
    print(f"Local status: {status['mode']}")
    if args.verify:
        path = Path(args.verify)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"sha256 {digest} {path}")
    if args.download:
        print("Refusing to start a 300 GB download from this helper. Use the Hugging Face command above on the machine that will hold the weights.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
