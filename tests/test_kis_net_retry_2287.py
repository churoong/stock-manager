"""KIS모의투자 — 조회는 다시 받고, 주문은 다시 보내지 않고, 코드 없는 주문 실패는 모름 (docs/infra.md 25.2287, 결함 루프).

① 조회(GET) 한 번의 시간 초과로 그날 아침 베끼기가 통째로 빠졌다(하루 한 번이라 저절로 풀리지 않는다).
② 주문 응답에 KIS 오류 코드가 없는데 거부로 적혀, 접수됐을 수 있는 주문이 다음 날 맞추기에서 빠졌다.
"""

from __future__ import annotations

from typing import Any

import pytest
import requests

from batch.sources import kis_vts as kv
from batch.sources.kis import KisFailed
from tests.test_kis_vts_1475 import _세션

잔고 = {"rt_cd": "0", "output1": [{"pdno": "005930", "hldg_qty": "3", "ord_psbl_qty": "3", "pchs_avg_pric": "9800"}],
       "output2": [{"dnca_tot_amt": "1000000", "tot_evlu_amt": "1029400"}]}  # fmt: skip


def _쉼을_적는_세션(monkeypatch: pytest.MonkeyPatch, replies: list) -> tuple[kv.Session, Any, list[float]]:
    s, http = _세션(monkeypatch, replies)
    쉼: list[float] = []
    s.sleep = 쉼.append  # type: ignore[method-assign]
    s._wait = lambda: None  # type: ignore[method-assign]
    return s, http, 쉼


def _끊기다가(http: Any, 실패: int, 방법: str = "get") -> list[int]:
    """앞 `실패` 번은 시간 초과, 그 뒤는 정해 둔 응답"""
    진짜, 부름 = getattr(http, 방법), [0]

    def 부르기(url: str, **kw: Any) -> Any:
        부름[0] += 1
        if 부름[0] <= 실패:
            raise requests.ReadTimeout(f"timed out: {url}?CANO=12345678")
        return 진짜(url, **kw)

    setattr(http, 방법, 부르기)
    return 부름


def test_조회는_시간_초과_한_번에_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(잔고, "")])
    부름 = _끊기다가(http, 1)
    보유, _합 = s.balance()
    assert 보유 == [kv.Holding("005930", 3, 3, 9800.0)]
    assert 부름[0] == 2 and 쉼 == [kv.GET_NET_WAITS[0]] and s.net_retries == 1


def test_조회는_두_번까지_다시_받고_끝내_안_되면_모름이다(monkeypatch: pytest.MonkeyPatch) -> None:
    assert kv.GET_NET_WAITS == (1.0, 3.0)
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(잔고, "")])
    부름 = _끊기다가(http, 2)
    assert s.balance()[0] and 부름[0] == 3 and 쉼 == [1.0, 3.0]
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(잔고, "")])
    부름 = _끊기다가(http, 3)
    with pytest.raises(kv.NoResponse) as e:
        s.balance()
    assert 부름[0] == 3 and 쉼 == [1.0, 3.0] and s.net_retries == 2
    # 주소(계좌번호)가 문장·원인·문맥 어디에도 없다
    assert "12345678" not in str(e.value) and e.value.__cause__ is None and e.value.__context__ is None
    assert str(e.value) == "모의 응답 없음 (ReadTimeout)"


def test_조회는_JSON_아닌_응답도_다시_받는다(monkeypatch: pytest.MonkeyPatch) -> None:
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(잔고, "")])
    진짜, 부름 = http.get, [0]

    def 게이트웨이(url: str, **kw: Any) -> Any:
        부름[0] += 1
        if 부름[0] == 1:
            return type("R", (), {"status_code": 502, "json": lambda _s: (_ for _ in ()).throw(ValueError()), "headers": {}})()
        return 진짜(url, **kw)

    http.get = 게이트웨이  # type: ignore[method-assign]
    assert s.balance()[0] and 부름[0] == 2 and 쉼 == [1.0]


