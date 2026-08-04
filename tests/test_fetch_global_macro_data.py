"""FRED 本地 CSV 导入功能的离线测试。"""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from scripts.fetch_global_macro_data import (
    _fetch_fred_local_csv,
    fetch_all,
    parse_args,
)

MODULE = "scripts.fetch_global_macro_data"

DGS10_ITEM = {
    "series_id": "DGS10",
    "api_code": "DGS10",
    "quality_level": "A",
}


def _config_content() -> str:
    return json.dumps({
        "version": 1,
        "series": [{
            "series_id": "DGS10",
            "source": "FRED",
            "api_code": "DGS10",
            "frequency": "daily",
            "unit": "percent",
            "available_at_rule": "realtime_start_or_next_business_day",
            "quality_level": "A",
            "role": "10年期名义国债收益率",
        }],
    }, ensure_ascii=False)


def _network_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "observation_date": ["2024-01-02", "2024-01-03"],
        "available_at": ["2024-01-03", "2024-01-04"],
        "value": [3.5, 3.4],
        "quality_level": ["A", "A"],
    })


class FetchGlobalMacroLocalCsvTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "global_data_sources.json"
        self.config.write_text(_config_content(), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_local_csv(self, series_id: str, content: str) -> Path:
        raw_dir = self.root / "raw" / "global_macro"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{series_id}.csv"
        path.write_text(content, encoding="utf-8")
        return path

    def _make_args(self, **overrides) -> argparse.Namespace:
        values = {
            "config": self.config,
            "start": "2024-01-01",
            "end": "2026-08-03",
            "output_dir": self.root,
            "series": None,
            "timeout": 5,
            "prefer_network": False,
            "local_only": False,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def _read_processed(self, series_id: str) -> pd.DataFrame:
        path = self.root / "processed" / "global_macro" / f"{series_id}.csv"
        self.assertTrue(path.exists(), f"processed CSV 未写出：{path}")
        return pd.read_csv(path)

    # ---- _fetch_fred_local_csv 直接单元测试 ----

    def test_local_csv_with_available_at_keeps_quality_a(self) -> None:
        path = self._write_local_csv("DGS10", (
            "observation_date,value,available_at\n"
            "2024-01-02,3.5,2024-01-03\n"
            "2024-01-03,3.4,2024-01-04\n"
        ))
        frame, raw, quality, warnings = _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertIsNone(raw)
        self.assertEqual(quality, "A")
        self.assertEqual(warnings, [])
        self.assertEqual(
            list(frame.columns),
            ["observation_date", "available_at", "value", "quality_level"],
        )
        self.assertEqual(frame["observation_date"].tolist(), ["2024-01-02", "2024-01-03"])
        self.assertEqual(frame["available_at"].tolist(), ["2024-01-03", "2024-01-04"])
        self.assertEqual(frame["value"].tolist(), [3.5, 3.4])

    def test_local_csv_without_available_at_synthesizes_quality_c(self) -> None:
        path = self._write_local_csv("DGS10", (
            "observation_date,value\n"
            "2024-01-02,3.5\n"
            "2024-01-03,3.4\n"
        ))
        frame, raw, quality, warnings = _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertEqual(quality, "C")
        self.assertEqual(frame["available_at"].tolist(), ["2024-01-03", "2024-01-04"])
        self.assertEqual(frame["quality_level"].tolist(), ["C", "C"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("降为 C", warnings[0])
        self.assertIn("下个工作日", warnings[0])

    def test_local_csv_with_realtime_start_quality_b(self) -> None:
        path = self._write_local_csv("DGS10", (
            "observation_date,realtime_start,value\n"
            "2024-01-02,2024-01-03,3.5\n"
            "2024-01-03,2024-01-04,3.4\n"
        ))
        frame, raw, quality, warnings = _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertEqual(quality, "B")
        self.assertEqual(frame["available_at"].tolist(), ["2024-01-03", "2024-01-04"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("降为 B", warnings[0])

    def test_local_csv_duplicate_dates_keep_last_and_crop_end(self) -> None:
        path = self._write_local_csv("DGS10", (
            "observation_date,value\n"
            "2024-01-02,3.5\n"
            "2024-01-02,3.6\n"
            "2024-01-03,3.4\n"
            "2026-12-31,9.9\n"
        ))
        frame, *_ = _fetch_fred_local_csv(DGS10_ITEM, path, end="2024-06-30")
        # 2026 未来行被裁剪；重复日期保留最后一条
        self.assertEqual(frame["observation_date"].tolist(), ["2024-01-02", "2024-01-03"])
        self.assertEqual(frame["value"].tolist(), [3.6, 3.4])

    def test_local_csv_missing_value_column_fails(self) -> None:
        path = self._write_local_csv("DGS10", "observation_date\n2024-01-02\n2024-01-03\n")
        with self.assertRaises(ValueError) as ctx:
            _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertIn("value", str(ctx.exception))

    def test_local_csv_missing_date_column_fails(self) -> None:
        path = self._write_local_csv("DGS10", "value\n3.5\n3.4\n")
        with self.assertRaises(ValueError) as ctx:
            _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertIn("日期列", str(ctx.exception))

    def test_local_csv_empty_file_fails(self) -> None:
        path = self._write_local_csv("DGS10", "observation_date,value\n")
        with self.assertRaises(ValueError) as ctx:
            _fetch_fred_local_csv(DGS10_ITEM, path, end="2026-08-03")
        self.assertIn("空文件", str(ctx.exception))

    # ---- fetch_all 本地优先与回退逻辑 ----

    def test_local_csv_preferred_over_network(self) -> None:
        self._write_local_csv("DGS10", (
            "observation_date,value\n"
            "2024-01-02,3.5\n"
            "2024-01-03,3.4\n"
        ))
        with mock.patch(f"{MODULE}._fetch_fred", side_effect=AssertionError("不应调用网络抓取")):
            manifest = fetch_all(self._make_args())
        result = manifest["results"][0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["import_mode"], "local_csv")
        self.assertEqual(result["quality_level"], "C")
        self.assertIn("warnings", result)
        self.assertIn("source_file", result)
        frame = self._read_processed("DGS10")
        self.assertEqual(frame["series_id"].tolist(), ["DGS10", "DGS10"])
        self.assertEqual(frame["available_at"].tolist(), ["2024-01-03", "2024-01-04"])
        # 本地模式不写 .raw
        self.assertFalse((self.root / "raw" / "global_macro" / "DGS10.raw").exists())

    def test_prefer_network_ignores_local_csv(self) -> None:
        self._write_local_csv("DGS10", (
            "observation_date,value\n"
            "2024-01-02,3.5\n"
        ))
        with mock.patch(f"{MODULE}._fetch_fred", return_value=(_network_frame(), b"raw-bytes", "A", [])) as fetch:
            manifest = fetch_all(self._make_args(prefer_network=True))
        fetch.assert_called_once()
        result = manifest["results"][0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["import_mode"], "network")
        self.assertTrue((self.root / "raw" / "global_macro" / "DGS10.raw").exists())

    def test_missing_local_csv_falls_back_to_network(self) -> None:
        with mock.patch(f"{MODULE}._fetch_fred", return_value=(_network_frame(), b"raw-bytes", "A", [])):
            manifest = fetch_all(self._make_args())
        result = manifest["results"][0]
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["import_mode"], "network")
        self.assertEqual(result["warnings"], [])
        self.assertTrue((self.root / "raw" / "global_macro" / "DGS10.raw").exists())

    def test_local_only_without_csv_fails_without_network(self) -> None:
        with mock.patch(f"{MODULE}._fetch_fred", side_effect=AssertionError("不应调用网络抓取")):
            manifest = fetch_all(self._make_args(local_only=True))
        result = manifest["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("FileNotFoundError", result["error"])
        self.assertIn("--local-only", result["error"])

    def test_broken_local_csv_fails_without_network_fallback(self) -> None:
        self._write_local_csv("DGS10", "observation_date\n2024-01-02\n")
        with mock.patch(f"{MODULE}._fetch_fred", side_effect=AssertionError("坏文件不应回退网络")):
            manifest = fetch_all(self._make_args())
        result = manifest["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertIn("value", result["error"])

    def test_parse_args_rejects_conflicting_flags(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--local-only", "--prefer-network"])

    def test_parse_args_accepts_each_flag_alone(self) -> None:
        self.assertTrue(parse_args(["--local-only"]).local_only)
        self.assertTrue(parse_args(["--prefer-network"]).prefer_network)
        self.assertFalse(parse_args([]).local_only)
        self.assertFalse(parse_args([]).prefer_network)

    # ---- _fetch_fred API 分支的 available_at 点内语义 ----

    def test_fetch_fred_api_uses_next_business_day_not_realtime_start(self) -> None:
        """FRED API 最新版查询会把 realtime_start 回填为查询日，不能用作 available_at。"""
        from scripts.fetch_global_macro_data import _fetch_fred

        # 模拟 FRED API 最新版响应：realtime_start 全部是查询日 2026-08-04
        observations = [
            {"realtime_start": "2026-08-04", "realtime_end": "2026-08-04", "date": "2024-01-02", "value": "3.95"},
            {"realtime_start": "2026-08-04", "realtime_end": "2026-08-04", "date": "2024-01-03", "value": "3.92"},
            {"realtime_start": "2026-08-04", "realtime_end": "2026-08-04", "date": "2024-01-04", "value": "3.90"},
        ]
        with mock.patch(f"{MODULE}._request_json", return_value={"observations": observations}), \
             mock.patch.dict("os.environ", {"FRED_API_KEY": "dummy"}):
            frame, _, quality, _ = _fetch_fred(DGS10_ITEM, start="2024-01-01", end="2024-06-30", timeout=5)

        # available_at 必须是观测日的下一工作日，而不是 realtime_start 查询日
        self.assertEqual(quality, "A")
        self.assertEqual(frame["available_at"].tolist(), ["2024-01-03", "2024-01-04", "2024-01-05"])
        self.assertEqual(frame["observation_date"].tolist(), ["2024-01-02", "2024-01-03", "2024-01-04"])


if __name__ == "__main__":
    unittest.main()
