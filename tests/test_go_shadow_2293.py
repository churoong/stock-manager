"""주문 허가 판정(GO / 매도만 / NO-GO)의 그림자 기록 (docs/paper.md 15.13, docs/infra.md 25.2293).

docs/roadmap.md 1장 #2 의 첫 조각. KIS모의투자 단계 기록에서 끌어내 기록만 한다 — 주문을 막지 않는다.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from batch.jobs import broker_mirror as jb
from batch.services import broker_mirror as bm

뿌리 = Path(__file__).resolve().parent.parent


def test_아무_것도_안_걸리면_go() -> None:
    평소 = {"reconciled": {"filled": 3, "unfilled": 1}, "diff": 0, "holdings": 12, "halted": False,
           "sent": {"accepted": 4}, "planned": {"cleanup": 0, "mirror": 4}}  # fmt: skip
    assert bm.go_shadow(평소) == {"verdict": "go", "reasons": []}
    assert bm.go_shadow({}) == {"verdict": "go", "reasons": []}


@pytest.mark.parametrize(("칸", "값"), [("halted", True), ("balance_truncated", True), ("send", "휴장일")])
def test_지금도_주문을_내지_않는_경우는_no_go(칸: str, 값: object) -> None:
    assert bm.go_shadow({칸: 값}) == {"verdict": "no_go", "reasons": [칸]}


@pytest.mark.parametrize(("기록", "갈래"), [
    ({"reconciled": {"filled": 2, "unknown": 1}}, "reconcile_unknown"),
    ({"reconcile_truncated": True}, "reconcile_truncated"),
    ({"diff": 2}, "diff"),
    ({"unknown_codes": 1}, "unknown_codes"),
])  # fmt: skip
def test_체결을_모르거나_잔고가_어긋나면_매도만(기록: dict, 갈래: str) -> None:
    assert bm.go_shadow(기록) == {"verdict": "sell_only", "reasons": [갈래]}


def test_둘_다_걸리면_no_go_이고_까닭은_모두_남는다() -> None:
    r = bm.go_shadow({"send": "접수 시작 전", "diff": 1, "reconciled": {"unknown": 2}, "halted": False})
    assert r == {"verdict": "no_go", "reasons": ["send", "reconcile_unknown", "diff"]}


def test_0_과_거짓과_빈_글은_걸리지_않는다() -> None:
    깨끗 = {"halted": False, "balance_truncated": False, "send": "", "reconciled": {"unknown": 0}, "diff": 0,
           "reconcile_truncated": False, "unknown_codes": 0}  # fmt: skip
    assert bm.go_shadow(깨끗)["verdict"] == "go"
    # 맞추기 칸이 dict 가 아니어도(예전 기록 꼴) 죽지 않는다
    assert bm.go_shadow({"reconciled": "모름"})["verdict"] == "go"
    # 기록의 다른 칸(체결과 맞은 수·보낸 수)은 판정에 쓰지 않는다
    assert bm.go_shadow({"reconciled": {"rejected": 5, "unfilled": 9}, "not_sent": {"x": 3}})["verdict"] == "go"


def test_갈래는_상수가_정의처다() -> None:
    assert list(bm.GO_NO_GO) == ["halted", "balance_truncated", "send"]
    assert list(bm.GO_SELL_ONLY) == ["reconcile_unknown", "reconcile_truncated", "diff", "unknown_codes", "sending_today",
                                     "new_code", "read_budget"]  # read_budget 25.2334
    assert not set(bm.GO_NO_GO) & set(bm.GO_SELL_ONLY)


def test_단계_기록의_꼬리에_실리고_깨져도_단계는_끝난다(monkeypatch: pytest.MonkeyPatch) -> None:
    세션 = type("S", (), {"calls": 3, "rate_retries": 0, "net_retries": 0})()
    r = jb._끝({"diff": 1, "holdings": 5}, 세션)
    assert r == {"diff": 1, "holdings": 5, "go_shadow": {"verdict": "sell_only", "reasons": ["diff"]}, "calls": 3}

    def 깨짐(_기록: dict) -> dict:
        raise ValueError("판정이 깨졌다")

    monkeypatch.setattr(jb.bm, "go_shadow", 깨짐)
    assert jb._끝({"diff": 1}, 세션) == {"diff": 1, "go_shadow": {"error": "ValueError"}, "calls": 3}


def test_갈래_열쇠마다_단계가_실제로_그_칸을_쓴다() -> None:
    """25.2298 (25.2293 교차검증 [권고]) — 시험이 손으로 만든 기록만 봐서, 단계가 쓰는 칸 이름이 바뀌면(예:
    `reconcile_truncated` → `recon_truncated`) 그 갈래가 조용히 죽어 "한 번도 안 선 갈래" 로 읽혔다. 열쇠마다
    `run` 이 그 이름으로 기록에 쓰는지 소스로 묶는다(`reconcile_unknown` 은 `reconciled` 의 `unknown` 에서 읽는다)"""
    글 = inspect.getsource(jb.run)  # run 안에서 — 모듈 어디든(죽은 코드) 있기만 하면 통과하지 않게(25.2301)
    for k in (*bm.GO_NO_GO, *bm.GO_SELL_ONLY):
        칸 = "reconciled" if k == "reconcile_unknown" else k
        assert f'기록["{칸}"] = ' in 글, k


def test_같은_날_다시_돈_실행은_앞_실행의_응답_없음을_매도만으로_센다() -> None:
    """25.2299 (25.2293 교차검증 [권고]) — 앞 실행이 응답 없음으로 남긴 오늘 `sending` 줄은 맞추기 대상(어제까지)이
    아니라 다시 돈 실행의 판정이 `go` 였다. 흐름: 첫 실행이 응답을 못 받고, 같은 날 다시 돈 실행이 그 수를 센다"""
    from batch.sources import kis_vts as kv
    from tests.test_kis_vts_1475 import _at, _Sess, 키
    from tests.test_paper_job_1445 import Client

    mp = pytest.MonkeyPatch()
    try:
        for k, v in 키.items():
            mp.setenv(k, v)
        mp.setattr(jb, "MIRROR_ACCOUNT", "B")

        def 새판() -> Client:
            c = Client()
            for oid, 회차, price, q in ((101, 1, 9_800, 3), (103, 2, 9_600, 2)):
                c.conn.execute(
                    "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                    " currency, decided_on, fill_session, reason, rule_version, tranche, created_at)"
                    " VALUES (?, 'B', 'KR', 1, 'buy', 'limit', ?, ?, 'KRW', '2026-10-15', '2026-10-16', 'r', 1, ?, 't')",
                    [oid, price, q, 회차],
                )  # fmt: skip
            return c

        # 앞 실행이 접수를 받았으면(accepted) 다시 돈 실행은 세지 않는다 — 응답 없음만이다
        c = 새판()
        jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
        assert "sending_today" not in jb.run(c, "KR", _at("08:45"), session=_Sess(), sleep=lambda _x: None)
        c = 새판()
        첫 = jb.run(c, "KR", _at("08:40"), session=_Sess(fail={"005930": kv.NoResponse("응답 없음")}), sleep=lambda _x: None)
        assert 첫["sent"] == {"no_response": 2} and "sending_today" not in 첫  # 첫 실행 앞에는 오늘 줄이 없다
        # 앞 실행이 남긴 정리 줄(내부 주문 없음)도 체결을 모르는 주문이다 — 함께 센다(25.2304)
        c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                       " order_type, quantity, trade_date, reason, status, source, created_at) VALUES ('kis_vts', NULL,"
                       " 'B', 'KR', 3, '000660', 'sell', 'market', 1, '2026-10-16', ?, 'sending', 't', 't')",
                       [jb.bm.REASON_CLEANUP])  # fmt: skip
        # 장중 손절 줄도(25.2305)
        c.conn.execute("INSERT INTO paper_broker_orders (broker, paper_order_id, account_id, market, stock_id, code, side,"
                       " order_type, quantity, trade_date, reason, status, source, created_at) VALUES ('kis_vts', NULL,"
                       " 'B', 'KR', 3, '000660', 'sell', 'market', 1, '2026-10-16', ?, 'sending', 't', 't')",
                       [jb.bm.GUARD_REASON_LIST[0]])  # fmt: skip
        둘 = jb.run(c, "KR", _at("08:45"), session=_Sess(), sleep=lambda _x: None)
        assert 둘["sending_today"] == 4
        assert 둘["go_shadow"]["verdict"] == "sell_only" and "sending_today" in 둘["go_shadow"]["reasons"]
    finally:
        mp.undo()


def _코드판(monkeypatch: pytest.MonkeyPatch, 지금: dict, 앞줄: list[tuple[str, str, dict]]):  # type: ignore[no-untyped-def]
    """KIS 흐름 판 + 앞 일일 배치 실행 줄 [(상태, 끝난 시각, step_log)] + 이 실행의 지문"""
    from tests.test_kis_vts_1475 import 키
    from tests.test_paper_job_1445 import Client

    for k, v in 키.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(jb, "MIRROR_ACCOUNT", "B")
    monkeypatch.setattr(jb.db, "run_fingerprint", lambda: dict(지금))
    c = Client()
    c.conn.execute(
        "INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity, currency,"
        " decided_on, fill_session, reason, rule_version, tranche, created_at)"
        " VALUES (101, 'B', 'KR', 1, 'buy', 'limit', 9800, 3, 'KRW', '2026-10-15', '2026-10-16', 'r', 1, 1, 't')"
    )  # fmt: skip
    for 상태, 끝, 로그 in 앞줄:
        c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, finished_at, step_log)"
                       " VALUES ('daily_kr', 'KR', '2026-10-15', ?, ?, ?, ?)", [상태, 끝, 끝, json.dumps(로그)])  # fmt: skip
    return c


def _지문(sha: str | None, code: str | None) -> dict:
    return {jb.db.RUN_FINGERPRINT_KEY: {"sha": sha, "code": code}}


def test_새_코드_첫날은_매도만으로_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2306 — 배치 코드 지문이 앞 성공·부분 성공 일일 배치와 다르면 `new_code`. 주문은 그대로 나간다"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _코드판(monkeypatch, {"sha": "s2", "code": "new"},
               # 먼저 끝난 성공 줄은 지금과 같은 코드지만 늦게 끝난 부분 성공 줄이 앞 실행이다(두 상태 가운데 늦은 것)
               [("success", "2026-10-13T23:40:00+00:00", _지문("s0", "new")),
                ("partial", "2026-10-14T23:40:00+00:00", _지문("s1", "old")),
                ("failed", "2026-10-14T23:50:00+00:00", _지문("s9", "new")),  # 실패는 앞 실행이 아니다
                # **같은 날**(한국 10-16 08:30) 앞서 끝난 실행은 앞 실행이 아니다(25.2308) — 08:27 이 접수 전에 막혀 partial
                # 로 끝나고 이 실행(예비)이 실제로 사는 날, 그 줄을 앞으로 잡으면 칸이 빠졌다
                ("partial", "2026-10-15T23:30:00+00:00", _지문("s2", "new"))])  # fmt: skip
    s = _Sess()
    기록 = jb.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert 기록["new_code"] is True and "new_code" in 기록["go_shadow"]["reasons"] and len(s.orders) == 1


@pytest.mark.parametrize(("지금", "앞줄"), [
    ({"sha": "s2", "code": "old"}, [("partial", "2026-10-14T23:40:00+00:00", {"fingerprint": {"sha": "s1", "code": "old"}})]),
    ({"sha": "s2", "code": "new"}, []),  # 앞 실행이 없다(첫 주) — 모름
    # 같은 날(한국 10-16 08:30) 줄만 있다 — 앞 거래일 실행이 없으니 모름(25.2309)
    ({"sha": "s2", "code": "new"}, [("partial", "2026-10-15T23:30:00+00:00", {"fingerprint": {"sha": "s1", "code": "old"}})]),
    ({"sha": "s2", "code": "new"}, [("success", "2026-10-14T23:40:00+00:00", {"fingerprint": {"sha": "s1"}})]),  # 25.2302 전
    # 앞 실행이 로컬(커밋 없음)이면 견줄 수 없다 — 그 줄은 뺀다
    ({"sha": "s2", "code": "new"}, [("success", "2026-10-14T23:40:00+00:00", {"fingerprint": {"sha": None, "code": "x"}})]),
    ({"sha": None, "code": "new"}, [("success", "2026-10-14T23:40:00+00:00", {"fingerprint": {"sha": "s1", "code": "old"}})]),
])  # fmt: skip
def test_새_코드가_아니거나_모르면_칸이_없다(monkeypatch: pytest.MonkeyPatch, 지금: dict, 앞줄: list) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _코드판(monkeypatch, 지금, 앞줄)
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
    assert "new_code" not in 기록 and "new_code" not in 기록["go_shadow"]["reasons"]


def test_앞_코드를_못_읽어도_주문_단계는_간다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _코드판(monkeypatch, {"sha": "s2", "code": "new"}, [])
    진짜 = c.execute

    def 실행(sql, params=None):  # type: ignore[no-untyped-def]
        if sql == jb.PREV_CODE_SQL:
            raise RuntimeError("no such function: json_extract")
        return 진짜(sql, params)

    monkeypatch.setattr(c, "execute", 실행)
    s = _Sess()
    기록 = jb.run(c, "KR", _at("08:40"), session=s, sleep=lambda _x: None)
    assert "new_code" not in 기록 and len(s.orders) == 1 and 기록["sent"] == {"accepted": 1}
    # 모름으로 남긴다(25.2308) — 칸이 없으면 분포에서 "새 코드 아님" 과 구별되지 않았다
    assert 기록["new_code_error"] == "RuntimeError"


def test_깨진_step_log_한_줄이_앞_코드_읽기를_깨지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2308 — `json_valid` 없이 `json_extract` 가 JSON 아닌 줄을 만나면 질의 전체가 깨졌다(25.1633 의 같은 교훈)"""
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _코드판(monkeypatch, {"sha": "s2", "code": "new"},
               [("partial", "2026-10-13T23:40:00+00:00", _지문("s1", "old"))])  # fmt: skip
    c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, finished_at, step_log)"
                   " VALUES ('daily_kr', 'KR', '2026-10-14', 'success', '2026-10-14T23:40:00+00:00',"
                   " '2026-10-14T23:40:00+00:00', '깨진 글')")  # fmt: skip
    기록 = jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)
    assert 기록["new_code"] is True and "new_code_error" not in 기록


