"""42회차 u3 생존편향 ⓪ 자료 탐침 (docs/factors.md 12.45 "u3 생존편향 사전 등록") — 오프라인, 가짜 KRX·DART·카운터.

묶는 것: 호출 상한 KRX·DART 각 ≤ 100(재시도까지) · 카운터는 시작 때 보고 끝에 적는다(api_usage 만, 시세·stocks·batch_runs 0) ·
출력에 종목코드·이름·가격·금액이 없다(꼴만) · 전략·점수 경로가 탐침 모듈을 import 하지 않는다 · 워크플로는 workflow_dispatch 만.
판정 함수는 손 계산으로 본다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from batch.sources import dart
from batch.sources.krx import KrxDailyRow
from batch.sources.yfinance_src import FetchResult

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("probe_survivorship_0", ROOT / "scripts" / "probe_survivorship_0.py")
assert _spec and _spec.loader
probe = importlib.util.module_from_spec(_spec)
sys.modules["probe_survivorship_0"] = probe  # dataclass 가 제 모듈을 sys.modules 에서 찾는다
_spec.loader.exec_module(probe)

#: 판정 창(3~7일)·일일 배치 시각을 피한 때 — 2026-11-09 05:00 UTC = 14:00 KST
조용한때 = datetime(2026, 11, 9, 5, 0, tzinfo=UTC)
닻 = date(2026, 11, 6)


def 평일(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def 행(code: str, name: str, close: float, shares: int, *, fluc: float = 0.5, change: float = 10.0,
      value: int = 5_000_000, open_: float | None = None) -> dict[str, str]:  # fmt: skip
    return {
        "BAS_DD": "", "ISU_CD": code, "ISU_NM": name, "MKT_NM": "", "TDD_CLSPRC": str(close),
        "CMPPREVDD_PRC": str(change), "FLUC_RT": str(fluc), "TDD_OPNPRC": str(close if open_ is None else open_),
        "TDD_HGPRC": str(close), "TDD_LWPRC": str(close), "ACC_TRDVOL": str(1000 if value else 0),
        "ACC_TRDVAL": str(value), "MKTCAP": str(int(close * shares)), "LIST_SHRS": str(shares),
    }  # fmt: skip


#: 가짜 세상 — 실제 종목이 아니다(9·8 로 시작하는 지은 코드, "가상" 이름)
가짜코드 = ("900010", "900020", "900025", "900030", "900070", "900080", "800010", "800020", "800030")
가짜이름 = ("가상하나", "가상둘", "가상넷우", "가상셋", "가상일곱우B", "가상여덟", "가상코하나", "가상이전", "가상코셋")
가짜값 = ("7777", "50000", "10500", "7777000", "250000000")

분할날 = date(2024, 6, 3)
셋_끝 = date(2025, 2, 14)
셋_정리 = date(2025, 2, 6)
셋_정지 = date(2025, 1, 20)


def 코스피(d: date) -> list[dict[str, str]]:
    rows = [행("900010", "가상하나", 7777, 1000)]
    # 분할 ×5 — 정지 5일(행은 있고 거래대금 0) 뒤 새 주식수 첫날 기준가 = 전날 종가 ÷ 5
    if d < 분할날 - timedelta(days=7):
        rows.append(행("900020", "가상둘", 50000, 1000))
    elif d < 분할날:
        rows.append(행("900020", "가상둘", 50000, 1000, fluc=0, change=0, value=0, open_=0))
    elif d == 분할날:
        rows.append(행("900020", "가상둘", 10500, 5000, fluc=5.0, change=500))
    else:
        rows.append(행("900020", "가상둘", 10500, 5000, fluc=0, change=0))
    rows.append(행("900025", "가상넷우", 3000, 200))
    rows.append(행("900070", "가상일곱우B", 2000, 300))
    if d != date(2025, 2, 10):  # 하루 행 없음(정지 중 '행 없음' 꼴)
        rows.append(행("900080", "가상여덟", 1500, 400))
    # 셋: 정지(거래대금 0) → 정리매매 7일(|FLUC_RT| 50, 첫날만 기준가 재설정으로 60) → 사라짐
    if d < 셋_정지:
        rows.append(행("900030", "가상셋", 4000, 1000))
    elif d < 셋_정리:
        rows.append(행("900030", "가상셋", 4000, 1000, fluc=0, change=0, value=0, open_=0))
    elif d <= 셋_끝:
        k = len(평일(셋_정리, d))
        rows.append(행("900030", "가상셋", 4000 * 0.5**k, 1000, fluc=-60.0 if k == 1 else -50.0, change=-1.0))
    if d >= date(2025, 1, 2):
        rows.append(행("800020", "가상이전", 900, 100))
    return rows


def 코스닥(d: date) -> list[dict[str, str]]:
    rows = [행("800010", "가상코하나", 1234, 500)]
    if d < date(2025, 1, 2):
        rows.append(행("800020", "가상이전", 900, 100))
    if d <= date(2023, 5, 10):
        rows.append(행("800030", "가상코셋", 700, 250))
    return rows


def 마스터(market: str) -> list[dict[str, str]]:
    src = 코스피(닻) if market == "KOSPI" else 코스닥(닻)
    return [{"ISU_SRT_CD": r["ISU_CD"], "ISU_CD": "KR7" + r["ISU_CD"] + "000", "ISU_ABBRV": r["ISU_NM"],
             "KIND_STKCERT_TP_NM": "우선주" if r["ISU_NM"].endswith(("우", "우B")) else "보통주",
             "SECT_TP_NM": "우량기업부", "LIST_DD": "20000101", "LIST_SHRS": r["LIST_SHRS"]} for r in src]  # fmt: skip


class 가짜KRX:
    def __init__(self, attempts: int = 1, ok: bool = True) -> None:
        self.부름: list[tuple[str, str]] = []
        self.attempts, self.ok = attempts, ok

    def __call__(self, api_name: str, bas_dd: str) -> FetchResult:
        self.부름.append((api_name, bas_dd))
        if not self.ok:
            return FetchResult(ok=False, error="서버 오류", attempts=self.attempts)
        d = datetime.strptime(bas_dd, "%Y%m%d").date()
        data = {"kospi_daily": lambda: 코스피(d), "kosdaq_daily": lambda: 코스닥(d),
                "kospi_master": lambda: 마스터("KOSPI"), "kosdaq_master": lambda: 마스터("KOSDAQ")}[api_name]()  # fmt: skip
        return FetchResult(ok=True, data=data, attempts=self.attempts)


class 가짜HTTP:
    def __init__(self) -> None:
        self.부름: list[tuple[str, dict]] = []

    def __call__(self, url: str, params: dict, timeout: int) -> Any:
        self.부름.append((url, params))
        if url.endswith("company.json"):
            body = {"status": "000", "corp_code": params["corp_code"], "corp_name": "가상셋", "stock_code": "900030",
                    "corp_cls": "E", "est_dt": "19990101", "induty_code": "264"}  # fmt: skip
        else:
            body = {"status": "000", "list": [{"report_nm": "상장폐지결정"}, {"report_nm": "상장폐지사유발생"}]}

        class R:
            def json(self) -> dict:
                return body

        return R()


def 고유번호() -> FetchResult:
    return FetchResult(ok=True, data=[dart.CorpCode("99999901", "가상셋", "900030"),
                                      dart.CorpCode("99999902", "가상코셋", "800030")])  # fmt: skip


class RS:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.rows = [tuple(r.values()) for r in rows]

    def dicts(self) -> list[dict]:
        return self._rows


class 가짜카운터:
    """api_usage 만 아는 가짜 Turso — 다른 표에 닿으면 기록에 남는다."""

    def __init__(self, state: dict[str, str] | None = None, count: dict[str, int] | None = None) -> None:
        self.sql: list[str] = []
        self.state = dict(state or {})
        self.count = dict(count or {})
        self.limit: dict[str, int] = {}
        self.적음: list[tuple[str, int]] = []
        self.closed = False

    def execute(self, sql: str, params: list | tuple = ()) -> RS:
        self.sql.append(sql)
        name = params[0] if params else ""
        if sql.startswith("SELECT state"):
            return RS([{"state": self.state[name]}] if name in self.state else [])
        if sql.startswith("SELECT call_count FROM"):
            return RS([{"call_count": self.count[name]}] if name in self.count else [])
        if sql.startswith("INSERT INTO api_usage"):
            self.적음.append((name, params[2]))
            self.count[name] = self.count.get(name, 0) + params[2]
            self.limit[name] = params[3]
            return RS([])
        if sql.startswith("SELECT call_count, limit_value"):
            return RS([{"call_count": self.count[name], "limit_value": self.limit.get(name), "warn_at_pct": 80}])
        if sql.startswith("UPDATE api_usage"):
            self.state[params[1]] = params[0]
            return RS([])
        raise AssertionError(f"예상 밖 SQL: {sql}")

    def close(self) -> None:
        self.closed = True


def deps(krx_fetch: Any = None, client: Any = None, http: Any = None, now: datetime = 조용한때, **kw: Any) -> Any:
    return probe.Deps(
        krx_fetch=krx_fetch or 가짜KRX(), dart_corp_codes=kw.pop("corp", 고유번호), http_get=http or 가짜HTTP(),
        client_factory=lambda: client if client is not None else 가짜카운터(), sessions=평일, anchor=lambda: 닻,
        now=lambda: now, us_open=lambda d: None, clock=iter(range(10_000)).__next__, sleep=lambda s: None,
        dart_key=lambda: "k", **kw,
    )  # fmt: skip


@pytest.fixture
def 한번(capsys: pytest.CaptureFixture[str]) -> tuple[int, str, 가짜KRX, 가짜HTTP, 가짜카운터]:
    k, h, c = 가짜KRX(), 가짜HTTP(), 가짜카운터()
    rc = probe.main([], deps(k, c, h))
    return rc, capsys.readouterr().out, k, h, c


# ---------------------------------------------------------------------------------------------------------------------
# 호출 상한
# ---------------------------------------------------------------------------------------------------------------------
def test_KRX_상한은_재시도까지_세어_100_을_넘지_않는다() -> None:
    k = 가짜KRX(attempts=3)
    b = probe.Budget("KRX", probe.KRX_MAX_CALLS, per_call=3)
    r = probe.KrxReader(b, k, clock=lambda: 0.0, sleep=lambda s: None)
    for i in range(200):
        r.day("KOSPI", date(2021, 9, 1) + timedelta(days=i))
    assert probe.KRX_MAX_CALLS == 100
    assert b.sent == 99 and len(k.부름) == 33 and b.sent <= 100 and b.refused == 200 - 33


def test_KRX_한_번씩_나가면_상한_바로_앞까지() -> None:
    k = 가짜KRX(attempts=1)
    b = probe.Budget("KRX", 100, per_call=3)
    r = probe.KrxReader(b, k, clock=lambda: 0.0, sleep=lambda s: None)
    for i in range(200):
        r.day("KOSDAQ", date(2021, 9, 1) + timedelta(days=i))
    assert len(k.부름) == b.sent == 98  # 다음 한 번이 최악 3번 나가면 100 을 넘는다


def test_DART_상한_100() -> None:
    h = 가짜HTTP()
    b = probe.Budget("DART", probe.DART_MAX_CALLS)
    r = probe.DartReader(b, "k", h, 고유번호, clock=lambda: 0.0)
    assert r.corp_codes() is not None
    for _ in range(200):
        r.company("99999901")
    assert probe.DART_MAX_CALLS == 100 and b.sent == 100 and len(h.부름) == 99 and r.company("x") is None


def test_한도_거절이면_더_부르지_않는다() -> None:
    k = 가짜KRX()
    k.ok = False
    b = probe.Budget("KRX", 100, per_call=3)
    r = probe.KrxReader(b, lambda a, d: FetchResult(ok=False, limit_state="blocked", attempts=1),
                        clock=lambda: 0.0, sleep=lambda s: None)  # fmt: skip
    assert r.day("KOSPI", 닻) is None and b.stopped
    assert r.day("KOSPI", 닻 - timedelta(days=1)) is None and b.sent == 1


def test_전체_실행이_상한_안에서_끝난다(한번) -> None:  # noqa: ANN001
    rc, out, k, h, c = 한번
    assert rc == 0
    assert 0 < len(k.부름) <= 100 and 0 < len(h.부름) + 1 <= 100
    assert "KRX: 보낸 요청" in out and "DART: 보낸 요청" in out


def test_상한이_작아도_모름으로_끝난다(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(probe, "KRX_MAX_CALLS", 30)
    k = 가짜KRX()
    assert probe.main([], deps(k)) == 0
    out = capsys.readouterr().out
    assert len(k.부름) <= 30 and "KRX: 보낸 요청" in out


# ---------------------------------------------------------------------------------------------------------------------
# 카운터
# ---------------------------------------------------------------------------------------------------------------------
def test_카운터는_시작에_보고_끝에_보낸_수를_적는다(한번) -> None:  # noqa: ANN001
    rc, out, k, h, c = 한번
    assert c.sql[0].startswith("SELECT state FROM api_usage")  # usage_blocked_today 가 맨 먼저
    적음 = dict(c.적음)
    assert 적음["krx_openapi"] == len(k.부름) and 적음["dart_opendart"] == len(h.부름) + 1  # +1 = corpCode
    assert c.limit == {"krx_openapi": 10_000, "dart_opendart": 20_000} and c.closed


def test_Turso_는_api_usage_카운터만(한번) -> None:  # noqa: ANN001
    _, _, _, _, c = 한번
    for sql in c.sql:
        assert "api_usage" in sql
        assert not re.search(r"\b(prices|stocks|batch_runs)\b", sql), sql


def test_카운터가_막혔으면_부르지_않는다(capsys: pytest.CaptureFixture[str]) -> None:
    k, h = 가짜KRX(), 가짜HTTP()
    c = 가짜카운터(state={"krx_openapi": "blocked", "dart_opendart": "blocked"})
    assert probe.main([], deps(k, c, h)) == 0
    out = capsys.readouterr().out
    assert k.부름 == [] and h.부름 == [] and c.적음 == []
    assert "오늘 카운터 100%" in out


def test_상한을_더해_100_에_닿으면_멈추고_80_이면_경고만(capsys: pytest.CaptureFixture[str]) -> None:
    k, h = 가짜KRX(), 가짜HTTP()
    c = 가짜카운터(count={"krx_openapi": 9_950, "dart_opendart": 16_000})
    probe.main([], deps(k, c, h))
    out = capsys.readouterr().out
    assert k.부름 == [] and "KRX: 부르지 않는다" in out and "DART: 경고" in out


def test_카운터를_못_열면_부르지_않는다(capsys: pytest.CaptureFixture[str]) -> None:
    k = 가짜KRX()

    def 깨짐() -> None:
        raise RuntimeError("no db")

    d = deps(k)
    d.client_factory = 깨짐
    assert probe.main([], d) == 2 and k.부름 == []


@pytest.mark.parametrize("now", [datetime(2026, 11, 5, 5, tzinfo=UTC), datetime(2026, 11, 9, 23, 40, tzinfo=UTC)])
def test_판정_창과_국내_배치_시각엔_부르지_않는다(now: datetime, capsys: pytest.CaptureFixture[str]) -> None:
    # 11-05 는 판정 창, 11-09 23:40 UTC = 11-10 08:40 KST 는 국내 일일 배치 시각
    k = 가짜KRX()
    assert probe.main([], deps(k, now=now)) == 2 and k.부름 == []


def test_미국_배치_시각엔_부르지_않는다() -> None:
    열림 = datetime(2026, 11, 9, 14, 30, tzinfo=UTC)
    assert probe.window_refusal(열림 - timedelta(minutes=30), lambda d: 열림)
    assert probe.window_refusal(열림 + timedelta(hours=1), lambda d: 열림) is None


# ---------------------------------------------------------------------------------------------------------------------
# 출력 — 꼴만
# ---------------------------------------------------------------------------------------------------------------------
def test_출력엔_종목코드_이름_가격_금액이_없다(한번) -> None:  # noqa: ANN001
    _, out, _, _, _ = 한번
    for s in 가짜코드 + 가짜이름 + 가짜값 + ("99999901", "99999902", "19990101"):
        assert s not in out, s
    assert not re.search(r"(?<!\d)\d{6}(?!\d)", out), "6자리 숫자(종목코드 꼴)가 찍혔다"


def test_12_45_의_물음마다_답한다(한번) -> None:  # noqa: ANN001
    _, out, _, _, _ = 한번
    for 머리 in ("== A.", "== B.", "== C.", "== D.", "== E.", "== F.", "== G.", "== H.", "== 호출·카운터"):
        assert 머리 in out
    assert "판정: 예 — 과거 응답에 지금 없는 종목 행이 든다" in out
    assert "KOSDAQ: 지난 표본 종목 3 · 닻에 없음 1 (33.3%) · 이전상장 1" in out
    assert "판정(MKTCAP = 그날 실제 시총 전제): 예" in out
    assert "판정(LIST_SHRS 가 그날 값): 예" in out
    assert "권리락 기준가 1" in out
    # 정리매매 창: 정지 3일(거래대금 0) + 정리매매 7일(|FLUC_RT|>30), 첫날만 원 종가 비율과 1%p 넘게 갈림
    assert "KOSPI: 창 10거래일 · 행 있음 10 · 행 없음 0 · |FLUC_RT|>30% 7일 · 거래대금 0 3일" in out
    assert "갈림 1일" in out
    assert "(날, 종목) 1" in out  # 900080 의 하루 행 없음
    # 정지 행 모양: 정리매매 창의 정지 3일 — 행은 있고 거래대금·거래량·시가 0, 종가는 남는다
    assert "거래대금 0 3 (" in out and "그 가운데 시가 0 3 · 그 가운데 종가 있음 3 · 거래량 0 3" in out
    assert "SECT_TP_NM 없음" in out and "SECT_TP_NM 있음" in out  # 일별엔 없고 마스터엔 있다
    assert "KOSPI 우선주(닻): 둘 다 1 · 코드만 0 · 이름만 1" in out
    assert "종목코드로 찾음 2 (100.0%)" in out and "'상장폐지' 제목이 있는 회사 2/2" in out
    assert "상장일 모름" in out and "KRX 상장폐지 현황 API 모름" in out
    assert "① 어림" in out


# ---------------------------------------------------------------------------------------------------------------------
# 판정 함수 — 손 계산
# ---------------------------------------------------------------------------------------------------------------------
def r(**kw: Any) -> KrxDailyRow:
    base = dict(bas_dd="", isu_cd="000000", isu_nm="", mkt_nm="", close=None, change=None, change_pct=None, open=None,
                high=None, low=None, volume=None, value=None, market_cap=None, listed_shares=None)  # fmt: skip
    base.update(kw)
    return KrxDailyRow(**base)


def test_우선주_규칙_둘() -> None:
    assert probe.preferred_by_code("123455") and not probe.preferred_by_code("123450")
    assert probe.preferred_by_name("가나우") and probe.preferred_by_name("가나우B ") and not probe.preferred_by_name("우리")


def test_시총_짝은_0_1퍼센트_안() -> None:
    assert probe.mktcap_match(r(close=100, listed_shares=1000, market_cap=100_100))
    assert probe.mktcap_match(r(close=100, listed_shares=1000, market_cap=100_200)) is False
    assert probe.mktcap_match(r(close=None, listed_shares=1000, market_cap=1)) is None


def test_허용_오차가_probe_krx_listed_shares_와_같다() -> None:
    """12.45 — 한 번도 돌지 않은 `probe_krx_listed_shares.py` 의 두 질문을 이 탐침에 묶었다. 잣대가 갈리면 깨진다."""
    src = (ROOT / "scripts" / "probe_krx_listed_shares.py").read_text(encoding="utf-8")
    assert "<= 0.001 * row.market_cap" in src and probe.MKTCAP_TOL == 0.001


def test_분할_배수() -> None:
    assert probe.split_ratio(r(listed_shares=1000, close=50000), r(listed_shares=5000, close=10500)) == 5
    assert probe.split_ratio(r(listed_shares=1000, close=50000), r(listed_shares=1100, close=45000)) is None  # 증자
    assert probe.split_ratio(r(listed_shares=1000, close=50000), r(listed_shares=5000, close=50000)) is None  # 값 그대로


def test_기준가_판정() -> None:
    앞 = r(close=50000)
    assert probe.classify_base(앞, r(close=10500, change=500), 5) == "권리락 기준가"  # 10500 − 500 = 50000 ÷ 5
    assert probe.classify_base(앞, r(close=10500, change=-39500), 5) == "원 종가"
    assert probe.classify_base(앞, r(close=10500, change=3000), 5) == "그 밖"
    assert probe.classify_base(None, r(close=10500, change=500), 5) == "모름"


def test_정리매매_창_꼴() -> None:
    c = probe.cleanup_shape([r(close=100, value=0, change_pct=0), None, r(close=40, change_pct=-60, value=1),
                             r(close=20, change_pct=-50, value=1)])  # fmt: skip
    # 둘째 날 없음 → 셋째 날은 앞 행(100)과 비교: 원 −60% = FLUC −60 → 안 갈림. 넷째: 원 −50 = −50
    assert c == {"거래일": 4, "행있음": 3, "행없음": 1, "큰등락": 2, "거래대금0": 1}
    assert probe.cleanup_shape([r(close=100, change_pct=0, value=1), r(close=50, change_pct=-60, value=1)])[
        "FLUC_원종가_갈림"] == 1  # fmt: skip


def test_이전상장은_사라진_것으로_세지_않는다() -> None:
    S = probe.Snapshot
    a = {"KOSPI": [S("KOSPI", date(2024, 1, 1), {"1": r(), "2": r()}, frozenset())],
         "KOSDAQ": [S("KOSDAQ", date(2024, 1, 1), {"3": r()}, frozenset())]}  # fmt: skip
    닻들 = {"KOSPI": S("KOSPI", 닻, {"1": r(), "3": r()}, frozenset()), "KOSDAQ": S("KOSDAQ", 닻, {}, frozenset())}
    셈, 사라짐 = probe.presence(a, 닻들)
    assert 사라짐 == {("KOSPI", "2"): date(2024, 1, 1)}
    assert 셈["KOSDAQ"] == {"지난표본종목": 1, "사라짐": 0, "이전상장": 1}


def test_이분_탐색() -> None:
    days = 평일(date(2025, 1, 1), date(2025, 3, 31))
    끝 = date(2025, 2, 14)
    봄: list[date] = []
    i = probe.bisect_edge(days, lambda d: 봄.append(d) or d <= 끝)
    assert days[i] == 끝 and len(봄) <= 7
    assert probe.bisect_edge(days, lambda d: None) is None


def test_표본_기준일_규칙() -> None:
    ds = probe.sample_dates(닻, 평일)
    assert ds[0] == date(2021, 9, 30) and ds[-1] == 닻 and len(ds) == 12
    assert all(d.month in (3, 9) for d in ds[:-1]) and ds == sorted(set(ds))


# ---------------------------------------------------------------------------------------------------------------------
# 경로·워크플로
# ---------------------------------------------------------------------------------------------------------------------
def test_전략_점수_경로가_탐침을_import_하지_않는다() -> None:
    """12.45 "탐침 모듈을 전략·점수 경로가 import 하지 않는다"."""
    걸림 = [str(p.relative_to(ROOT)) for p in (ROOT / "batch").rglob("*.py")
            if re.search(r"probe_survivorship", p.read_text(encoding="utf-8"))]  # fmt: skip
    assert 걸림 == []
    assert list((ROOT / "batch" / "services").rglob("*.py")) and list((ROOT / "batch" / "jobs").rglob("*.py"))


def test_워크플로는_손으로만_돌고_결과는_비공개로() -> None:
    yaml = pytest.importorskip("yaml")
    src = (ROOT / ".github" / "workflows" / "probe-survivorship.yml").read_text(encoding="utf-8")
    data = yaml.safe_load(src)
    on = data.get("on", data.get(True))
    assert (list(on) if isinstance(on, dict) else [on]) == ["workflow_dispatch"]
    job = next(iter(data["jobs"].values()))
    assert 0 < int(job["timeout-minutes"]) <= 30
    assert "probe_survivorship_0.py" in src and "2>&1 | python scripts/ops_tee.py" in src
    적는, 올리는 = re.search(r"ops_tee\.py (\S+)", src), re.search(r"--file (\S+)", src)
    assert 적는 and 올리는 and 적는.group(1) == 올리는.group(1)
    올림 = [s for s in job["steps"] if "publish_output.py" in str(s.get("run", ""))]
    assert len(올림) == 1 and "always()" in str(올림[0].get("if", ""))
    assert data["permissions"]["issues"] == "write"


def test_main_의_KRX_상한도_재시도를_센다(capsys: pytest.CaptureFixture[str]) -> None:
    """main 이 Budget 에 per_call = krx.MAX_RETRY 를 잇는지(25.2201 교차검증) — 늘 3번씩 나가도 100 을 넘지 않는다"""
    k = 가짜KRX(attempts=3)
    probe.main([], deps(k))
    out = capsys.readouterr().out
    n = sum(3 for _ in k.부름)
    assert n <= probe.KRX_MAX_CALLS and len(k.부름) > 0, (n, out[-400:])


def test_DART_한도_거절_020_이면_멈춘다(capsys: pytest.CaptureFixture[str]) -> None:
    class 막힘(가짜HTTP):
        def __call__(self, url: str, params: dict, timeout: int) -> Any:
            self.부름.append((url, params))

            class R:
                def json(self) -> dict:
                    return {"status": "020", "message": "한도"}

            return R()

    h = 막힘()
    probe.main([], deps(http=h))
    out = capsys.readouterr().out
    assert len(h.부름) == 1 and "DART 020" in out


def test_UTC_로_7일이면_KST_8일_새벽이어도_판정_창() -> None:
    now = datetime(2026, 11, 7, 20, tzinfo=UTC)  # KST 11-08 05:00
    assert probe.window_refusal(now, us_open=lambda d: None) == "판정 실행 창(그 달 3~7일)"


def test_카운터_읽기가_깨지면_부르지_않는다(capsys: pytest.CaptureFixture[str]) -> None:
    """한도를 모르는 채 부르지 않는다(25.2207 — 전엔 읽기 예외를 삼키고 그대로 불렀다)"""

    class 읽기깨짐(가짜카운터):
        def execute(self, sql: str, params: list | tuple = ()) -> RS:
            if sql.startswith("SELECT call_count FROM"):
                raise RuntimeError("읽기 실패")
            return super().execute(sql, params)

    k, h = 가짜KRX(), 가짜HTTP()
    probe.main([], deps(k, 읽기깨짐(), h))
    assert k.부름 == [] and h.부름 == []
