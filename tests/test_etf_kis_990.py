"""KODEX 홈페이지에서 못 받은 국내 ETF 를 KIS 구성종목 상위 30 으로 (docs/infra.md 25.990)."""

from __future__ import annotations

from batch.jobs import etf_tilt as job
from batch.sources import kis


class _QC:
    calls = 0

    def __init__(self, token: str) -> None:
        pass

    def etf_components(self, code: str):
        _QC.calls += 1
        if code == "229200":
            raise kis.KisFailed("KIS 응답 실패 EGW00201")
        return [] if code == "360750" else [("005930", 34.63), ("000660", 26.9)]


def test_못_받은_것만_KIS_로_받고_실패에서_지운다(monkeypatch) -> None:
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    monkeypatch.setattr(kis, "access_token", lambda c: ("t", False))
    monkeypatch.setattr(kis, "QuoteClient", _QC)
    _QC.calls = 0
    out: dict = {}
    failed = {"069500": "HTTP 429", "229200": "HTTP 429", "360750": "HTTP 429"}
    calls = job.fetch_kr_kis(object(), ["069500", "229200", "360750"], out, failed)
    assert list(out) == ["069500"] and out["069500"].source == "kis_openapi"
    assert out["069500"].holdings == [("005930", 34.63), ("000660", 26.9)]
    assert set(failed) == {"229200", "360750"} and "KIS 도 실패" in failed["229200"] and "비어" in failed["360750"]
    assert calls == _QC.calls == 3


def test_키가_없으면_아무것도_안_한다(monkeypatch) -> None:
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    failed = {"069500": "HTTP 429"}
    assert job.fetch_kr_kis(object(), ["069500"], {}, failed) == 0
    assert failed == {"069500": "HTTP 429"}
