"""飞书只读推送（B 侧统一工具）

统一环境变量 ``FEISHU_WEBHOOK_URL``（飞书群自定义机器人 webhook，密钥性质、绝不入 git）。
B 无 .env 机制，配置走 Windows 系统环境变量（``setx FEISHU_WEBHOOK_URL "..."``）。
未配置时静默跳过（回滚开关：清空该变量即全局禁用推送）。

优先 requests，无则 fallback urllib（零依赖）。
与协调层 ``coordination/tools/feishu_notify.py`` 同签名 ``feishu_send(title, body) -> bool``，
未来升级飞书开放平台 API / interactive 卡片只需替换本模块内部实现。
"""

from __future__ import annotations

import json
import os
import urllib.request

try:
    import requests  # type: ignore
except ImportError:  # pragma: no cover - 未装 requests 时降级 urllib
    requests = None  # type: ignore

TIMEOUT = 15
MAX_LEN = 1900  # 飞书 text 消息约 2000 字符上限，留余量


def feishu_send(title: str, body: str = "") -> bool:
    """推送一条文本消息到飞书群。未配置 URL / 发送失败返回 False，绝不抛异常。"""
    url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not url:
        print("[feishu] 未配置 FEISHU_WEBHOOK_URL，跳过推送")
        return False
    content = f"{title}\n{body}".strip()
    if len(content) > MAX_LEN:
        # 对「整个拼接后」的 content 截断（不只切 body，避免 title 极端超长时丢失标题尾部）
        content = content[:MAX_LEN] + "…（详见系统）"
    # 飞书群机器人 payload：msg_type(下划线) + content.text（不是钉钉的 msgtype+text）
    payload = {"msg_type": "text", "content": {"text": content}}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        http_ok = False
        body: dict = {}
        if requests is not None:
            resp = requests.post(url, json=payload, timeout=TIMEOUT)
            http_ok = bool(getattr(resp, "ok", False))
            try:
                body = resp.json()
            except Exception:
                body = {}
        else:
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                http_ok = resp.status == 200
                body = json.loads(resp.read().decode("utf-8"))
        # 飞书业务成功标志 = code==0（HTTP 200 但 code!=0 表示 payload 无效等，需如实报告）
        ok = http_ok and body.get("code") == 0
        print("[feishu] 推送成功" if ok else
              f"[feishu] 推送未送达: http={http_ok} code={body.get('code')} msg={body.get('msg')}")
        return ok
    except Exception as exc:  # 推送永不阻断业务
        print(f"[feishu] 推送失败: {exc}")
        return False
