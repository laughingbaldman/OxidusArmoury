#!/usr/bin/env python3
"""Write a LoRA chat dataset from answered questions and notes."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.training.lora_data import build_dataset, write_jsonl


def main() -> int:
    dest = ROOT / "data" / "training" / "lora_dataset.jsonl"
    count = write_jsonl(build_dataset(ROOT), dest)
    print(f"Wrote {count} rows to {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
