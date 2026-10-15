"""그날 첫 리포트 저장이 실패하면 — 알림 없이 정상처럼 가고 다시 저장하지도 않던 것 (docs/infra.md 25.1180, 감사 재현)."""

from __future__ import annotations

import pytest

from batch.core import calendar as cal
from batch.jobs import daily, etf_link
from batch.jobs import signals as signals_job
from batch.services import report_picks as rp


def _row(i: int) -> rp.SignalRow:
    return rp.SignalRow(
        stock_id=i, ticker=f"{i:06d}", name=f"종목{i}", market="KOSPI", horizon="mid", signal_type="실적 모멘텀",
        currency="KRW", buy_zone_low=9000.0, buy_zone_high=10000.0, suggested_weight_pct=10.0,
        suggested_amount=1_000_000.0, size_reduction=1.0,
        tranche_plan=[{"step": 1, "ratio": 1.0, "price": 10000, "amount": 1_000_000}], rationale_text="매출 18.2%",
        as_of_date="2026-10-08", total_score=80.0 - i, close=10000.0, close_date="2026-10-08",
        criteria=[{"label": "매출", "display": "18.2%", "threshold": ">0", "source": "dart", "value": 1}],
    )  # fmt: skip


class _가짜:
    def close(self) -> None:
        pass

    def execute(self, *a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("하네스에는 DB 가 없다")


def _준비(monkeypatch: pytest.MonkeyPatch, 실패수: int) -> tuple[list, list, list]:
    m = monkeypatch.setattr
    m(daily, "TursoClient", _가짜)
    결정 = cal.RunDecision(True, "테스트", "2026-10-08", "2026-10-09", 30)
    m(daily.cal, "decide", lambda mk, now=None, force=False: 결정)
    m(daily.cal, "scheduled_for", lambda mk, d: None)
    m(daily.backend, "resolved_backend", lambda: "turso")
    m(daily.db, "apply_migrations", lambda c: [])
    m(daily.db, "has_successful_run", lambda *a, **k: False)
    m(daily.reports, "sent_exists", lambda *a, **k: False)
    m(daily.db, "start_batch_run", lambda *a, **k: 1)
    m(daily.db, "finish_batch_run", lambda c, rid, **k: None)
    m(daily.db, "measure_db_size", lambda c: None)
    m(daily.db, "db_size_note", lambda c: None)
    m(etf_link, "link", lambda c, mk: {})
    m(daily, "collect_prices", lambda c, mk, d: ([{"name": "x", "close": 1, "change_pct": 0, "volume": 1,
                                                    "currency": "KRW", "source": "krx", "trade_date": d}], [], "1종목"))  # fmt: skip
    m(daily, "refresh_recommendations", lambda mk, d: [])
    m(daily, "refresh_portfolio", lambda mk=None: [])
    m(daily, "load_signal_rows", lambda c, country, upto=None: ([_row(1), _row(2)], "2026-10-08"))
    m(signals_job, "load_settings", lambda c, cur, d: {
        "total_investable": 10_000_000.0, "max_weight_per_sector": 30.0, "max_weight_per_stock": 10.0,
        "min_order_amount": 0.0, "fx_missing": False, "fx": None, "setting_warnings": []})  # fmt: skip
    m(daily.trend, "load_settings", lambda c: {"warnings": []})
    m(daily, "report_regimes", lambda c, mk, s, d: ({}, "시장 국면: 테스트"))
    m(daily, "load_holdings", lambda c, cur, fx, w: [])
    m(daily, "_sell_flags_for_report", lambda c, country, w=None: [])
    m(daily, "load_price_series", lambda c, ids, d, w=None, values=None: {})
    m(daily, "_other_report_reserved", lambda *a, **k: (0.0, None))
    m(daily, "_shake_weights", lambda c: (None, 0.0))
    m(daily, "_pick_history", lambda c, mk, d, w: {})
    m(daily, "_self_grade", lambda c, mk, d, w: None)
    m(daily, "_holding_scores", lambda c, a, w: [])
    m(daily, "_corp_event_lines", lambda c, d, j, w=None: [])
    m(daily, "_divergence_lines", lambda c, d, p, w=None: [])
    저장: list = []
    남은실패 = [실패수]

    def store(client, **k):  # noqa: ANN001, ANN003
        if 남은실패[0] > 0:
            남은실패[0] -= 1
            raise RuntimeError("SQLITE_BUSY: database is locked")
        저장.append(k)
        return 7

    m(daily.reports, "store_report", store)
    m(daily.reports, "mark_sent", lambda *a, **k: None)
    m(daily.reports, "mark_partial_send", lambda *a, **k: None)
    보낸: list = []
    m(daily.telegram, "send", lambda text, *a, **k: (보낸.append(text), [1])[1])
    m(daily.telegram, "append_disclaimer", lambda t: t)
    알림: list = []
    m(daily, "_notify_failure", lambda client, market, name, error_text, dry_run, title="배치 실패":
      알림.append((title, error_text)))  # fmt: skip
    return 보낸, 저장, 알림


def test_한_번_실패는_다시_저장하고_말없이_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    보낸, 저장, 알림 = _준비(monkeypatch, 1)
    assert daily.run("KR") == 0
    assert len(저장) == 1 and not 알림
    assert not any("웹에 저장하지 못했습니다" in t for t in 보낸)


def test_두_번_다_실패하면_본문에_적고_보낸_뒤_알린다(monkeypatch: pytest.MonkeyPatch) -> None:
    보낸, 저장, 알림 = _준비(monkeypatch, 2)
    assert daily.run("KR") == 0
    assert not 저장
    assert any("리포트를 웹에 저장하지 못했습니다" in t for t in 보낸)
    assert [제목 for 제목, _ in 알림] == ["리포트 저장 실패"]


def test_저장_실패_알림이_웹에_전체가_있다고_하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """알림 원인에 표식이 없으면 `failure_alert` 가 "웹의 오늘 리포트에 전체가 있습니다" 를 붙였다 (25.1216)."""
    from batch.notify import formatter

    _, _, 알림 = _준비(monkeypatch, 2)
    assert daily.run("KR") == 0
    (제목, 원인), = 알림
    글 = formatter.failure_alert(market="KR", job_name="daily_kr", error_text=원인, last_success=None, title=제목)
    assert "웹의 오늘 리포트에 전체가 있습니다" not in 글
    assert "웹에서는 볼 수 없습니다" in 글
