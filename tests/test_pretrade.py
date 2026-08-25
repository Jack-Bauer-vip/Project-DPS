"""ETF/股票自动投前研究引擎的离线回归测试。"""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import numpy as np
import pandas as pd

from qteasy_research.pretrade.metrics import analyze_benchmark, analyze_price_history
from qteasy_research.pretrade.storage import ResearchStore
from qteasy_research.pretrade.technical import analyze_technical_indicators
from qteasy_research.pretrade.llm import validate_evidence
from qteasy_research.pretrade.llm import EvidenceResponse
from qteasy_research.pretrade.orchestrator import continue_research, run_instrument_research
from qteasy_research.pretrade.providers import ProviderData
from qteasy_research.pretrade.schemas import AssetIdentity
from qteasy_research.pretrade.projects import (
    add_project_decision,
    add_project_note,
    add_asset_reference,
    close_research_project,
    compare_research_versions,
    create_research_project,
    create_strategy_project,
    delete_asset_reference,
    delete_project_note,
    get_research_project,
    list_project_notes,
    list_project_versions,
    list_asset_references,
    reopen_research_project,
    update_research_project,
)
from qteasy_research.pretrade.symbols import infer_asset_type, normalize_code


class SymbolTests(unittest.TestCase):
    def test_normalize_codes(self) -> None:
        self.assertEqual(normalize_code("518880"), ("518880.SH", "SH"))
        self.assertEqual(normalize_code("600519.SZ"), ("600519.SZ", "SZ"))
        self.assertEqual(infer_asset_type("518880.SH"), "ETF")
        self.assertEqual(infer_asset_type("600519.SH"), "STOCK")


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        dates = pd.date_range("2020-01-01", periods=300, freq="B")
        close = 100 * np.cumprod(np.full(300, 1.001))
        self.frame = pd.DataFrame({"trade_date": dates, "close": close, "amount": 1000.0})

    def test_metrics_have_sample_gating_and_windows(self) -> None:
        result = analyze_price_history(self.frame)
        self.assertEqual(result["quality"]["sample_days"], 299)
        self.assertIsNotNone(result["windows"]["1y"]["annual_return"])
        self.assertEqual(result["windows"]["1m"]["confidence"], "low")
        self.assertIsNotNone(result["var95"])

    def test_benchmark_alignment(self) -> None:
        benchmark = self.frame.copy()
        benchmark["close"] = benchmark["close"] * 0.9
        result = analyze_benchmark(self.frame, benchmark)
        self.assertTrue(result["available"])
        self.assertEqual(result["overlap_days"], 299)
        self.assertIsNotNone(result["correlation"])


class TechnicalIndicatorTests(unittest.TestCase):
    def setUp(self) -> None:
        dates = pd.date_range("2020-01-01", periods=320, freq="B")
        close = 100 * np.cumprod(1 + np.linspace(-0.002, 0.003, 320))
        self.frame = pd.DataFrame({
            "trade_date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "amount": 100000.0,
        })

    def test_supported_indicators_and_composite_scores(self) -> None:
        result = analyze_technical_indicators(self.frame)
        self.assertEqual(result["formula_version"], "technical-v1")
        self.assertEqual(set(result["indicators"]), {"macd", "rsi", "atr", "adx", "bollinger"})
        self.assertIn("trend", result["composite_scores"])
        self.assertIn("momentum", result["composite_scores"])
        self.assertIn("risk", result["composite_scores"])
        self.assertNotIn("kdj", result["indicators"])

    def test_short_history_returns_unavailable_metrics(self) -> None:
        result = analyze_technical_indicators(self.frame.head(10))
        self.assertEqual(result["indicators"]["macd"]["state"], "unavailable")
        self.assertIsNone(result["indicators"]["bollinger"]["middle"]["value"])


class LLMValidationTests(unittest.TestCase):
    def test_evidence_without_source_is_rejected(self) -> None:
        result = validate_evidence({
            "facts": [
                {"claim": "有来源的事实", "value": 1, "source_url": "https://example.com", "as_of": "2026-07-29"},
                {"claim": "无来源的事实", "value": 2},
            ],
        })
        self.assertEqual(len(result.facts), 1)
        self.assertTrue(result.missing_items)


