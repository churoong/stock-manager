"""늦게 여는 날 KIS모의투자 국내 주문 접수 창 (docs/infra.md 25.2240).

2026-11-19 수능일(10:00 개장) 08:38 KST 에 낸 모의 주문이 모두 "40570000 모의투자 장시작전 입니다" 로 거부됐다 —
접수 시작이 08:30 고정(`ORDER_OPEN_KST`)이었다. 09:00 개장일엔 08:30~08:38 주문이 받아지므로 모의는 개장 30분 전부터
받는다고 본다. 이제 접수 시작 = max(08:30, 개장 − 30분), 마감 = max(15:20, 마감 − 10분). 08:27 실행은 한 시간을
기다릴 수 없어 막히고, 09:35 예비 실행이 발송 뒤 단계를 이어 낸다(`pre_open_resume`).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from datetime import time as dtime

import pytest

from batch.core import calendar as cal
from batch.core import db
from batch.jobs import broker_mirror as bmj
from batch.jobs import daily
from batch.jobs import desk as desk_job
from tests.test_paper_job_1445 import Client
from tests.test_portfolio_job import MemClient

수능 = "2026-11-19"
새해 = "2027-01-04"
보통 = "2026-11-20"


def _at(hhmm: str, day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hhmm}:00+09:00").astimezone(UTC)


# ── 접수 창 ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("day", [수능, 새해])
def test_10시_개장일은_09시30분부터_받는다(day: str) -> None:
    # 08:25·08:38(11-19 실제 거부 시각)·08:50 — 접수 시작(09:30)까지 10분 넘게 남아 막는다
    for t in ("08:25", "08:38", "08:50", "09:19"):
        assert bmj.send_block("KR", _at(t, day)) == bmj.PRE_OPEN_BLOCK, t
    # 09:20 — 10분 안이면 기다려 낸다(예전과 같은 `WAIT_MAX`)
    assert bmj.send_block("KR", _at("09:20", day)) is None
    assert bmj.send_block("KR", _at("09:31", day)) is None
    assert bmj.send_block("KR", _at("09:35", day)) is None
    assert bmj.kr_order_window(date.fromisoformat(day))[0] == _at("09:30", day)


def test_보통날은_예전_그대로_08시30분() -> None:
    assert bmj.send_block("KR", _at("08:19", 보통)) == bmj.PRE_OPEN_BLOCK
    assert bmj.send_block("KR", _at("08:25", 보통)) is None  # 5분 기다려 낸다
    assert bmj.kr_order_window(date.fromisoformat(보통)) == (_at("08:30", 보통), _at("15:20", 보통))


def test_마감은_수능일만_16시20분이고_15시20분보다_당기지_않는다() -> None:
    assert bmj.kr_order_window(date.fromisoformat(수능))[1] == _at("16:20", 수능)
    assert bmj.send_block("KR", _at("15:25", 수능)) is None
    assert bmj.send_block("KR", _at("16:19", 수능)) is None
    assert bmj.send_block("KR", _at("16:20", 수능)) == "장 끝난 뒤 — 보내지 않음"
    # 새해 첫 거래일은 10:00~15:30 — 마감은 그대로 15:20
    assert bmj.kr_order_window(date.fromisoformat(새해))[1] == _at("15:20", 새해)
    assert bmj.send_block("KR", _at("15:20", 새해)) == "장 끝난 뒤 — 보내지 않음"
    assert bmj.send_block("KR", _at("15:20", 보통)) == "장 끝난 뒤 — 보내지 않음"
    assert bmj.send_block("KR", _at("15:19", 보통)) is None
    assert bmj.kr_close_utc(date.fromisoformat(수능)) == _at("16:30", 수능)


# ── 단계의 기다림이 같은 창을 쓴다 ────────────────────────────────────────────


@pytest.fixture
def c(monkeypatch: pytest.MonkeyPatch) -> Client:
    for k, v in {"KIS_VTS_APP_KEY": "k", "KIS_VTS_APP_SECRET": "s", "KIS_VTS_ACCOUNT": "12345678-01"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(bmj, "MIRROR_ACCOUNT", "B")
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
        " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (101, 'B', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-11-18', ?, 'r', 1, 1, 't')",
        [수능],
    )  # fmt: skip
    return c


def test_단계는_수능일_접수_시작까지_기다리고_이르면_보내지_않는다(c: Client) -> None:
    from tests.test_kis_vts_1475 import _Sess

    s = _Sess()
    기록 = bmj.run(c, "KR", _at("08:38", 수능), session=s, sleep=lambda _x: pytest.fail("기다리면 안 된다"),
                   clock=lambda: _at("08:38", 수능))  # fmt: skip
    assert 기록["send"] == bmj.PRE_OPEN_BLOCK and s.orders == []
    잔: list[float] = []
    bmj.run(c, "KR", _at("09:22", 수능), session=s, sleep=잔.append, clock=lambda: _at("09:30", 수능))
    assert 잔 == [8 * 60 + 5.0] and len(s.orders) == 1  # 08:30 이 아니라 09:30 까지


def test_09시35분_전송은_10시_개장_전이라_늦은_전송으로_표시되지_않는다() -> None:
    """25.2220 — 개장 뒤 보낸 국내 시장가만 비교에서 뺀다. 09:35 는 10:00 개장 전"""
    줄 = [{"b_market": "KR", "i_order_type": "market", "trade_date": 수능, "sent_at": _at("09:35", 수능).isoformat()},
         {"b_market": "KR", "i_order_type": "market", "trade_date": 수능, "sent_at": _at("10:01", 수능).isoformat()}]
    assert bmj.mark_late_sends(줄) == 1 and "late_open_send" not in 줄[0] and 줄[1]["late_open_send"]


# ── 예비 실행이 이어 낸다 ──────────────────────────────────────────────────────


def _run(mem: MemClient, started: str, step_log: object, status: str = "partial") -> None:
    mem.conn.execute(
        "INSERT INTO batch_runs (job_name, market, trade_date, started_at, status, step_log)"
        " VALUES ('daily_kr', 'KR', '2026-11-18', ?, ?, ?)",
        [started, status, step_log if isinstance(step_log, str) or step_log is None else json.dumps(step_log)],
    )  # fmt: skip


def test_이어_낼지는_마지막_KIS_기록이_접수_시작_전이고_지금_보낼_수_있을_때만() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]

    def 이어(t: str, day: str = 수능, market: str = "KR") -> bool:
        return bmj.pre_open_resume(mem, "daily_kr", market, "2026-11-18", _at(t, day))

    assert 이어("09:35") is False  # 기록 없음
    _run(mem, "2026-11-18T23:27:00+00:00", {"broker": {"send": bmj.PRE_OPEN_BLOCK}, "sent": True})
    _run(mem, "2026-11-18T23:40:00+00:00", "{깨진")  # 깨진 행은 건너뛴다
    _run(mem, "2026-11-18T23:45:00+00:00", {"decision": "x"})  # KIS 단계가 없는 행도
    assert 이어("09:35") is True
    assert 이어("08:50") is False  # 아직 접수 시작 10분 넘게 전 — 이어 돌아도 또 막힌다
    assert 이어("16:25") is False  # 마감 뒤
    assert 이어("09:35", market="US") is False
    # 이어 돈 실행이 보냈으면 그 기록이 마지막 — 다시 이어 돌지 않는다
    _run(mem, "2026-11-19T00:35:00+00:00", {"broker": {"sent": {"accepted": 1}}})
    assert 이어("10:35") is False


def test_첫째가_꺼진_날_도전자만_막혀도_이어_돈다() -> None:
    """25.2243 (교차검증 [권고]) — 첫째 계좌가 꺼져 `broker.send` 가 없어도 도전자 기록을 본다"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _run(mem, "2026-11-18T23:27:00+00:00", {"broker": {"missing": ["KIS_VTS_APP_KEY"]},
                                            "broker2": {"send": bmj.PRE_OPEN_BLOCK}})  # fmt: skip
    assert bmj.pre_open_resume(mem, "daily_kr", "KR", "2026-11-18", _at("09:35", 수능)) is True
    mem2 = MemClient()
    db.apply_migrations(mem2)  # type: ignore[arg-type]
    _run(mem2, "2026-11-18T23:27:00+00:00", {"broker2": {"send": bmj.PRE_OPEN_BLOCK}})
    assert bmj.pre_open_resume(mem2, "daily_kr", "KR", "2026-11-18", _at("09:35", 수능)) is True


