"""grid 策略 pretrade 研究项目初始化脚本（P3）离线测试。

覆盖：grid_config.csv 读取 / 契约回退 / 双缺失报错、plan 预览零写入、
apply 建项目 + 组合标的上下文 + 资产引用 + 初始笔记、幂等复用、既有资产
项目复用、全 ASCII 标识。
纯临时目录 + fixture，不碰真实 research_store / 共享目录。
运行：``cd research_engines/qteasy_lab && .venv/Scripts/python.exe -B -m unittest discover -s "D:/Project DPS/tests" -q``
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qteasy_research.pretrade import (
    create_research_project,
    list_asset_references,
    list_portfolio_asset_contexts,
    list_project_notes,
    list_research_projects,
)
from scripts.init_grid_research_projects import (
    find_asset_project,
    find_strategy_project,
    load_grid_instances,
    plan_init,
    run_init,
)

GRID_CSV_CONTENT = """strategy_id,asset_id,enabled,anchor_price,regular_spread,edge_spread,regular_levels_per_side,edge_levels_per_side,rating,source,updated_at,notes
grid_lh,159985.SZ,1,,0.0589,0.1178,3,1,recommended,manual,2026-08-13,
grid_lh,513520.SH,1,,0.1304,0.2608,3,1,recommended,manual,2026-08-13,
grid_lh,513650.SH,1,,0.0509,0.1018,3,1,recommended,manual,2026-08-13,
grid_lh,515180.SH,1,,0.0473,0.0946,3,1,recommended,manual,2026-08-13,
grid_scz,513520.SH,1,,0.1304,0.2608,3,1,recommended,manual,2026-08-13,
grid_scz,513650.SH,1,,0.0509,0.1018,3,1,recommended,manual,2026-08-13,
grid_scz,515180.SH,1,,0.0473,0.0946,3,1,recommended,manual,2026-08-13,
grid_scz,518880.SH,1,,0.0697,0.1394,3,1,recommended,manual,2026-08-13,
"""

CONTRACT_CONTENT = {
    "schema_version": "1.0",
    "contract_type": "strategy_rules",
    "generated_at": "2026-08-06T00:00:00",
    "generated_by": "systemA",
    "strategies": [
        {
            "strategy_id": "grid_lh",
            "decision_rule": "grid",
            "enabled": True,
            "use_target_ratio": False,
            "rebalance_frequency": "daily",
            "rebalance_threshold_abs": 0.03,
            "asset_rebalance_threshold_abs": 0.02,
            "signal_filters": None,
            "preferences": {"macro_fit": -0.2},
            "assets": [
                {"asset_id": "159985.SZ", "role": "grid", "enabled": True,
                 "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.2,
                 "target_weight_configured": False},
                {"asset_id": "513520.SH", "role": "grid", "enabled": True,
                 "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.2,
                 "target_weight_configured": False},
            ],
        },
        {
            "strategy_id": "grid_scz",
            "decision_rule": "grid",
            "enabled": True,
            "use_target_ratio": False,
            "rebalance_frequency": "daily",
            "rebalance_threshold_abs": 0.03,
            "asset_rebalance_threshold_abs": 0.02,
            "signal_filters": None,
            "preferences": {"macro_fit": -0.2},
            "assets": [
                {"asset_id": "518880.SH", "role": "grid", "enabled": True,
                 "min_weight": 0.0, "target_weight": 0.0, "max_weight": 0.2,
                 "target_weight_configured": False},
            ],
        },
    ],
    "shared_config": {"adj_type": "none", "grid": {}},
}


class LoadInstancesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.grid_config = self.root / "grid_config.csv"
        self.grid_config.write_text(GRID_CSV_CONTENT, encoding="utf-8")
        self.contract = self.root / "strategy_contract.json"
        self.contract.write_text(json.dumps(CONTRACT_CONTENT, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_load_from_grid_config(self) -> None:
        instances = load_grid_instances(self.grid_config, self.root / "absent.json")
        self.assertEqual(set(instances), {"grid_lh", "grid_scz"})
        self.assertEqual(instances["grid_lh"]["source"], "grid_config.csv")
        self.assertEqual(set(instances["grid_lh"]["assets"]), {"159985.SZ", "513520.SH", "513650.SH", "515180.SH"})
        self.assertEqual(set(instances["grid_scz"]["assets"]), {"513520.SH", "513650.SH", "515180.SH", "518880.SH"})
        # 原始行保留（初始记录用）
        self.assertEqual(len(instances["grid_lh"]["rows"]), 4)

    def test_contract_fallback_when_config_missing(self) -> None:
        instances = load_grid_instances(self.root / "absent.csv", self.contract)
        self.assertEqual(instances["grid_lh"]["source"], "strategy_contract.json")
        self.assertEqual(set(instances["grid_lh"]["assets"]), {"159985.SZ", "513520.SH"})
        self.assertEqual(instances["grid_scz"]["assets"], ["518880.SH"])

    def test_missing_both_raises(self) -> None:
        with self.assertRaises(ValueError):
            load_grid_instances(self.root / "absent.csv", self.root / "absent.json")

    def test_disabled_rows_ignored(self) -> None:
        frame = pd.read_csv(self.grid_config, dtype=str)
        disabled = frame.copy()
        disabled.loc[disabled["asset_id"] == "159985.SZ", "enabled"] = "0"
        (self.root / "disabled.csv").write_text(disabled.to_csv(index=False), encoding="utf-8")
        instances = load_grid_instances(self.root / "disabled.csv", self.root / "absent.json")
        self.assertNotIn("159985.SZ", instances["grid_lh"]["assets"])


class RunInitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = self.root / "store"
        self.store.mkdir()
        self.grid_config = self.root / "grid_config.csv"
        self.grid_config.write_text(GRID_CSV_CONTENT, encoding="utf-8")
        self.instances = load_grid_instances(self.grid_config, self.root / "absent.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _count_projects(self, project_type: str | None = None) -> int:
        return len([
            p for p in list_research_projects(output_dir=self.store)
            if project_type is None or p.project_type == project_type
        ])

    def test_plan_preview_writes_nothing(self) -> None:
        plan = plan_init(self.instances, self.store)
        self.assertEqual(plan["strategy"]["grid_lh"]["created"], True)
        self.assertEqual(plan["asset"]["518880.SH"]["created"], True)
        # 计划应完整列出将补的笔记与引用（即使策略项目尚未创建）。
        self.assertEqual(len(plan["notes_to_add"]), 7, "2 策略 + 5 资产各一条初始笔记")
        self.assertEqual(len(plan["refs_to_add"]), 8, "grid_lh 4 + grid_scz 4 条资产引用")
        self.assertEqual(self._count_projects(), 0, "plan 不得写任何数据")

    def test_run_init_dry_run_writes_nothing(self) -> None:
        plan = run_init(self.instances, self.store, apply=False)
        self.assertIn("strategy", plan)
        self.assertEqual(self._count_projects(), 0)

    def test_apply_creates_projects_contexts_refs_notes(self) -> None:
        plan = run_init(self.instances, self.store, apply=True)
        # 策略项目
        lh = find_strategy_project("grid_lh", self.store)
        scz = find_strategy_project("grid_scz", self.store)
        self.assertIsNotNone(lh)
        self.assertIsNotNone(scz)
        self.assertEqual(lh.project_type, "STRATEGY_PORTFOLIO")
        self.assertEqual(lh.strategy_name, "grid_lh")
        self.assertTrue(lh.name.isascii())
        self.assertTrue(scz.name.isascii())
        # 组合标的上下文
        lh_codes = {c.code for c in list_portfolio_asset_contexts(project_id=lh.project_id, output_dir=self.store)}
        self.assertEqual(lh_codes, {"159985.SZ", "513520.SH", "513650.SH", "515180.SH"})
        scz_codes = {c.code for c in list_portfolio_asset_contexts(project_id=scz.project_id, output_dir=self.store)}
        self.assertEqual(scz_codes, {"513520.SH", "513650.SH", "515180.SH", "518880.SH"})
        # 资产项目：5 个唯一标的全部就绪
        for code in ("159985.SZ", "513520.SH", "513650.SH", "515180.SH", "518880.SH"):
            project = find_asset_project(code, self.store)
            self.assertIsNotNone(project, code)
            self.assertEqual(project.project_type, "ASSET_PROFILE")
            self.assertTrue(project.name.isascii())
        # 引用：grid_scz → 518880.SH 资产项目
        scz_refs = list_asset_references(scz.project_id, output_dir=self.store)
        self.assertTrue(any(ref.asset_code == "518880.SH" for ref in scz_refs))
        self.assertEqual(len(scz_refs), 4)
        # 初始笔记
        for project in (lh, scz):
            titles = [n.title for n in list_project_notes(project.project_id, output_dir=self.store)]
            self.assertIn("grid_pretrade_init", titles)
        # 计划汇总
        self.assertIn("grid_lh", plan["strategy"])
        self.assertEqual(self._count_projects("STRATEGY_PORTFOLIO"), 2)
        self.assertEqual(self._count_projects("ASSET_PROFILE"), 5)

    def test_apply_is_idempotent(self) -> None:
        run_init(self.instances, self.store, apply=True)
        plan2 = run_init(self.instances, self.store, apply=True)
        self.assertEqual(self._count_projects("STRATEGY_PORTFOLIO"), 2)
        self.assertEqual(self._count_projects("ASSET_PROFILE"), 5)
        scz = find_strategy_project("grid_scz", self.store)
        self.assertEqual(len(list_asset_references(scz.project_id, output_dir=self.store)), 4)
        notes = [n for n in list_project_notes(scz.project_id, output_dir=self.store) if n.title == "grid_pretrade_init"]
        self.assertEqual(len(notes), 1, "重复运行不得追加重复笔记")
        self.assertEqual(plan2["strategy"]["grid_lh"]["created"], False, "重复运行应复用")
        self.assertEqual(plan2["refs_to_add"], [], "已挂引用不得重复计划")
        self.assertEqual(plan2["notes_to_add"], [], "已有笔记不得重复计划")

    def test_existing_asset_project_reused_not_overwritten(self) -> None:
        existing = create_research_project("黄金", "518880.SH", output_dir=self.store)
        run_init(self.instances, self.store, apply=True)
        reused = find_asset_project("518880.SH", self.store)
        self.assertIsNotNone(reused)
        self.assertEqual(reused.project_id, existing.project_id, "既有资产项目必须复用")
        self.assertEqual(reused.name, "黄金", "不得覆盖既有项目名称")
        self.assertEqual(self._count_projects("ASSET_PROFILE"), 5)


if __name__ == "__main__":
    unittest.main()
