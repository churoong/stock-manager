"""장중 감시 — 다음 장이 권리락·무상증자·분할 첫날인 국내 종목 표시 (docs/infra.md 25.1725, 장중 감사 [필수] 1)."""

from __future__ import annotations

import pytest

from batch.jobs import monitor_targets as mt
from tests.test_paper_job_1445 import Client


def _일정(c: Client, code: str, kind: str, day: str, fetched: str = "2026-10-01T00:00:00+00:00") -> None:
    c.conn.execute("INSERT INTO kr_corp_events (code, kind, record_date, name, detail, source, fetched_at)"
                   " VALUES (?, ?, ?, 'n', '{}', 't', ?)", [code, kind, day, fetched])  # fmt: skip


def test_다음_장이_권리락일인_종목만() -> None:
    """10-23(금) 08:00 KST 에 만든 목록 — 다음 장은 10-23, 기준일 10-26(월)이면 10-23 이 권리락일"""
    c = Client(accounts=None)
    _일정(c, "005930", "bonus", "2026-10-26")
    _일정(c, "000660", "dividend", "2026-10-26")  # 현금배당은 표시하지 않는다
    지금 = "2026-10-22T23:00:00+00:00"
    assert mt.action_day_ids(c, [1, 3], 지금) == ({1}, "2026-10-23")
    # 기준일이 그다음 주면 아니다(그날 장은 아직 권리락 전)
    assert mt.action_day_ids(c, [1], "2026-10-21T23:00:00+00:00")[0] == set()
    assert mt.action_day_ids(c, [], 지금) == (set(), None)


def test_일정을_못_읽으면_비운다() -> None:
    class 깨짐:
        def execute(self, *_a: object, **_k: object) -> object:
            raise RuntimeError("no such table: kr_corp_events")

    assert mt.action_day_ids(깨짐(), [1], "2026-10-22T23:00:00+00:00") == (set(), None)  # type: ignore[arg-type]


def test_목록에_날짜_붙은_표시가_실린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """배선 — `build_targets` 가 그 종목 사유에 `action_day:다음 장` 을 싣는다(웹이 그날만 가격 알림을 뺀다)"""
    import json

    monkeypatch.setattr(mt, "expected_signal_date", lambda *_a, **_k: "2026-09-16")

    from batch.core import db
    from tests.test_monitor_targets import MemClient

    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
              " yahoo_symbol) VALUES (2, '000660', 'KOSPI', 'KR', '종목2', 'KRW', 'active', 't', 't', '000660.KS')")  # fmt: skip
    c.execute("INSERT INTO signals (stock_id, as_of_date, horizon, signal_type, buy_zone_low, buy_zone_high, currency,"
              " tranche_plan, target_price, stop_price, size_reduction, rationale_text, rationale_data, calc_version,"
              " created_at) VALUES (2, '2026-09-16', 'short', 'x', 980, 1000, 'KRW', '[]', 1200, 900, 1, 'x', '{}', 1,"
              " 't')")  # fmt: skip
    # 2026-09-17(목) 09:00 KST — 다음 장 09-17, 기준일 09-18(금)이면 09-17 이 권리락일
    c.execute("INSERT INTO kr_corp_events (code, kind, record_date, name, detail, source, fetched_at)"
              " VALUES ('000660', 'rights', '2026-09-18', 'n', '{}', 't', 't')")
    targets = mt.build_targets(mem, "KR", "2026-09-17T00:00:00+00:00")  # type: ignore[arg-type]
    사유 = json.loads(next(v for v in targets[0] if isinstance(v, str) and v.startswith("[")))
    assert "action_day:2026-09-17" in 사유


def test_질의의_종류가_상수와_같다() -> None:
    for k in mt.ACTION_DAY_KINDS:
        assert f"'{k}'" in mt.ACTION_DAY_SQL
    assert "'dividend'" not in mt.ACTION_DAY_SQL


# ── 25.1725 교차검증 반영 ───────────────────────────────────────────────────────


def test_기준일이_옮겨진_옛_행은_보지_않는다() -> None:
    """[필수] 1 — 유상증자 기준일이 10-26 → 11-10 으로 정정되면 옛 행이 남아 10-23 에 하루 내내 가격 알림이 빠졌다"""
    c = Client(accounts=None)
    _일정(c, "005930", "rights", "2026-10-26", "2026-10-05T00:00:00+00:00")
    _일정(c, "005930", "rights", "2026-11-10", "2026-10-15T00:00:00+00:00")
    assert mt.action_day_ids(c, [1], "2026-10-22T23:00:00+00:00") == (set(), "2026-10-23")


def test_기준일이_휴장일이면_그_앞_마지막_거래일의_전_거래일() -> None:
    """[권고] 2 — 기준일 10-24(토)면 마지막 거래일 10-23 의 전 거래일 10-22 가 권리락일(예전 창은 10-23 으로 하루 늦었다)"""
    assert mt.ex_date("2026-10-24").isoformat() == "2026-10-22"
    assert mt.ex_date("2026-10-26").isoformat() == "2026-10-23"  # 거래일이면 전 1거래일
    c = Client(accounts=None)
    _일정(c, "005930", "bonus", "2026-10-24")
    assert mt.action_day_ids(c, [1], "2026-10-21T23:00:00+00:00") == ({1}, "2026-10-22")
    assert mt.action_day_ids(c, [1], "2026-10-22T23:00:00+00:00")[0] == set()


def test_분할은_표시하지_않는다() -> None:
    """[권고] 3 — 분할·병합은 매매정지 뒤 변경상장일이 첫날이라 기준일 규칙이 맞지 않는다(±31% 가드가 잡는다)"""
    c = Client(accounts=None)
    _일정(c, "005930", "split", "2026-10-26")
    assert mt.action_day_ids(c, [1], "2026-10-22T23:00:00+00:00")[0] == set()
    assert "'split'" not in mt.ACTION_DAY_SQL


def test_긴_연휴_뒤_기준일도_잡는다() -> None:
    """25.1726 교차검증 [권고] 1 — 2017 열흘 추석(9-30~10-9) 뒤 첫 거래일 10-10 이 기준일이면 권리락일은 9-29. 창이 10일이면 놓쳤다"""
    assert mt.ex_date("2017-10-10").isoformat() == "2017-09-29"
    c = Client(accounts=None)
    _일정(c, "005930", "rights", "2017-10-10", "2017-09-01T00:00:00+00:00")
    assert mt.action_day_ids(c, [1], "2017-09-28T23:00:00+00:00") == ({1}, "2017-09-29")


def test_리포트_일정_줄도_같은_락일() -> None:
    """25.1728 — 리포트 기업행위 일정의 락일도 기준일 이하 마지막 거래일의 전 거래일(2024 기준일 12-31 배당락은 12-27)"""
    from datetime import date

    from batch.core import calendar as cal

    assert cal.ex_date_kr(date(2024, 12, 31)) == date(2024, 12, 27)
    assert cal.ex_date_kr(date(2026, 10, 26)) == date(2026, 10, 23)
    assert mt.ex_date("2026-10-24") == cal.ex_date_kr(date(2026, 10, 24))