def test_로컬_실행은_앞_코드를_읽지도_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_kis_vts_1475 import _at, _Sess

    c = _코드판(monkeypatch, {"sha": None, "code": "new"}, [])
    진짜 = c.execute
    monkeypatch.setattr(c, "execute", lambda sql, params=None: pytest.fail("읽으면 안 된다") if sql == jb.PREV_CODE_SQL
                        else 진짜(sql, params))  # fmt: skip
    jb.run(c, "KR", _at("08:40"), session=_Sess(), sleep=lambda _x: None)


@pytest.mark.parametrize(("날", "시장", "자정"), [
    ("2026-10-16", "KR", "2026-10-15T15:00:00+00:00"),
    ("2026-01-15", "US", "2026-01-15T05:00:00+00:00"),  # 겨울(EST, UTC−5)
    ("2026-07-15", "US", "2026-07-15T04:00:00+00:00"),  # 여름(EDT, UTC−4)
    ("2026-03-09", "US", "2026-03-09T04:00:00+00:00"),  # 서머타임 시작(3-08) 다음 날
])  # fmt: skip
def test_그_시장_자정을_UTC_로(날: str, 시장: str, 자정: str) -> None:
    """25.2309 — 미국은 동부 자정이라 서머타임이 들어간다. run 이 이 함수로 앞 거래일 경계를 잡는다"""
    from datetime import date

    assert jb.market_midnight_utc(date.fromisoformat(날), 시장) == 자정
    assert "자정 = market_midnight_utc(오늘, market)" in inspect.getsource(jb.run)


