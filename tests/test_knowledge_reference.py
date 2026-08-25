"""K 知识参考（研究起点）模块与研究主流程集成的回归测试。

覆盖：
- 查询词构造（去重 / 代码规范化）；
- K 可用命中卡片 → 研究输出记录知识参考；
- K 不可用 / 指错 BASE_URL / client 异常 / 查询空结果 → 优雅降级，不阻断研究；
- knowledge_reference=False 时完全跳过 K 查询。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from qteasy_research.pretrade.knowledge_reference import (
    build_queries,
    query_knowledge_reference,
)
from qteasy_research.pretrade.orchestrator import run_instrument_research
from qteasy_research.pretrade.providers import ProviderData
from qteasy_research.pretrade.schemas import AssetIdentity


def _hit(card_id: str, title: str, score: float, summary: str = "摘要") -> dict:
    return {
        "id": card_id,
        "title": title,
        "category": "测试/策略",
        "tags": ["黄金", "ETF"],
        "source_type": "pdf",
        "brief_summary": summary,
        "score": score,
    }


class FakeKnowledgeClient:
    """替身：is_ready + search 都受控，不触网。"""

    def __init__(self, *, ready: bool = True, items: list[dict] | None = None,
                 raise_on_search: bool = False):
        self._ready = ready
        self._items = items or []
        self._raise_on_search = raise_on_search

    def is_ready(self) -> bool:
        return self._ready

    def search(self, q: str, **kwargs) -> dict:
        if self._raise_on_search:
            raise RuntimeError("模拟 search 异常")
        return {"total": len(self._items), "query": q, "mode": "hybrid", "items": self._items}


class BuildQueriesTests(unittest.TestCase):
    def test_code_and_name_dedup(self) -> None:
        queries = build_queries("518880.SH", "黄金ETF", "黄金策略", "利率")
        self.assertEqual(queries[0], "518880.SH")
        self.assertEqual(queries[1], "518880")
        self.assertEqual(queries[2], "黄金ETF")
        self.assertEqual(queries[3], "黄金策略")
        self.assertEqual(queries[4], "利率")

    def test_dedup_keeps_first(self) -> None:
        queries = build_queries("600519.SH", "600519", "600519.SH")
        self.assertEqual(queries, ["600519.SH", "600519"])

    def test_empty_input(self) -> None:
        self.assertEqual(build_queries(""), [])


class QueryKnowledgeReferenceTests(unittest.TestCase):
    def test_unavailable_base_url_degrades(self) -> None:
        ref = query_knowledge_reference("518880.SH", base_url="http://127.0.0.1:9999")
        self.assertFalse(ref["available"])
        self.assertEqual(ref["hit_count"], 0)
        self.assertEqual(ref["cards"], [])

    def test_ready_returns_cards_sorted_by_score(self) -> None:
        fake = FakeKnowledgeClient(items=[
            _hit("K-1", "低相关", 0.3, "低"),
            _hit("K-2", "高相关", 0.9, "高"),
        ])
        with patch("qteasy_research.pretrade.knowledge_reference.KnowledgeClient", return_value=fake):
            ref = query_knowledge_reference("518880.SH", "黄金ETF")
        self.assertTrue(ref["available"])
        self.assertEqual(ref["hit_count"], 2)
        self.assertEqual(ref["cards"][0]["card_id"], "K-2")

    def test_search_empty_returns_available_no_cards(self) -> None:
        fake = FakeKnowledgeClient(items=[])
        with patch("qteasy_research.pretrade.knowledge_reference.KnowledgeClient", return_value=fake):
            ref = query_knowledge_reference("518880.SH")
        self.assertTrue(ref["available"])
        self.assertEqual(ref["hit_count"], 0)
        self.assertIn("未检索到", ref["reason"])

    def test_search_exception_degrades(self) -> None:
        fake = FakeKnowledgeClient(items=[], raise_on_search=True)
        with patch("qteasy_research.pretrade.knowledge_reference.KnowledgeClient", return_value=fake):
            ref = query_knowledge_reference("518880.SH")
        self.assertFalse(ref["available"])
        self.assertIn("失败", ref["reason"])

    def test_not_ready_degrades(self) -> None:
        fake = FakeKnowledgeClient(ready=False)
        with patch("qteasy_research.pretrade.knowledge_reference.KnowledgeClient", return_value=fake):
            ref = query_knowledge_reference("518880.SH")
        self.assertFalse(ref["available"])
        self.assertIn("未运行", ref["reason"])

    def test_dedupe_same_card_across_queries(self) -> None:
        fake = FakeKnowledgeClient(items=[_hit("K-1", "同卡", 0.6)])
        with patch("qteasy_research.pretrade.knowledge_reference.KnowledgeClient", return_value=fake):
            ref = query_knowledge_reference("518880.SH", "黄金ETF")
        self.assertEqual(ref["hit_count"], 1)


class ResearchIntegrationTests(unittest.TestCase):
    """K 参考接入 run_instrument_research 的降级与呈现验证（离线，不触网）。"""

    def _fake_providers(self):
        dates = pd.date_range("2025-01-01", periods=120, freq="B")
        values = 100 * np.cumprod(np.full(120, 1.001))

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
                return AssetIdentity(code="518880.SH", asset_type="ETF", exchange="SH",
                                     benchmark="000300.SH", name="黄金ETF", resolved=True)

            def get_price_history(self, identity):
                return ProviderData(data=self.frame.copy(), source=self.name,
                                    as_of=self.frame["trade_date"].max().strftime("%Y-%m-%d"))

            def get_metadata(self, identity):
                return ProviderData(data=pd.DataFrame([{"ts_code": identity.code, "name": "黄金ETF"}]),
                                    source=self.name)

            def get_benchmark_history(self, code):
                return ProviderData(data=self.frame[["trade_date", "close"]].copy(),
                                    source=self.name,
                                    as_of=self.frame["trade_date"].max().strftime("%Y-%m-%d"))

        return FakeLocalCsvProvider

    def _run(self, directory: str, **kwargs):
        fake_provider = self._fake_providers()
        with patch("qteasy_research.pretrade.orchestrator.LocalCsvProvider", fake_provider), \
                patch("qteasy_research.pretrade.orchestrator.DProvider") as mock_d:
            mock_d.return_value.get_price_history.return_value = ProviderData()
            mock_d.return_value.get_benchmark_history.return_value = ProviderData()
            mock_d.return_value.get_metadata.return_value = ProviderData()
            return run_instrument_research(
                "518880.SH",
                network_research=False,
                llm_provider=None,
                force_refresh=True,
                output_dir=directory,
                **kwargs,
            )

    def test_research_includes_knowledge_reference_cards(self) -> None:
        fake_ref = {
            "available": True, "reason": "", "queries": ["518880.SH", "黄金ETF"],
            "hit_count": 2,
            "cards": [
                {"card_id": "KBE-test-001", "title": "黄金ETF投资研究", "category": "基金/策略",
                 "summary": "黄金配置价值与择时要点", "tags": ["黄金"], "source_type": "pdf", "score": 0.9},
                {"card_id": "KBE-test-002", "title": "黄金择时再思考", "category": "金融/工程",
                 "summary": "系统化定量视角", "tags": ["黄金"], "source_type": "pdf", "score": 0.8},
            ],
        }
        with patch("qteasy_research.pretrade.orchestrator.query_knowledge_reference",
                   return_value=fake_ref) as mock_query:
            with tempfile.TemporaryDirectory() as directory:
                result = self._run(directory)
                mock_query.assert_called_once()
        self.assertEqual(result.knowledge_reference["hit_count"], 2)
        self.assertIn("KBE-test-001", result.report)
        self.assertIn("K 知识参考（研究起点）", result.report)
        self.assertNotIn("KBE-test-001", result.asset_identity.code)

    def test_research_degrades_when_k_unavailable(self) -> None:
        fake_ref = {
            "available": False, "reason": "K 知识库服务器未运行（http://127.0.0.1:8000）",
            "queries": ["518880.SH", "黄金ETF"], "cards": [], "hit_count": 0,
        }
        with patch("qteasy_research.pretrade.orchestrator.query_knowledge_reference",
                   return_value=fake_ref):
            with tempfile.TemporaryDirectory() as directory:
                result = self._run(directory)
        self.assertIn(result.run_status, {"PARTIAL", "COMPLETED"})
        self.assertFalse(result.knowledge_reference["available"])
        self.assertIn("K 知识参考", result.report)
        self.assertIn("未成功", result.report)
        # 临时目录在断言时已清理，这里只验证 artifact 路径已记录、报告内容已渲染
        self.assertTrue(result.artifacts.get("report"))
        self.assertIn("K 知识库服务器未运行", result.report)

    def test_research_knowledge_reference_disabled_skips_query(self) -> None:
        with patch("qteasy_research.pretrade.orchestrator.query_knowledge_reference") as mock_query:
            with tempfile.TemporaryDirectory() as directory:
                result = self._run(directory, knowledge_reference=False)
                mock_query.assert_not_called()
        self.assertEqual(result.knowledge_reference["hit_count"], 0)
        self.assertFalse(result.knowledge_reference["available"])
        self.assertIn("未启用 K 知识参考", result.report)


if __name__ == "__main__":
    unittest.main()
