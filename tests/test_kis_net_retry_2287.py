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

    assert '({"net_retries": 끊김} if 끊김 else {})' in inspect.getsource(jb.run)


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