def test_보통날_이른_손_실행이_막히면_08시27분_예약_실행이_이어_낸다() -> None:
    """운용 보고 '이 실행이나 다음 예약 실행이 냅니다'(25.2250) 의 근거 — 보통날도 같은 길이다 (25.2250)"""
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _run(mem, "2026-11-19T22:50:00+00:00", {"broker": {"send": bmj.PRE_OPEN_BLOCK}})  # 보통날 07:50 손 실행
    assert bmj.pre_open_resume(mem, "daily_kr", "KR", "2026-11-18", _at("08:27", 보통)) is True
    assert bmj.pre_open_resume(mem, "daily_kr", "KR", "2026-11-18", _at("08:19", 보통)) is False  # 아직 막힘
    assert bmj.pre_open_resume(mem, "daily_kr", "KR", "2026-11-18", _at("09:35", 보통)) is True


def test_다른_까닭으로_막힌_날은_이어_돌지_않는다() -> None:
    mem = MemClient()
    db.apply_migrations(mem)  # type: ignore[arg-type]
    _run(mem, "2026-11-18T23:27:00+00:00", {"broker": {"send": "가상모의투자 단계를 건너뜀(잠금) — 오늘 베끼지 않음"}})
    assert bmj.pre_open_resume(mem, "daily_kr", "KR", "2026-11-18", _at("09:35", 수능)) is False


