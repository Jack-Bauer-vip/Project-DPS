"""K 知识库（Knowledge Weaver）研究起点参考查询。

在标的研究开始时，先向本地知识库查询与标的/策略相关的知识卡，
把已有结论作为「研究起点参考」呈现，避免重复劳动。

降级铁律（硬性设计约束）：
- K 不可用 / client 异常 / 查询空结果 → 返回结构化降级结果，**绝不抛异常阻断研究**；
- 只用 knowledge_client 只读方法（``is_ready`` / ``search``），不写 K、不发写端点；
- 知识卡引用只作为研究参考展示数据，不写入策略名 / 契约 / 表名。
"""

from __future__ import annotations

from typing import Any

import requests

try:
    from knowledge_client import KnowledgeClient
except Exception:  # pragma: no cover - 包缺失时降级，不阻断研究主流程
    KnowledgeClient = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_MAX_CARDS = 5
_MAX_QUERIES = 4
_QUERY_TIMEOUT = 8


def build_queries(
    code: str,
    name: str | None = None,
    strategy: str | None = None,
    macro_theme: str | None = None,
) -> list[str]:
    """构造查询词：标的代码、中文名、策略名、宏观主题。

    去重保序、过滤空串；带交易所后缀的代码（如 518880.SH）同时补一个
    纯代码（518880）以提高召回。
    """
    candidates: list[str] = []
    if code:
        normalized = str(code).strip().upper()
        candidates.append(normalized)
        if "." in normalized:
            candidates.append(normalized.split(".", 1)[0])
    for text in (name, strategy, macro_theme):
        if text:
            candidates.append(str(text).strip())
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _unavailable(reason: str, queries: list[str]) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "queries": queries,
        "cards": [],
        "hit_count": 0,
    }


def _card_brief(item: dict[str, Any]) -> dict[str, Any]:
    """从 search 结果项提取精简卡片引用（标题 + 摘要要点片段 + 卡 ID）。"""
    summary = item.get("brief_summary") or item.get("title") or ""
    summary = str(summary).strip()
    if len(summary) > 120:
        summary = summary[:120] + "…"
    return {
        "card_id": item.get("id") or "",
        "title": item.get("title") or "",
        "summary": summary,
        "category": item.get("category") or "",
        "tags": list(item.get("tags") or [])[:6],
        "source_type": item.get("source_type") or "",
        "score": item.get("score"),
    }


def query_knowledge_reference(
    code: str,
    name: str | None = None,
    strategy: str | None = None,
    macro_theme: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    max_cards: int = _MAX_CARDS,
    max_queries: int = _MAX_QUERIES,
) -> dict[str, Any]:
    """查询 K 知识库，返回研究起点参考。永不抛异常。

    返回结构：:

        {
            "available": bool,   # K 服务是否可用
            "reason": str,       # 不可用 / 空结果原因（human-readable）
            "queries": [...],    # 实际使用的查询词
            "cards": [...],      # 命中知识卡 [{card_id,title,summary,category,tags,source_type,score}]
            "hit_count": int,    # 去重后卡片数
        }
    """
    queries = build_queries(code, name, strategy, macro_theme)
    if KnowledgeClient is None:
        return _unavailable("knowledge_client 未安装或导入失败，已跳过知识参考", queries)

    try:
        kb = KnowledgeClient(base_url=base_url, timeout=_QUERY_TIMEOUT)
        if not kb.is_ready():
            return _unavailable(
                f"K 知识库服务器未运行（{base_url}），已跳过知识参考", queries
            )
        # 聚合所有查询词的命中，按相关度 score 降序取 top-N（同一卡只保留最高分）。
        agg: dict[str, dict[str, Any]] = {}
        searched_any = False
        for q in queries[:max_queries]:
            try:
                resp = kb.search(q, mode="hybrid", limit=max(8, max_cards * 3))
            except Exception:  # noqa: BLE001 - 单个查询词失败不致命，继续下一个
                continue
            searched_any = True
            for item in resp.get("items") or []:
                card_id = item.get("id")
                if not card_id:
                    continue
                score = item.get("score") or 0
                existing = agg.get(card_id)
                if existing is None or (existing.get("score") or 0) < score:
                    agg[card_id] = _card_brief(item)
        if agg:
            cards = sorted(agg.values(), key=lambda c: c.get("score") or 0, reverse=True)[:max_cards]
            return {
                "available": True,
                "reason": "",
                "queries": queries,
                "cards": cards,
                "hit_count": len(cards),
            }
        if not searched_any:
            return _unavailable("K 知识库搜索请求全部失败", queries)
        return {
            "available": True,
            "reason": "K 知识库未检索到与查询词相关的知识卡",
            "queries": queries,
            "cards": [],
            "hit_count": 0,
        }
    except requests.RequestException as exc:
        return _unavailable(f"K 知识库连接失败：{exc}", queries)
    except Exception as exc:  # noqa: BLE001 - 降级铁律：任何异常都不阻断研究
        return _unavailable(f"K 知识库查询异常：{type(exc).__name__}: {exc}", queries)
