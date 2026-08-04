"""Package a pre-neutralized momentum column into the factor Parquet layout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd


factor_id = "momentum_60d"
formula = "close.pct_change(60)"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--value-column", default="neutralized_value")
    return parser.parse_args()


def build(arguments: argparse.Namespace) -> Path:
    if "neutral" not in arguments.value_column.lower():
        raise ValueError("生产因子 Parquet 只接受已离线中性化的列，请使用包含 neutral 的字段名。")
    frame = pd.read_csv(arguments.input_csv)
    required = {"date", "asset_code", arguments.value_column}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"输入缺少字段：{sorted(missing)}")
    output = frame[["date", "asset_code", arguments.value_column]].rename(columns={arguments.value_column: "value"})
    output["available_at"] = output["date"]
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = arguments.output_dir / f"{factor_id}.parquet"
    output.to_parquet(output_path, index=False)
    manifest = {"factor_id": factor_id, "formula": formula, "formula_hash": hashlib.md5(formula.encode("utf-8")).hexdigest(), "value_semantics": "neutralized_exposure", "source_column": arguments.value_column}
    (arguments.output_dir / f"{factor_id}.manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


if __name__ == "__main__":
    arguments = parse_args()
    output_path = build(arguments)
    print(f"wrote {output_path}")