def test_읽기가_깨지면_이어_돌지_않는다() -> None:
    class 깨짐:
        def execute(self, *a: object, **k: object) -> None:
            raise RuntimeError("x")

    assert bmj.pre_open_resume(깨짐(), "daily_kr", "KR", "2026-11-18", _at("09:35", 수능)) is False


def _예비(monkeypatch: pytest.MonkeyPatch, 이어: bool, 보냄: bool = True) -> tuple[list, list, dict]:
    from tests.test_report_store_fail_1180 import _준비
    from tests.test_send_fail_kis_1715 import _kis_desk

    보낸, 저장, _ = _준비(monkeypatch, 0)
    부름 = _kis_desk(monkeypatch)
    # 08:27 실행이 성공·발송·운용 요약까지 끝난 날
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: True)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: 보냄)
    monkeypatch.setattr(desk_job, "post_send_done", lambda *a, **k: True)
    인자: list = []
    monkeypatch.setattr(bmj, "pre_open_resume", lambda *a, **k: (인자.append(a[1:4]), 부름.setdefault("now", []).append(a[4]), 이어)[2])
    부름["resume_args"] = 인자
    return 보낸, 저장, 부름


def test_예비_실행은_접수_전에_막힌_날_발송_없이_KIS_베끼기를_이어_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    보낸, 저장, 부름 = _예비(monkeypatch, True)
    assert daily.run("KR") == 0
    assert 보낸 == [] and 저장 == []  # 리포트는 다시 보내지도 덮지도 않는다
    assert len(부름["kis"]) == 2 and 부름["desk"] == [1]  # 첫째·도전자 베끼기와 운용 요약


def test_이어_낼_때_발송_기록이_없으면_리포트를_보내고_KIS도_이어_낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    """08:27 발송이 실패한 10:00 개장일 — 실패 경로도 KIS 단계를 돌아 PRE_OPEN_BLOCK 이 남는다. 09:35 예비 실행은
    리포트를 보내고 KIS 를 이어 낸다 (25.2243, 교차검증 [필수] — 25.2240 은 거꾸로 굳혔다)"""
    보낸, 저장, 부름 = _예비(monkeypatch, True, 보냄=False)
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: False)
    assert daily.run("KR") == 0
    assert 보낸 != [] and len(부름["kis"]) == 2
    assert len(저장) == 1  # 실패한 앞 실행 — 웹에도 처음 저장한다 (25.2246)