def test_다시_받기는_끊긴_요청을_그대로_다시_보낸다(monkeypatch: pytest.MonkeyPatch) -> None:
    """25.2288 — 둘째 쪽에서 끊겼을 때 첫 쪽으로 돌아가거나 이어 받기 표지 없이 나가면 안 된다"""
    쪽1 = {**잔고, "ctx_area_fk100": "f", "ctx_area_nk100": "n"}
    쪽2 = {"rt_cd": "0", "output1": [{"pdno": "000660", "hldg_qty": "2", "ord_psbl_qty": "2", "pchs_avg_pric": "500"}],
          "output2": [{}]}  # fmt: skip
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(쪽1, "M"), (쪽2, "")])
    진짜, 본 = http.get, []

    def 둘째에서_한_번_끊김(url: str, **kw: Any) -> Any:
        본.append((kw["params"].get("CTX_AREA_NK100"), kw["headers"]["tr_cont"]))
        if len(본) == 2:
            raise requests.ReadTimeout("timed out")
        return 진짜(url, **kw)

    http.get = 둘째에서_한_번_끊김  # type: ignore[method-assign]
    보유, _합 = s.balance()
    assert [h.code for h in 보유] == ["005930", "000660"]
    assert 본 == [("", ""), ("n", "N"), ("n", "N")] and 쉼 == [1.0]


def test_코드_없는_모름_문장에_받은_것이_남는다() -> None:
    """25.2288 — 주문 줄의 message 로 가는 유일한 흔적. 값이 아니라 칸 이름만, msg1 은 앞 60자"""
    with pytest.raises(kv.NoResponse) as e:
        kv.parse_order({"rt_cd": "1", "msg_cd": "", "msg1": "모의투자 장시작전 입니다" + "가" * 80, "output": {"ODNO": "9"}})
    글 = str(e.value)
    assert "rt_cd '1'" in 글 and "모의투자 장시작전 입니다" in 글 and "칸(msg1,msg_cd,output,rt_cd)" in 글
    assert "가" * 61 not in 글 and "ODNO" not in 글 and "'9'" not in 글
    with pytest.raises(kv.NoResponse, match=r"rt_cd None · Gateway Timeout · 칸\(error,status\)"):
        kv.parse_us_reservation({"error": "Gateway Timeout", "status": 504})
    with pytest.raises(kv.NoResponse) as e:
        kv.parse_order(None)
    assert str(e.value) == "모의 주문 응답에 KIS 오류 코드가 없습니다"


def test_다시_받은_수가_실행_기록에_실린다() -> None:
    """25.2288 — 다시 받아 넘긴 날이 기록에 남아야 잦기를 잰다(rate_retries 와 같은 꼴: 있을 때만)"""
    from batch.jobs import intraday_guard as ig

    세션 = type("S", (), {"calls": 7, "rate_retries": 0, "net_retries": 2})()
    assert ig._호출(세션) == {"calls": 7, "net_retries": 2}
    세션.net_retries, 세션.rate_retries = 0, 1
    assert ig._호출(세션) == {"calls": 7, "rate_retries": 1}
    import inspect

    from batch.jobs import broker_mirror as jb

    # 아침 베끼기 — 꼬리를 한 곳에서 만들고 **모든 반환**이 그것을 쓴다(25.2290: 처음엔 마지막 반환에만 붙였다)
    세션.net_retries, 세션.rate_retries = 2, 0
    assert jb._호출(세션) == {"calls": 7, "net_retries": 2}
    세션.net_retries = 0
    assert jb._호출(세션) == {"calls": 7}
    세션.net_retries, 세션.rate_retries = 2, 3  # 초당 한도 다시 받기도 같은 꼴로(25.2291 — 장중 감시와 같은 칸)
    assert jb._호출(세션) == ig._호출(세션) == {"calls": 7, "rate_retries": 3, "net_retries": 2}
    글 = inspect.getsource(jb.run)
    assert 글.count("return 기록 | _호출(sess)") == 4 and '{"calls": sess.calls}' not in 글
    assert 글.count("return 기록") == 4  # 기록을 돌려주는 반환은 모두 꼬리를 단다


