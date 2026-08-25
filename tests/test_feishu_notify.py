"""飞书只读推送模块测试：mock requests.post，不依赖外网。

运行（从 qteasy_lab 目录）：python -B -m unittest tests/test_feishu_notify.py -v
"""

import os
import unittest
from unittest import mock

from qteasy_research.common.feishu_notify import MAX_LEN, feishu_send


class FeishuNotifyTest(unittest.TestCase):
    """有 URL 场景：payload 结构 / timeout / 截断 / 异常吞掉。"""

    def setUp(self):
        self._saved = os.environ.get("FEISHU_WEBHOOK_URL")
        os.environ["FEISHU_WEBHOOK_URL"] = "http://fake/webhook"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("FEISHU_WEBHOOK_URL", None)
        else:
            os.environ["FEISHU_WEBHOOK_URL"] = self._saved

    def test_payload_and_success(self):
        with mock.patch("qteasy_research.common.feishu_notify.requests") as fake_req:
            fake_req.post.return_value.ok = True
            fake_req.post.return_value.json.return_value = {"code": 0}
            self.assertTrue(feishu_send("标题", "正文"))
            payload = fake_req.post.call_args.kwargs["json"]
            self.assertEqual(payload["msg_type"], "text")
            self.assertEqual(payload["content"]["text"], "标题\n正文")

    def test_timeout_passed(self):
        with mock.patch("qteasy_research.common.feishu_notify.requests") as fake_req:
            fake_req.post.return_value.ok = True
            fake_req.post.return_value.json.return_value = {"code": 0}
            feishu_send("标题", "正文")
            self.assertEqual(fake_req.post.call_args.kwargs["timeout"], 15)

    def test_long_content_truncated(self):
        with mock.patch("qteasy_research.common.feishu_notify.requests") as fake_req:
            fake_req.post.return_value.ok = True
            fake_req.post.return_value.json.return_value = {"code": 0}
            feishu_send("标题", "x" * 5000)
            content = fake_req.post.call_args.kwargs["json"]["content"]["text"]
            self.assertLessEqual(len(content), MAX_LEN + 10)
            self.assertTrue(content.endswith("…（详见系统）"))

    def test_exception_swallowed(self):
        with mock.patch("qteasy_research.common.feishu_notify.requests") as fake_req:
            fake_req.post.side_effect = RuntimeError("network down")
            self.assertFalse(feishu_send("标题", "正文"))


class FeishuNotifyNoUrlTest(unittest.TestCase):
    """无 URL 场景：静默跳过（回滚开关）。"""

    def test_no_url_skips(self):
        os.environ.pop("FEISHU_WEBHOOK_URL", None)
        self.assertFalse(feishu_send("标题", "正文"))


if __name__ == "__main__":
    unittest.main()