def test_이어_낼_때_앞_실행이_성공했으면_발송_기록이_없어도_다시_보내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """발송 여부 모름(25.493)·발송 시각 못 적음으로 `sent_at` 이 빈 성공 실행 — 다시 보내지 않고 KIS 만 잇는다 (25.2244)"""
    보낸, 저장, 부름 = _예비(monkeypatch, True, 보냄=False)  # has_successful_run 은 참 그대로
    assert daily.run("KR") == 0
    assert 보낸 == [] and len(부름["kis"]) == 2
    assert 저장 == []  # 아침 본문을 09:35 판으로 덮지 않는다 (25.2245)


def test_이어_낼_때_앞_실행이_실패했어도_보낸_기록이_있으면_다시_보내지도_덮지도_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """08:27 이 발송·발송 시각까지 적고 그 뒤 failed 로 닫힌 날 — `sent_exists` 길이 발송끝남을 참으로 둔다 (25.2247)"""
    보낸, 저장, 부름 = _예비(monkeypatch, True, 보냄=True)
    monkeypatch.setattr(daily.db, "has_successful_run", lambda *a, **k: False)
    assert daily.run("KR") == 0
    assert 보낸 == [] and 저장 == [] and len(부름["kis"]) == 2 and 부름["desk"] == [1]


def test_손_실행은_이어_낼_날이라도_성공한_날_리포트를_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    """force 는 이어냄을 보지 않는다 — `이어냄 and` 를 빼면 손 재실행이 '이미 보냄' 으로 빠졌다 (25.2245)"""
    보낸, _저장, _부름 = _예비(monkeypatch, True, 보냄=False)
    assert daily.run("KR", force=True) == 0
    assert 보낸 != []


def test_이어_낼지는_이_배치_이름과_리포트_거래일로_묻는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """LAST_BROKER_SEND_SQL 이 찾는 열(job_name·trade_date)과 같은 값을 넘긴다 — 시장 코드나 session_date 를 넘기면 행을
    못 찾아 이어 내기가 조용히 꺼진다 (25.2249, 교차검증 [권고])"""
    _보낸, _저장, 부름 = _예비(monkeypatch, True)
    daily.run("KR")
    assert 부름["resume_args"] == [("daily_kr", "KR", "2026-10-08")]  # _준비 의 trade_date(session_date 는 10-09)
    assert all(t.tzinfo is not None for t in 부름["now"])  # 지금 시각은 시간대가 붙은 값


def test_예비_실행은_막히지_않은_날엔_예전처럼_빠진다(monkeypatch: pytest.MonkeyPatch) -> None:
    보낸, _저장, 부름 = _예비(monkeypatch, False)
    assert daily.run("KR") == 0
    assert 보낸 == [] and 부름["kis"] == [] and 부름["paper"] == []


# ── 운용 보고 글 ─────────────────────────────────────────────────────────────


def test_운용_보고는_늦게_여는_날_예비_실행이_낸다고_말한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_paper_job_1445 import Client as PClient

    c = PClient(accounts=None)
    monkeypatch.setattr(desk_job.bmj, "MIRROR_ACCOUNT", "B")
    for k, v in (("KIS_VTS_APP_KEY", "k"), ("KIS_VTS_APP_SECRET", "s"), ("KIS_VTS_ACCOUNT", "12345678-01")):
        monkeypatch.setenv(k, v)

    def 머리(now: datetime) -> str:
        return desk_job.report_head(c, "KR", now, trade_date="2026-11-18", session_date=수능,
                                    step_log={"paper": {"accounts": {}}}, warnings=[])[-1]  # fmt: skip

    assert 머리(_at("08:28", 수능)) == "▸ KIS모의투자: 개장이 늦은 날이라 접수 시작(09:30) 뒤 이 실행이나 예비 실행이 냅니다"
    assert 머리(_at("08:28", 새해)).endswith("접수 시작(09:30) 뒤 이 실행이나 예비 실행이 냅니다")
    # 보통날 07:50 손 실행도 08:27 예약 실행이 이어 낸다 (25.2249·2250 — 예전 "오늘은 내지 않습니다" 는 거짓이었다)
    assert 머리(_at("07:50", 보통)) == "▸ KIS모의투자: 접수 시작(08:30) 뒤 이 실행이나 다음 예약 실행이 냅니다"