class OfflineResearchTests(unittest.TestCase):
    def test_local_etf_research_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = run_instrument_research(
                "518880",
                network_research=False,
                llm_provider=None,
                force_refresh=True,
                output_dir=directory,
            )
            self.assertIn(first.run_status, {"PARTIAL", "COMPLETED"})
            self.assertEqual(first.asset_identity.code, "518880.SH")
            self.assertEqual(first.asset_identity.asset_type, "ETF")
            self.assertTrue(first.asset_identity.name)
            self.assertTrue(Path(first.artifacts["report"]).exists())
            self.assertTrue(Path(first.artifacts["normalized_price"]).exists())
            self.assertTrue(Path(first.artifacts["drawdown"]).exists())
            self.assertTrue(Path(first.artifacts["result"]).exists())
            self.assertTrue(first.benchmark_analysis["available"])

            second = run_instrument_research(
                "518880.SH",
                network_research=False,
                llm_provider=None,
                output_dir=directory,
            )
            self.assertEqual(second.run_id, first.run_id)
            self.assertEqual(second.asset_identity.code, first.asset_identity.code)

    def test_offline_mode_does_not_call_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_instrument_research(
                "518880.SH",
                network_research=False,
                llm_provider="ollama",
                output_dir=directory,
                force_refresh=True,
            )
            self.assertIn("离线模式已跳过模型补研", result.missing_items)


if __name__ == "__main__":
    unittest.main()


class TechnicalUpdateTests(unittest.TestCase):
    def test_new_market_date_creates_project_version(self) -> None:
        dates = pd.date_range("2025-01-01", periods=80, freq="B")
        values = 100 * np.cumprod(np.full(80, 1.001))

        class FakeLocalCsvProvider:
            name = "local_csv_fixture"
            frame = pd.DataFrame({
                "trade_date": dates,
                "open": values,
                "high": values * 1.01,
                "low": values * 0.99,
                "close": values,
                "amount": 100000.0,
            })

            def __init__(self):
                self.data_dir = Path(tempfile.gettempdir())

            def resolve(self, raw_code):
                return AssetIdentity(code="518880.SH", asset_type="ETF", exchange="SH", benchmark="000300.SH", resolved=True)

            def get_price_history(self, identity):
                return ProviderData(data=self.frame.copy(), source=self.name, as_of=self.frame["trade_date"].max().strftime("%Y-%m-%d"))

            def get_metadata(self, identity):
                return ProviderData(data=pd.DataFrame([{"ts_code": identity.code, "name": "Fixture ETF"}]), source=self.name)

            def get_benchmark_history(self, code):
                return ProviderData(data=self.frame[["trade_date", "close"]].copy(), source=self.name, as_of=self.frame["trade_date"].max().strftime("%Y-%m-%d"))

        with tempfile.TemporaryDirectory() as directory, \
                patch("qteasy_research.pretrade.orchestrator.LocalCsvProvider", FakeLocalCsvProvider), \
                patch("qteasy_research.pretrade.orchestrator.DProvider") as mock_d_provider:
            # 隔离 D 数据中台：本测试只验证「本地数据更新 → check_update 出新版本」，
            # 不让 D（首位 provider）抢先命中导致本地帧变更被掩盖。
            mock_d_provider.return_value.get_price_history.return_value = ProviderData()
            mock_d_provider.return_value.get_benchmark_history.return_value = ProviderData()
            mock_d_provider.return_value.get_metadata.return_value = ProviderData()
            project = create_research_project("fixture", "518880.SH", output_dir=directory)
            first = run_instrument_research(
                "518880.SH",
                network_research=False,
                force_refresh=True,
                output_dir=directory,
                project_id=project.project_id,
            )
            FakeLocalCsvProvider.frame = pd.concat([
                FakeLocalCsvProvider.frame,
                pd.DataFrame([{
                    "trade_date": pd.Timestamp("2025-04-24"),
                    "open": values[-1] * 1.001,
                    "high": values[-1] * 1.011,
                    "low": values[-1] * 0.991,
                    "close": values[-1] * 1.001,
                    "amount": 100000.0,
                }]),
            ], ignore_index=True)
            second = run_instrument_research(
                "518880.SH",
                network_research=False,
                update_policy="check_update",
                output_dir=directory,
                project_id=project.project_id,
            )
            self.assertEqual(second.version_no, 2)
            self.assertEqual(second.parent_run_id, first.run_id)
            self.assertTrue(Path(first.artifacts["result"]).exists())

    def test_check_update_reuses_unchanged_technical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = run_instrument_research(
                "518880.SH",
                network_research=False,
                force_refresh=True,
                output_dir=directory,
            )
            checked = run_instrument_research(
                "518880.SH",
                network_research=False,
                update_policy="check_update",
                output_dir=directory,
            )
            self.assertEqual(first.run_id, checked.run_id)
            self.assertTrue(checked.cache_status["update_checked"])
            self.assertEqual(len(ResearchStore(directory).list_technical_snapshots("518880.SH")), 1)

    def test_indicator_config_change_creates_new_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first = run_instrument_research(
                "518880.SH",
                network_research=False,
                force_refresh=True,
                output_dir=directory,
            )
            second = run_instrument_research(
                "518880.SH",
                network_research=False,
                indicator_config={"rsi_period": 21},
                output_dir=directory,
            )
            self.assertNotEqual(first.run_id, second.run_id)
            self.assertNotEqual(
                first.cache_status["technical_config_hash"],
                second.cache_status["technical_config_hash"],
            )
            self.assertEqual(len(ResearchStore(directory).list_technical_snapshots("518880.SH")), 2)
            self.assertTrue(Path(first.artifacts["result"]).exists())