def test_모름으로_닫을_때_보낼_때_적은_글을_버리지_않는다() -> None:
    """25.2290 (25.2288 교차검증 [권고]) — 모름 문장에 실은 "받은 것" 이 다음 아침 맞추기의 "체결 조회에 없음" 으로
    덮여 하루 만에 사라졌다. 앞머리는 새 글 그대로(머리로 가르는 읽기는 그대로), 뒤에 보낼 때 글 120자"""
    import sqlite3

    from batch.jobs import broker_mirror as jb
    from batch.services import broker_mirror as bm

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE paper_broker_orders (id INTEGER PRIMARY KEY, status TEXT, broker_order_no TEXT, filled_qty INTEGER,"
              " avg_price REAL, message TEXT, fetched_at TEXT)")  # fmt: skip
    보낼때 = "모의 주문 응답에 KIS 오류 코드가 없습니다 — rt_cd '1' · 모의투자 장시작전 입니다 · 칸(msg1,rt_cd)"
    c.executemany("INSERT INTO paper_broker_orders (id, status, broker_order_no, message) VALUES (?, 'sending', ?, ?)",
                  [(1, None, 보낼때), (2, None, None), (3, None, ""), (4, "0001", 보낼때), (5, None, "가" * 300),
                   (6, None, 보낼때)])  # fmt: skip
    결과 = [bm.Outcome(1, "unknown", None, 0, None, bm.MISSING), bm.Outcome(2, "unknown", None, 0, None, bm.MISSING),
            bm.Outcome(3, "unknown", None, 0, None, bm.MISSING), bm.Outcome(4, "filled", "0009", 3, 100.0, None),
            bm.Outcome(5, "unknown", None, 0, None, bm.MISSING_US), bm.Outcome(6, "rejected", None, 0, None, "거부 수량")]  # fmt: skip
    for o in 결과:
        assert len(jb.recon_params(o, "t")) == jb.RECON_SQL.count("?") == 12
        c.execute(jb.RECON_SQL, jb.recon_params(o, "t"))
    행 = {r[0]: r[1:] for r in c.execute("SELECT id, status, message, broker_order_no, filled_qty FROM paper_broker_orders")}
    assert 행[1] == ("unknown", f"{bm.MISSING} · 보낼 때: {보낼때}", None, 0)
    assert 행[2] == ("unknown", bm.MISSING, None, 0) and 행[3] == ("unknown", bm.MISSING, None, 0)  # 앞 글이 없으면 새 글만
    assert 행[4] == ("filled", 보낼때, "0009", 3)  # 새 글이 없으면(체결과 맞음) 예전대로 그대로 둔다
    assert 행[5][1] == f"{bm.MISSING_US}{jb.RECON_KEPT}{'가' * jb.RECON_KEEP_CHARS}" and jb.RECON_KEEP_CHARS == 240
    assert 행[6] == ("rejected", "거부 수량", None, 0)  # 모름이 아니면 예전대로 새 글로 바꾼다
    assert f"'{jb.RECON_KEPT}'" in jb.RECON_SQL and f"substr(message, 1, {jb.RECON_KEEP_CHARS})" in jb.RECON_SQL
    # **두 번 적용돼도 같다**(25.2291) — DB 호출은 시간 초과·5xx 에서 같은 묶음을 다시 보낸다
    for o in 결과:
        c.execute(jb.RECON_SQL, jb.recon_params(o, "t"))
    둘째 = {r[0]: r[1:] for r in c.execute("SELECT id, status, message, broker_order_no, filled_qty FROM paper_broker_orders")}
    assert 둘째 == 행 and 둘째[1][1].count(jb.RECON_KEPT) == 1
    # 운영 꼴 — 장중 손절 줄(머리 + 가장 긴 모름 문장)도 칸 이름까지 남는다(120자였을 때는 잘렸다)
    try:
        kv.parse_order({"rt_cd": "1", "msg_cd": "", "msg1": "모의투자 주문이 불가한 시간입니다 다시 확인하시기 바랍니다 " * 3,
                        "output": {}, "error_code": "x", "ctx_area_fk100": "", "ctx_area_nk100": ""})  # fmt: skip
    except kv.NoResponse as exc:
        장중 = f"장중 현재가 123,400 ≤ 손절선 125,000 · {str(exc)[:200]}"
    c.execute("INSERT INTO paper_broker_orders (id, status, message) VALUES (7, 'sending', ?)", [장중])
    c.execute(jb.RECON_SQL, jb.recon_params(bm.Outcome(7, "unknown", None, 0, None, bm.MISSING), "t"))
    글 = c.execute("SELECT message FROM paper_broker_orders WHERE id = 7").fetchone()[0]
    assert 글 == f"{bm.MISSING}{jb.RECON_KEPT}{장중}" and "칸(" in 글 and "rt_cd '1'" in 글