def test_미국_앞_코드는_동부_날짜로_가른다() -> None:
    """미국 예비 실행(14:27 UTC)과 전날 실행 — 동부 자정 전 줄만 앞 실행이다"""
    from datetime import date

    from tests.test_paper_job_1445 import Client

    c = Client()
    # 둘째 줄은 두 자정 **사이** — 동부 01-14 22:00(앞날)이지만 한국으로는 01-15 12:00(같은 날)이다(25.2311 — 처음 판은 두 줄이
    # 두 자정 모두에서 같은 쪽이라 한국 자정으로 바꿔도 통과했다)
    for 끝, 코드 in (("2026-01-14T13:40:00+00:00", "old"), ("2026-01-15T03:00:00+00:00", "evening"),
                     ("2026-01-15T05:30:00+00:00", "today")):
        c.conn.execute("INSERT INTO batch_runs (job_name, market, trade_date, status, started_at, finished_at, step_log)"
                       " VALUES ('daily_us', 'US', '2026-01-14', 'partial', ?, ?, ?)",
                       [끝, 끝, json.dumps(_지문("s1", 코드))])  # fmt: skip
    # 동부 자정(05:00Z) 전 줄 가운데 늦은 것 = "evening". 한국 자정(01-14 15:00Z)으로 갈랐다면 "old" 였다
    assert jb.prev_code(c, "US", jb.market_midnight_utc(date(2026, 1, 15), "US")) == "evening"