# ── 손 재실행은 이미 보낸 날 발송 뒤에만 덮는다 (25.567 의 `not 보낸_것_있음` 절반, 25.2248) ──────────────


def _손_재실행(monkeypatch: pytest.MonkeyPatch) -> tuple[list, list]:
    from tests.test_report_store_fail_1180 import _준비
    from tests.test_send_fail_kis_1715 import _kis_desk

    _보낸, 저장, _ = _준비(monkeypatch, 0)
    _kis_desk(monkeypatch)
    monkeypatch.setattr(daily.reports, "sent_exists", lambda *a, **k: True)  # 아침에 이미 보냈다
    monkeypatch.setattr(bmj, "pre_open_resume", lambda *a, **k: False)
    순서: list = []
    옛저장 = daily.reports.store_report
    monkeypatch.setattr(daily.reports, "store_report", lambda c, **k: (순서.append("저장"), 옛저장(c, **k))[1])
    return 저장, 순서


def test_손_재실행은_이미_보낸_날_새_리포트를_보낸_뒤에_덮는다(monkeypatch: pytest.MonkeyPatch) -> None:
    저장, 순서 = _손_재실행(monkeypatch)
    monkeypatch.setattr(daily.telegram, "send", lambda text, *a, **k: (순서.append("발송"), [1])[1])
    assert daily.run("KR", force=True) == 0
    assert 순서 == ["발송", "저장"] and len(저장) == 1


def test_손_재실행_발송이_실패하면_아침_행을_덮지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.notify import telegram

    저장, _순서 = _손_재실행(monkeypatch)

    def 실패(*_a: object, **_k: object) -> None:
        raise telegram.TelegramError("429 retry_after 3600")

    monkeypatch.setattr(daily.telegram, "send", 실패)
    daily.run("KR", force=True)
    assert 저장 == []  # 받은 본문·발송 시각이 웹 이력에 남는다


def test_달력이_이미_수능_늦춤을_알면_두_번_늦추지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """exchange_calendars 가 수능일을 10:00·16:30 으로 주는 해 — 한 시간 더 늦추면 보내기 마감이 17:20 이 된다 (25.2249)"""
    from datetime import date as _d

    tz = cal.market_tz("KR")
    monkeypatch.setattr(cal, "session_open_utc", lambda m, d: datetime.combine(d, dtime(10, 0), tzinfo=tz).astimezone(UTC))
    monkeypatch.setattr(cal, "session_close_utc",
                        lambda m, d: datetime.combine(d, dtime(16, 30), tzinfo=tz).astimezone(UTC))  # fmt: skip
    날 = _d.fromisoformat(수능)
    assert bmj.kr_close_utc(날).astimezone(tz).time() == dtime(16, 30)
    시작, 끝 = bmj.kr_order_window(날)
    assert (시작.time(), 끝.time()) == (dtime(9, 30), dtime(16, 20))


def test_올해_수능일이_9월부터는_목록에_있다() -> None:
    """수능일은 달력이 모른다 — 목록에 없으면 그해 KIS 주문이 08:38 에 나가 "장시작전" 으로 모두 거부된다(2026-11-19,
    25.2240). 9월이면 그해 수능일이 공고돼 있으니 `core/calendar.KR_CSAT_DAYS` 에 더할 것 (25.2249 — 정의처는 25.2356 부터 core/calendar)"""
    from datetime import date as _d

    from batch.jobs import monitor_targets as mt

    오늘 = _d.today()
    if 오늘.month >= 9:
        assert any(d.startswith(f"{오늘.year}-") for d in mt.KR_CSAT_DAYS), f"{오늘.year} 수능일을 batch/core/calendar.py KR_CSAT_DAYS 에 더하세요"
