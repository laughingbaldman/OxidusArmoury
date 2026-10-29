#!/usr/bin/env python3
"""Check a LoRA dataset and print the train command. Training itself needs a GPU."""

from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.training.lora_data import check_jsonl


def main() -> int:
    parser = argparse.ArgumentParser(description="Oxidus LoRA helper")
    parser.add_argument("--dataset", default=str(ROOT / "data" / "training" / "lora_dataset.jsonl"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--train", action="store_true")
    args = parser.parse_args()
    report = check_jsonl(args.dataset)
    print(report)
    command = (
        "python3 -m src.training.lora_train"
        f" --dataset {args.dataset} --output data/models/lora"
    )
    print("Train command (GPU, optional peft):")
    print(command)
    if args.train:
        print("This environment does not start a GPU train from --train. Use --check here.")
        return 2
    if not report["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
