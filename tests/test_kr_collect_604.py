"""국내 수집 감사 (docs/infra.md 25.604).

1. 08:15 KST 전의 빈 거래소 응답은 야후로 그날을 닫지 않고 `KrxNotYet` 으로 멈춘다
2. 주간 재무의 고유번호 요약은 경고가 아니다 — 받기 실패만 경고다
3. 한도가 아닌 DART 실패가 잇달면 멈춘다
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from batch.jobs import daily
from batch.jobs import financials as fin
from batch.sources import dart, krx, yfinance_src
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_yahoo_fallback import _client


class Test이른_실행:
    def test_경계(self) -> None:
        # 08:14 KST = 23:14 UTC 전날, 08:15 KST = 23:15 UTC
        assert daily._krx_아직_이른가(datetime(2026, 9, 24, 23, 14, tzinfo=UTC))
        assert not daily._krx_아직_이른가(datetime(2026, 9, 24, 23, 15, tzinfo=UTC))

    def test_이르면_야후로_대신하지_않고_멈춘다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _client()
        monkeypatch.setattr(krx, "fetch_daily", lambda m, d: FetchResult(ok=True, data=[], source="krx"))
        monkeypatch.setattr(daily, "_krx_아직_이른가", lambda now=None: True)
        불린: list[Any] = []
        monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: 불린.append(a) or FetchResult(ok=False))
        monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
        with pytest.raises(daily.KrxNotYet):
            daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]
        assert 불린 == []
        assert c.conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0

    def test_늦으면_예전처럼_야후로_받는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _client()
        monkeypatch.setattr(krx, "fetch_daily", lambda m, d: FetchResult(ok=True, data=[], source="krx"))
        monkeypatch.setattr(daily, "_krx_아직_이른가", lambda now=None: False)
        불린: list[Any] = []
        monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: 불린.append(a) or FetchResult(ok=False))
        monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
        with pytest.raises(RuntimeError, match="한 건도 저장하지 못했습니다"):
            daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]
        assert len(불린) == 2  # 시장마다 폴백을 시도했다

    def test_실패_응답은_이른_시각이어도_폴백한다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """이른 실행 판정은 **빈 성공** 에만 쓴다. HTTP 오류까지 삼키면 진짜 장애가 건너뜀으로 숨는다."""
        c = _client()
        monkeypatch.setattr(krx, "fetch_daily", lambda m, d: FetchResult(ok=False, error="HTTP 500", source="krx"))
        monkeypatch.setattr(daily, "_krx_아직_이른가", lambda now=None: True)
        monkeypatch.setattr(yfinance_src, "fetch_daily_bars", lambda *a, **k: FetchResult(ok=False))
        monkeypatch.setattr(daily, "_collect_index_prices", lambda client: [])
        with pytest.raises(RuntimeError, match="한 건도 저장하지 못했습니다"):
            daily.collect_kr_prices(c, "2026-09-25")  # type: ignore[arg-type]


class Test고유번호_요약:
    def test_성공은_경고가_아니다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        c = _client()
        corp = SimpleNamespace(corp_code="00126380", stock_code="005930")
        monkeypatch.setattr(dart, "fetch_corp_codes", lambda: SimpleNamespace(ok=True, data=[corp], error=None))
        monkeypatch.setattr(fin.db, "record_api_call", lambda *a, **k: {"state": "ok"})
        linked, w = fin.sync_corp_codes(c)  # type: ignore[arg-type]
        assert (linked, w) == (1, [])

    def test_받기_실패는_경고다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(dart, "fetch_corp_codes", lambda: SimpleNamespace(ok=False, data=[], error="키 오류"))
        monkeypatch.setattr(fin.db, "record_api_call", lambda *a, **k: {"state": "ok"})
        _linked, w = fin.sync_corp_codes(_client())  # type: ignore[arg-type]
        assert w == ["고유번호 받기 실패: 키 오류"]


class Test잇단_실패:
    @staticmethod
    def _준비(monkeypatch: pytest.MonkeyPatch, 결과들: list[Any]) -> list[list[str]]:
        보낸것: list[list[str]] = []
        반복 = iter(결과들)

        def 가짜받기(chunk: list[str], year: int, report: str) -> Any:
            보낸것.append(list(chunk))
            return next(반복)

        monkeypatch.setattr(dart, "fetch_multi_financials", 가짜받기)
        monkeypatch.setattr(dart, "MAX_CORPS_PER_CALL", 1)
        monkeypatch.setattr(fin.db, "record_api_call", lambda *a, **k: {"state": "ok"})
        monkeypatch.setattr(fin, "_한도_찼나", lambda client: False)
        monkeypatch.setattr(fin, "_store", lambda _c, data, *_a: len(data))
        monkeypatch.setattr(fin, "CALL_INTERVAL_SECONDS", 0)
        return 보낸것

    @staticmethod
    def _실패() -> Any:
        return type("R", (), {"ok": False, "data": [], "source": "dart", "limit_state": "ok",
                              "error": "사용할 수 없는 키입니다"})()  # fmt: skip

    @staticmethod
    def _성공() -> Any:
        return type("R", (), {"ok": True, "data": [1], "source": "dart", "limit_state": "ok", "error": None})()

    def test_잇달면_멈추고_바깥_루프도_끊는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        n = fin.MAX_CONSECUTIVE_FAILURES
        보낸것 = self._준비(monkeypatch, [self._실패() for _ in range(n + 5)])
        corps = [(f"c{i}", i) for i in range(1, n + 6)]
        _stored, w = fin.collect_period(None, corps, 2025, "11011")  # type: ignore[arg-type]
        assert len(보낸것) == n
        assert any(x.startswith(fin.FAILURES_STOPPED) for x in w)
        assert fin.LIMIT_REACHED not in w, "한도가 아니다 — 틀린 원인을 적지 않는다 (25.605)"

    def test_종목이_하나여도_기간을_건너_이어_센다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """참고 분석(유니버스 밖 종목 하나)은 기간마다 호출이 한 번이다 — 기간마다 0 에서 세면 23개 기간을 다
        불렀다(10-08 국내 배치, 25.1083). 같은 `Streak` 를 넘기면 5번째 기간에서 멈춘다."""
        n = fin.MAX_CONSECUTIVE_FAILURES
        보낸것 = self._준비(monkeypatch, [self._실패() for _ in range(n + 10)])
        잇단 = fin.Streak()
        멈춤 = None
        for 기간 in range(n + 10):
            _s, w = fin.collect_period(None, [("c1", 1)], 2025 - 기간, "11011", streak=잇단)  # type: ignore[arg-type]
            if any(x.startswith(fin.FAILURES_STOPPED) for x in w):
                멈춤 = 기간
                break
        assert 멈춤 == n - 1 and len(보낸것) == n
        import inspect

        assert "streak=잇단" in inspect.getsource(fin.run)  # 실제 실행이 같은 셈을 넘긴다

    def test_사이에_성공이_있으면_다시_센다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        n = fin.MAX_CONSECUTIVE_FAILURES
        결과 = [self._실패() for _ in range(n - 1)] + [self._성공()] + [self._실패() for _ in range(n - 1)]
        보낸것 = self._준비(monkeypatch, 결과)
        corps = [(f"c{i}", i) for i in range(1, len(결과) + 1)]
        stored, w = fin.collect_period(None, corps, 2025, "11011")  # type: ignore[arg-type]
        assert len(보낸것) == len(결과) and stored == 1
        assert not any(x.startswith(fin.FAILURES_STOPPED) for x in w)
