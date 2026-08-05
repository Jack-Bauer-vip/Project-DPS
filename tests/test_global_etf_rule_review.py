"""宏观规则审核状态机与历史版本链的离线测试。

覆盖：历史快照（create/update）、确认/驳回/撤销/重新提交、非法转换拒绝、
重复 APPROVED 冲突、状态枚举校验、历史查询过滤与引擎隔离（REJECTED 不被读取）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qteasy_research.pretrade.storage import ResearchStore


def _rule(**overrides) -> dict:
    payload = {
        "asset_code": "SPY",
        "macro_state": "real_yield_up",
        "modifier": 0.92,
        "sample_count": 85,
        "confidence": "medium",
        "status": "DRAFT",
        "effective_date": "2026-08-03",
    }
    payload.update(overrides)
    return payload


class MacroRuleReviewStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = ResearchStore(self.root / "store")

    def tearDown(self) -> None:
        self.temp.cleanup()

    # ---- 历史快照：create / update ----

    def test_upsert_create_writes_history(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["action"], "create")
        self.assertEqual(history[0]["status"], "DRAFT")
        self.assertEqual(history[0]["modifier"], 0.92)

    def test_upsert_update_writes_history_snapshot(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule(modifier=0.92))
        # 同 (asset,state,effective_date) 更新 → 应触发 UPDATE + update 历史
        self.store.upsert_global_etf_macro_rule(_rule(modifier=0.85))
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual(len(history), 2)
        self.assertEqual([h["action"] for h in history], ["create", "update"])
        # update 历史行是变更后的新值
        self.assertEqual(history[1]["modifier"], 0.85)

    def test_upsert_rejects_unknown_status(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self.store.upsert_global_etf_macro_rule(_rule(status="FOO"))
        self.assertIn("status", str(ctx.exception))

    # ---- 确认 ----

    def test_approve_transitions_and_records_approved_at(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        approved = self.store.approve_global_etf_macro_rule(saved["rule_id"], approved_by="bern")
        self.assertEqual(approved["status"], "APPROVED")
        self.assertEqual(approved["approved_by"], "bern")
        self.assertIsNotNone(approved["approved_at"])
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual(history[-1]["action"], "approve")
        self.assertEqual(history[-1]["status"], "APPROVED")
        # 引擎只读 APPROVED
        rules = self.store.get_global_etf_macro_rules(
            asset_code="SPY", macro_states=["real_yield_up"], target_date="2026-08-03"
        )
        self.assertEqual(len(rules), 1)

    def test_approve_wrong_state_rejected(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.approve_global_etf_macro_rule(saved["rule_id"])
        with self.assertRaises(ValueError) as ctx:
            self.store.approve_global_etf_macro_rule(saved["rule_id"])
        self.assertIn("不允许", str(ctx.exception))

    # ---- 驳回 ----

    def test_reject_transitions_and_records_reason(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        rejected = self.store.reject_global_etf_macro_rule(
            saved["rule_id"], rejected_by="bern", reason="样本外失效"
        )
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["reason"], "样本外失效")
        self.assertIsNotNone(rejected["rejected_at"])
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual(history[-1]["action"], "reject")
        self.assertEqual(history[-1]["reason"], "样本外失效")

    def test_reject_approved_rule_forbidden(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.approve_global_etf_macro_rule(saved["rule_id"])
        with self.assertRaises(ValueError):
            self.store.reject_global_etf_macro_rule(saved["rule_id"])

    # ---- 撤销 ----

    def test_revoke_approved_rule(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.approve_global_etf_macro_rule(saved["rule_id"])
        revoked = self.store.revoke_global_etf_macro_rule(
            saved["rule_id"], rejected_by="bern", reason="新数据证伪"
        )
        self.assertEqual(revoked["status"], "REJECTED")
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual([h["action"] for h in history], ["create", "approve", "revoke"])
        # 撤销后引擎不再读到 APPROVED
        rules = self.store.get_global_etf_macro_rules(
            asset_code="SPY", macro_states=["real_yield_up"], target_date="2026-08-03"
        )
        self.assertEqual(rules, [])

    def test_revoke_draft_forbidden(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        with self.assertRaises(ValueError):
            self.store.revoke_global_etf_macro_rule(saved["rule_id"])

    # ---- 重新提交 ----

    def test_reset_rejected_to_draft(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.reject_global_etf_macro_rule(saved["rule_id"], reason="暂不启用")
        reset = self.store.reset_global_etf_macro_rule_to_draft(saved["rule_id"], note="补充样本后重审")
        self.assertEqual(reset["status"], "DRAFT")
        history = self.store.list_global_etf_macro_rule_history(saved["rule_id"])
        self.assertEqual(history[-1]["action"], "reset")
        # 重新提交后可再次确认
        approved = self.store.approve_global_etf_macro_rule(saved["rule_id"])
        self.assertEqual(approved["status"], "APPROVED")

    def test_reset_approved_forbidden(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.approve_global_etf_macro_rule(saved["rule_id"])
        with self.assertRaises(ValueError):
            self.store.reset_global_etf_macro_rule_to_draft(saved["rule_id"])

    # ---- 重复 APPROVED 冲突 ----

    def test_duplicate_approved_conflict_raises(self) -> None:
        self.store.upsert_global_etf_macro_rule(_rule(effective_date="2026-08-01"))
        self.store.upsert_global_etf_macro_rule(_rule(effective_date="2026-08-02"))
        approved_ones = self.store.list_global_etf_macro_rules()
        self.store.approve_global_etf_macro_rule(approved_ones[0]["rule_id"])
        # 第二条尝试 APPROVED（不同 effective_date 新增）→ 冲突
        with self.assertRaises(ValueError) as ctx:
            self.store.upsert_global_etf_macro_rule(
                _rule(effective_date="2026-08-02", status="APPROVED")
            )
        self.assertIn("已有 APPROVED 规则", str(ctx.exception))

    # ---- 历史查询过滤 ----

    def test_history_filter_by_asset_and_state(self) -> None:
        self.store.upsert_global_etf_macro_rule(_rule())
        self.store.upsert_global_etf_macro_rule(_rule(asset_code="TLT", macro_state="rate_up"))
        by_asset = self.store.list_global_etf_macro_rule_history(asset_code="TLT")
        self.assertEqual(len(by_asset), 1)
        self.assertEqual(by_asset[0]["asset_code"], "TLT")
        by_state = self.store.list_global_etf_macro_rule_history(macro_state="rate_up")
        self.assertEqual(len(by_state), 1)
        self.assertEqual(by_state[0]["macro_state"], "rate_up")

    def test_history_missing_rule_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.store.approve_global_etf_macro_rule(999999)

    # ---- 引擎隔离 ----

    def test_rejected_rule_never_used_by_engine(self) -> None:
        saved = self.store.upsert_global_etf_macro_rule(_rule())
        self.store.reject_global_etf_macro_rule(saved["rule_id"])
        rules = self.store.get_global_etf_macro_rules(
            asset_code="SPY", macro_states=["real_yield_up"], target_date="2026-08-03"
        )
        self.assertEqual(rules, [])


if __name__ == "__main__":
    unittest.main()
