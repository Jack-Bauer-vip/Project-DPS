"""reference/metadata：元数据头与新鲜度校验测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.reference.metadata import (
    build_header,
    embed_header_any,
    embed_header_csv,
    now_iso,
    parse_header_csv,
    today_iso,
    validate_freshness,
    write_parquet_with_meta,
)


class MetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_csv_header_roundtrip(self) -> None:
        path = self.root / "out.csv"
        frame = pd.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
        header = build_header(generated_date="2026-08-06", data_asof="2026-08-05")
        embed_header_csv(path, header, frame)
        parsed = parse_header_csv(path)
        self.assertEqual(parsed["generated_date"], "2026-08-06")
        self.assertEqual(parsed["data_asof"], "2026-08-05")
        read_back = pd.read_csv(path, comment="#")
        self.assertEqual(len(read_back), 2)

    def test_no_header_csv_returns_empty(self) -> None:
        path = self.root / "plain.csv"
        pd.DataFrame({"a": [1]}).to_csv(path, index=False)
        self.assertEqual(parse_header_csv(path), {})

    def test_parquet_metadata_roundtrip(self) -> None:
        import pyarrow.parquet as pq

        path = self.root / "out.parquet"
        frame = pd.DataFrame({"a": [1, 2]})
        header = build_header(generated_date="2026-08-06", data_asof="2026-08-05")
        write_parquet_with_meta(path, frame, header)
        meta = pq.read_metadata(path).metadata
        self.assertEqual(meta[b"generated_date"].decode(), "2026-08-06")
        self.assertEqual(meta[b"data_asof"].decode(), "2026-08-05")

    def test_embed_header_any_json(self) -> None:
        path = self.root / "out.json"
        embed_header_any(
            path,
            {"generated_date": "2026-08-06", "data_asof": "2026-08-05"},
            {"assets": [1]},
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["generated_date"], "2026-08-06")
        self.assertEqual(payload["assets"], [1])

    def test_freshness_boundaries(self) -> None:
        self.assertTrue(validate_freshness("2026-08-04", ref_date="2026-08-06")[0])  # 恰好 2 天
        ok, reason = validate_freshness("2026-08-03", ref_date="2026-08-06")  # 3 天超阈值
        self.assertFalse(ok)
        self.assertIn("2", reason)
        self.assertFalse(validate_freshness("2026-08-07", ref_date="2026-08-06")[0])  # 未来日期
        self.assertFalse(validate_freshness("not-a-date", ref_date="2026-08-06")[0])  # 非法日期

    def test_today_and_now_iso(self) -> None:
        self.assertEqual(len(today_iso()), 10)
        self.assertIn("T", now_iso())
