"""일일 리포트 저장 (docs/reports.md). 텔레그램으로 보낸 본문과 그 재료를 daily_reports·report_items 에 남긴다.

왜 있나: CLAUDE.md 는 "배치 결과는 daily_reports 테이블에 저장. 웹앱은 오늘 리포트와 이전
리포트 이력을 보여준다" 고 하는데 2026-09-17 까지 표가 없었다. 텔레그램 메시지는 폰에서
스크롤해 올라가야 찾고, 지난 추천을 나중에 되짚을 길이 없었다.

원칙
- 본문(summary_text)은 보낸 글 **그대로**. 화면이 다시 그리지 않는다 — 다시 그리면 둘이 어긋난다
- 재료(report_items)는 구조로. 화면이 표로 보여 주거나 종목 상세로 연결할 때 쓴다
- 같은 거래일을 다시 돌리면 지우고 다시 넣는다. 실패한 실행은 넣지 않는다
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any

from batch.core import db
from batch.core.turso import TursoClient
from batch.notify import report_sections as rs
from batch.services import report_picks as rp

log = logging.getLogger(__name__)

SECTION_RECOMMEND = "recommend"  # 1부 개별 종목 (기간마다 한 항목)
SECTION_BUY = "buy_signal"  # 2부 배분
SECTION_SELL_FLAG = "sell_flag"
SECTION_NOTICE = "notice"  # 2부 제외 사유 · 경고 · 시장 국면


def items_from(
    composed: rp.Composed,
    sell_flags: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    regime_line: str | None = None,
) -> list[dict[str, Any]]:
    """본문을 만든 재료를 report_items 행(dict)으로. 순서가 곧 rank 다."""
    items: list[dict[str, Any]] = []
    stock_of = {row.ticker: row.stock_id for row in composed.chosen}

    def add(section: str, payload: dict[str, Any], stock_id: int | None = None, rationale: str | None = None) -> None:
        items.append({
            "section": section,
            "stock_id": stock_id,
            "rank": sum(1 for i in items if i["section"] == section) + 1,
            "payload": payload,
            "rationale_text": rationale,
        })

    for pick in composed.picks:
        add(SECTION_RECOMMEND, asdict(pick), stock_of.get(pick.ticker), pick.rationale)

    if composed.portfolio is not None:
        for alloc in composed.portfolio.allocations:
            add(SECTION_BUY, asdict(alloc), stock_of.get(alloc.ticker))
        for ex in composed.portfolio.excluded:
            add(SECTION_NOTICE, {"kind": "excluded", **asdict(ex)}, stock_of.get(ex.ticker))

    for flag in sell_flags or []:
        add(SECTION_SELL_FLAG, dict(flag), _int_or_none(flag.get("stock_id")))

    if regime_line:
        add(SECTION_NOTICE, {"kind": "regime", "text": regime_line})
    for warning in warnings or []:
        add(SECTION_NOTICE, {"kind": "warning", "text": warning})
    return items


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def store_report(
    client: TursoClient,
    *,
    market: str,
    trade_date: str,
    status: str,
    message: str,
    warnings: list[str],
    items: list[dict[str, Any]],
    batch_run_id: int | None,
    generated_at: str | None = None,
) -> int:
    """같은 (market, trade_date) 를 지우고 다시 넣는다. 리포트 id 를 돌려준다."""
    now = generated_at or db.now_iso()
    # **지우기·리포트·항목을 한 묶음으로** 보낸다 (docs/infra.md 25.341). 예전에는 항목을 두 번째 묶음으로
    # 따로 넣어, 그 사이에 끊기면 **항목 없는 리포트**가 `success` 그대로 최신으로 남았다. D1 의 batch 는
    # 한 트랜잭션이라 이제 `--force` 재실행이 중간에 실패해도 그날의 옛 리포트가 지워진 채로 남지 않는다.
    # 항목의 리포트 id 는 같은 묶음 안에서 서브쿼리로 잡는다(바로 위에서 지웠으니 한 행뿐이다)
    statements: list[tuple[str, list[Any]]] = [
        (
            "DELETE FROM report_items WHERE report_id IN"
            " (SELECT id FROM daily_reports WHERE market = ? AND trade_date = ?)",
            [market, trade_date],
        ),
        ("DELETE FROM daily_reports WHERE market = ? AND trade_date = ?", [market, trade_date]),
        (
            "INSERT INTO daily_reports"
            " (market, trade_date, status, generated_at, summary_text, warnings_json, batch_run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [market, trade_date, status, now, message, json.dumps(warnings, ensure_ascii=False), batch_run_id],
        ),
    ]
    statements += [
        (
            "INSERT INTO report_items (report_id, section, stock_id, rank, payload_json, rationale_text)"
            " VALUES ((SELECT id FROM daily_reports WHERE market = ? AND trade_date = ?), ?, ?, ?, ?, ?)",
            [
                market, trade_date, item["section"], item.get("stock_id"), item["rank"],
                json.dumps(item["payload"], ensure_ascii=False, default=str), item.get("rationale_text"),
            ],
        )
        for item in items
    ]
    results = client.batch(statements)
    report_id = results[2].last_insert_rowid
    if report_id is None:
        rs_id = client.execute(
            "SELECT id FROM daily_reports WHERE market = ? AND trade_date = ?", [market, trade_date]
        )
        report_id = int(rs_id.scalar())
    return int(report_id)


def sent_exists(client: TursoClient, market: str, trade_date: str) -> bool:
    """그 시장·거래일에 **이미 보낸** 리포트가 있는가 (docs/infra.md 25.567). 못 읽으면 False(예전처럼 먼저 저장)."""
    try:
        rs = client.execute(
            "SELECT 1 FROM daily_reports WHERE market = ? AND trade_date = ? AND sent_at IS NOT NULL LIMIT 1",
            [market, trade_date],
        )
    except Exception as exc:  # noqa: BLE001 — 못 읽으면 예전 동작(먼저 저장)으로 간다
        log.warning("보낸 리포트가 있는지 읽지 못했습니다: %s", exc)
        return False
    return bool(rs.rows)


def mark_sent(client: TursoClient, report_id: int, message_ids: Any, sent_at: str | None = None) -> None:
    """발송이 끝난 뒤 시각과 메시지 id 를 적는다. 발송 전에는 NULL 이라 '만들었지만 못 보냈다' 가 보인다."""
    if isinstance(message_ids, (list, tuple)):
        joined = ",".join(str(m) for m in message_ids)
    else:
        joined = None if message_ids is None else str(message_ids)
    client.execute(
        "UPDATE daily_reports SET sent_at = ?, telegram_message_id = ? WHERE id = ?",
        [sent_at or db.now_iso(), joined, report_id],
    )


def mark_partial_send(client: TursoClient, report_id: int, warnings: list[str]) -> None:
    """텔레그램이 **일부만** 나간 리포트 — 상태를 partial 로, 경고를 다시 적는다 (docs/infra.md 25.419).

    리포트는 보내기 **전에** 저장한다(`store_report`). 25.414 는 일부 발송 경고를 그 뒤에 덧붙여, 웹 리포트는
    `success` 에 발송 시각까지 찍혀 **잘린 줄을 알 수 없었다.** 경고 목록을 통째로 바꿔 적는다.
    """
    client.execute(
        "UPDATE daily_reports SET status = 'partial', warnings_json = ? WHERE id = ?",
        [json.dumps(warnings, ensure_ascii=False), report_id],
    )


__all__ = ["items_from", "store_report", "mark_sent", "mark_partial_send", "rs"]