def test_작업_이름은_일일_배치와_같다() -> None:
    from batch.jobs import daily

    assert {m: daily.job_name(m) for m in ("KR", "US")} == jb.DAILY_JOB


def test_기록만_한다_주문을_막거나_바꾸지_않는다() -> None:
    """판정을 읽는 코드가 없다 — 켜는 일은 따로(그림자 기록이 쌓인 뒤, 사용자 결정)"""
    글 = inspect.getsource(jb.run)
    assert "go_shadow" not in 글  # 주문을 내는 본문은 판정을 보지 않는다(꼬리 `_끝` 만 만든다)
    for 파일 in ("batch/jobs/broker_mirror.py", "batch/jobs/intraday_guard.py", "batch/jobs/paper.py", "batch/jobs/daily.py"):
        본문 = (뿌리 / 파일).read_text()
        assert 'get("go_shadow")' not in 본문 and '["go_shadow"]' not in 본문, 파일
    assert "execute" not in inspect.getsource(bm.go_shadow) and "client" not in inspect.getsource(bm.go_shadow)


def test_문서가_갈래와_읽는_법을_적는다() -> None:
    글 = (뿌리 / "docs" / "paper.md").read_text()
    for 조각 in ("### 15.13 주문 허가 판정", "25.2293", "`go_shadow`", "주문을 막지 않는다", "`sell_only`", "`no_go`",
               *(f"`{k}`" for k in (*bm.GO_NO_GO, *bm.GO_SELL_ONLY)), "새 코드 첫날", "오탐", "25.2298", "`planned_buys`",
               "같은 날 다시 돈 실행", "`broker2`", "칸이 없는 실행", "미국"):  # fmt: skip
        assert 조각 in 글, 조각