class ResearchProjectTests(unittest.TestCase):
    def test_project_versions_cache_and_crud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = create_research_project("黄金 ETF 观察", "518880", "验证中期配置价值", output_dir=directory)
            first = run_instrument_research(
                project.code,
                network_research=False,
                llm_provider=None,
                force_refresh=True,
                output_dir=directory,
                project_id=project.project_id,
            )
            self.assertEqual(first.version_no, 1)
            self.assertIsNone(first.parent_run_id)
            self.assertTrue(first.is_frozen)
            old_result_path = Path(first.artifacts["result"])
            old_result_text = old_result_path.read_text(encoding="utf-8")

            reused = run_instrument_research(
                project.code,
                network_research=False,
                llm_provider=None,
                output_dir=directory,
                project_id=project.project_id,
                update_policy="reuse",
            )
            self.assertEqual(reused.run_id, first.run_id)
            self.assertTrue(reused.cache_status["run_reused"])

            second = run_instrument_research(
                project.code,
                network_research=False,
                llm_provider=None,
                output_dir=directory,
                project_id=project.project_id,
                update_policy="refresh",
            )
            self.assertEqual(second.version_no, 2)
            self.assertEqual(second.parent_run_id, first.run_id)
            self.assertNotEqual(second.run_id, first.run_id)
            self.assertEqual(old_result_path.read_text(encoding="utf-8"), old_result_text)
            self.assertEqual(len(list_project_versions(project.project_id, output_dir=directory)), 2)
            comparison = compare_research_versions(first.run_id, second.run_id, output_dir=directory)
            self.assertIn("run_a", comparison)

            note = add_project_note(project.project_id, "观察黄金与实际利率的关系", output_dir=directory)
            self.assertEqual(len(list_project_notes(project.project_id, output_dir=directory)), 1)
            delete_project_note(note.note_id, output_dir=directory)
            self.assertEqual(list_project_notes(project.project_id, output_dir=directory), [])
            self.assertEqual(len(list_project_notes(project.project_id, include_deleted=True, output_dir=directory)), 1)
            add_project_decision(project.project_id, "暂不纳入正式资产池", output_dir=directory)

            closed = close_research_project(project.project_id, output_dir=directory)
            self.assertEqual(closed.status, "CLOSED")
            with self.assertRaises(ValueError):
                update_research_project(project.project_id, output_dir=directory, objective="不可直接修改")
            reopened = reopen_research_project(project.project_id, output_dir=directory)
            self.assertEqual(reopened.status, "ACTIVE")
            self.assertEqual(get_research_project(project.project_id, output_dir=directory).status, "ACTIVE")

            class FakeProvider:
                def research_evidence(self, request):
                    return EvidenceResponse(
                        facts=[{"claim": "测试补研事实", "value": True, "source_url": "https://example.com", "as_of": "2026-07-29", "official": False, "confidence": 0.8}],
                        conflicts=[],
                        missing_items=[],
                    )

            with patch("qteasy_research.pretrade.orchestrator._default_root", return_value=Path(directory)), \
                 patch("qteasy_research.pretrade.orchestrator.build_llm_provider", return_value=FakeProvider()):
                followup = continue_research(first.run_id, "补充一个测试问题", llm_provider="ollama")
            self.assertEqual(followup.parent_run_id, first.run_id)
            self.assertNotEqual(followup.run_id, first.run_id)
            self.assertEqual(old_result_path.read_text(encoding="utf-8"), old_result_text)

    def test_asset_profile_can_be_reused_by_strategy_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            asset = create_research_project("黄金档案", "518880.SH", output_dir=directory)
            run_instrument_research(
                asset.code,
                network_research=False,
                llm_provider=None,
                force_refresh=True,
                output_dir=directory,
                project_id=asset.project_id,
            )
            strategy = create_strategy_project("防御组合", "risk_parity", output_dir=directory)
            self.assertEqual(strategy.project_type, "STRATEGY_PORTFOLIO")
            reference = add_asset_reference(
                strategy.project_id,
                asset.project_id,
                asset_version_no=1,
                role="core",
                output_dir=directory,
            )
            references = list_asset_references(strategy.project_id, output_dir=directory)
            self.assertEqual(len(references), 1)
            self.assertEqual(references[0].asset_code, "518880.SH")
            self.assertEqual(references[0].asset_version_no, 1)
            delete_asset_reference(reference.reference_id, output_dir=directory)
            self.assertEqual(list_asset_references(strategy.project_id, output_dir=directory), [])