def test_모름_문장의_길이를_묶는다() -> None:
    """25.2290 — 칸 이름은 여덟까지·이름마다 24자, rt_cd 는 앞 20자, 문구는 글일 때만(dict 면 안쪽 값이 따라온다)"""
    본문 = {f"k{n:02d}": 1 for n in range(12)} | {"rt_cd": "9" * 80, "msg_cd": "", "이름이_아주_긴_칸_" * 5: 1}
    with pytest.raises(kv.NoResponse) as e:
        kv.parse_order(본문)
    글 = str(e.value)
    assert "k08" not in 글 and "k07" in 글 and "9" * 21 not in 글  # 정렬한 앞 여덟(k00~k07)까지
    짧은본문 = {"rt_cd": "1", "msg_cd": "", "이름이_아주_긴_칸_" * 5: 1}
    with pytest.raises(kv.NoResponse) as e2:
        kv.parse_order(짧은본문)
    assert ("이름이_아주_긴_칸_" * 5)[:24] in str(e2.value) and ("이름이_아주_긴_칸_" * 5)[:25] not in str(e2.value)
    with pytest.raises(kv.NoResponse) as e:
        kv.parse_order({"error": {"account": "12345678"}, "error_description": "token expired " + "x" * 80})
    assert "12345678" not in str(e.value) and "token expired" in str(e.value) and "x" * 47 not in str(e.value)
    # 문구 후보가 글이 아니면(dict) 싣지 않는다 — 안쪽 값이 문장에 따라오지 않게
    with pytest.raises(kv.NoResponse) as e:
        kv.parse_order({"error": {"account": "12345678"}})
    assert "12345678" not in str(e.value) and str(e.value).endswith("칸(error)")


@pytest.mark.parametrize("꼴", ["끊김", "JSON 아님"])
def test_주문은_다시_보내지_않는다(monkeypatch: pytest.MonkeyPatch, 꼴: str) -> None:
    """접수됐는지 모르는 주문을 다시 보내면 두 번 나간다 — 한 번만 보내고 모름으로 둔다"""
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [])
    부름 = [0]

    def 보내기(url: str, **kw: Any) -> Any:
        부름[0] += 1
        if 꼴 == "끊김":
            raise requests.ConnectionError(f"reset: {url}")
        return type("R", (), {"status_code": 504, "json": lambda _s: (_ for _ in ()).throw(ValueError()), "headers": {}})()

    http.post = 보내기  # type: ignore[method-assign]
    with pytest.raises(kv.NoResponse):
        s.order("buy", "005930", 1, 9_800.0)
    assert 부름[0] == 1 and 쉼 == [] and s.net_retries == 0


def test_초당_한도_다시_받기와_따로_센다(monkeypatch: pytest.MonkeyPatch) -> None:
    한도 = {"rt_cd": "1", "msg_cd": kv.RATE_LIMIT_CODES[0], "msg1": "초당 거래건수를 초과"}
    s, http, 쉼 = _쉼을_적는_세션(monkeypatch, [(한도, ""), (잔고, "")])
    부름 = _끊기다가(http, 1)
    assert s.balance()[0] and 부름[0] == 3
    assert 쉼 == [kv.GET_NET_WAITS[0], kv.RATE_WAITS[0]] and (s.net_retries, s.rate_retries) == (1, 1)


@pytest.mark.parametrize("읽기", [kv.parse_order, kv.parse_us_reservation])
def test_코드_없는_주문_실패는_거부가_아니라_모름이다(읽기: Any) -> None:
    for 응답 in ({"error": "Internal Server Error", "status": 500}, {"error": "Gateway Timeout"}, {}, {"rt_cd": "1"},
               {"rt_cd": "1", "msg_cd": "", "msg1": ""}, {"rt_cd": "1", "msg_cd": "  ", "msg1": "모름"}, None, []):  # fmt: skip
        with pytest.raises(kv.NoResponse, match="KIS 오류 코드가 없습니다"):
            읽기(응답)
    # KIS 가 코드를 준 실패만 거부 — NoResponse 가 아닌 KisFailed
    with pytest.raises(KisFailed, match="APBK0919") as e:
        읽기({"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액을 초과 했습니다"})
    assert not isinstance(e.value, kv.NoResponse)


def test_주문_전송에서도_코드_없는_게이트웨이_JSON_은_모름이다(monkeypatch: pytest.MonkeyPatch) -> None:
    s, _http, _쉼 = _쉼을_적는_세션(monkeypatch, [({"error": "Gateway Timeout"}, "")])
    with pytest.raises(kv.NoResponse):
        s.order("buy", "005930", 1, 9_800.0)
    s, _http, _쉼 = _쉼을_적는_세션(monkeypatch, [({"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액 초과"}, "")])
    with pytest.raises(KisFailed) as e:
        s.order("buy", "005930", 1, 9_800.0)
    assert not isinstance(e.value, kv.NoResponse)


def test_조회_응답의_실패는_예전대로_거부로_읽는다() -> None:
    """`_ok` 는 그대로 — 조회에는 '접수됐는지 모름' 이 없다"""
    with pytest.raises(KisFailed) as e:
        kv.parse_executions({"error": "Gateway Timeout"})
    assert not isinstance(e.value, kv.NoResponse)
