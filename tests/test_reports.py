"""일일 리포트 저장 (docs/reports.md). 본문은 그대로, 재료는 구조로, 같은 날은 덮어쓴다."""

from __future__ import annotations

import json

from batch.services import report_picks as rp
from batch.services import reports
from tests.test_report_picks import SqliteClient, row


def _composed(n: int = 2) -> rp.Composed:
    return rp.compose([row(i) for i in range(1, n + 1)], total_investable=10_000_000, signals_as_of="2026-09-16")


class Test재료:
    def test_1부_2부_경고가_항목이_된다(self) -> None:
        items = reports.items_from(_composed(), sell_flags=[{"stock_id": 7, "ticker": "X", "level": "red"}],
                                   warnings=["환율 없음"], regime_line="시장 국면: KOSPI 약세")
        sections = [i["section"] for i in items]
        assert sections.count("recommend") == 2 and sections.count("buy_signal") == 2
        assert sections.count("sell_flag") == 1 and sections.count("notice") == 2
        rec = [i for i in items if i["section"] == "recommend"]
        assert rec[0]["stock_id"] == 1 and rec[0]["rank"] == 1 and rec[1]["rank"] == 2
        assert rec[0]["payload"]["ticker"] == "000001" and rec[0]["rationale_text"] == "매출 18.2%"
        flag = next(i for i in items if i["section"] == "sell_flag")
        assert flag["stock_id"] == 7
        kinds = [i["payload"]["kind"] for i in items if i["section"] == "notice"]
        assert kinds == ["regime", "warning"]

    def test_제외_사유도_남는다(self) -> None:
        composed = rp.compose([row(1, suggested_amount=None)], total_investable=10_000_000, signals_as_of="2026-09-16")
        items = reports.items_from(composed)
        ex = [i for i in items if i["section"] == "notice"]
        assert ex[0]["payload"]["kind"] == "excluded" and ex[0]["stock_id"] == 1

    def test_신호가_없으면_항목도_없다(self) -> None:
        assert reports.items_from(rp.compose([], 0, None)) == []


class Test저장:
    def _stocks(self, client: SqliteClient) -> None:
        for i in (1, 2):
            client.conn.execute(
                "INSERT INTO stocks (id, ticker, market, country, currency, status, source, fetched_at)"
                f" VALUES ({i}, '{i:06d}', 'KOSPI', 'KR', 'KRW', 'active', 't', 't')"
            )

    def test_본문과_항목을_넣고_발송_시각을_적는다(self) -> None:
        client = SqliteClient()
        self._stocks(client)
        composed = _composed()
        report_id = reports.store_report(
            client, market="KR", trade_date="2026-09-16", status="success", message=composed.text,  # type: ignore[arg-type]
            warnings=[], items=reports.items_from(composed), batch_run_id=None, generated_at="2026-09-17T08:30:00",
        )
        stored = client.conn.execute("SELECT summary_text, sent_at, status FROM daily_reports WHERE id = ?", [report_id]).fetchone()
        assert stored[0] == composed.text and stored[1] is None and stored[2] == "success"
        n = client.conn.execute("SELECT COUNT(*) FROM report_items WHERE report_id = ?", [report_id]).fetchone()[0]
        assert n == 4
        reports.mark_sent(client, report_id, [11, 12], sent_at="2026-09-17T08:31:00")  # type: ignore[arg-type]
        sent = client.conn.execute("SELECT sent_at, telegram_message_id FROM daily_reports WHERE id = ?", [report_id]).fetchone()
        assert sent == ("2026-09-17T08:31:00", "11,12")

        # 일부만 나갔으면 웹에도 보인다 — 저장한 뒤에 붙인 경고가 사라지지 않게 (docs/infra.md 25.419)
        reports.mark_partial_send(client, report_id, ["텔레그램 리포트 2조각 가운데 1조각만 보냈습니다"])  # type: ignore[arg-type]
        status, warn = client.conn.execute(
            "SELECT status, warnings_json FROM daily_reports WHERE id = ?", [report_id]
        ).fetchone()
        assert status == "partial" and "1조각만" in warn

    def test_같은_날은_지우고_다시_넣는다(self) -> None:
        client = SqliteClient()
        self._stocks(client)
        first = reports.store_report(client, market="KR", trade_date="2026-09-16", status="success", message="첫째",  # type: ignore[arg-type]
                                     warnings=[], items=reports.items_from(_composed()), batch_run_id=None)
        second = reports.store_report(client, market="KR", trade_date="2026-09-16", status="partial", message="둘째",  # type: ignore[arg-type]
                                      warnings=["w"], items=reports.items_from(_composed(1)), batch_run_id=None)
        assert second != first
        rows = client.conn.execute("SELECT summary_text, warnings_json FROM daily_reports").fetchall()
        assert rows == [("둘째", json.dumps(["w"], ensure_ascii=False))]
        assert client.conn.execute("SELECT COUNT(*) FROM report_items").fetchone()[0] == 2  # 1종목: 1부 1 + 2부 1
        # 다른 시장은 건드리지 않는다
        reports.store_report(client, market="US", trade_date="2026-09-16", status="success", message="미국",  # type: ignore[arg-type]
                             warnings=[], items=[], batch_run_id=None)
        assert client.conn.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0] == 2

    def test_리포트와_항목은_한_묶음이다(self) -> None:
        """D1 의 batch 는 한 트랜잭션이다 — 따로 보내면 항목 없는 `success` 리포트가 남는다 (docs/infra.md 25.341)."""
        client = SqliteClient()
        self._stocks(client)
        묶음들: list[list[str]] = []
        원래 = client.batch

        def 세는_batch(statements):  # type: ignore[no-untyped-def]
            묶음들.append([sql for sql, _ in statements])
            return 원래(statements)

        client.batch = 세는_batch  # type: ignore[method-assign]
        report_id = reports.store_report(
            client, market="KR", trade_date="2026-09-16", status="success", message="본문",  # type: ignore[arg-type]
            warnings=[], items=reports.items_from(_composed()), batch_run_id=None,
        )
        assert len(묶음들) == 1
        assert sum("INSERT INTO report_items" in sql for sql in 묶음들[0]) == 4
        n = client.conn.execute("SELECT COUNT(*) FROM report_items WHERE report_id = ?", [report_id]).fetchone()[0]
        assert n == 4
