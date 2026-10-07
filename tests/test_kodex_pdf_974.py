"""KODEX 구성종목 어댑터 (docs/data-sources.md 13.6, docs/infra.md 25.974) — 응답 모양은 2026-10-06 실측."""

from __future__ import annotations

from datetime import date

import pytest

from batch.sources import kodex_pdf as kp

PDF = {"pdf": {"gijunYMD": "20261002", "totalCnt": 3, "list": [
    {"itmNo": "KRD010010001", "secNm": "원화예금", "evalA": "2594753", "ratio": None},
    {"itmNo": "005930", "secNm": "삼성전자", "evalA": "1923444000", "ratio": "34.46"},
    {"itmNo": "000660", "secNm": "SK하이닉스", "evalA": "1556217000", "ratio": "27.88"},
]}}  # fmt: skip


class Test읽기:
    def test_상품_목록(self) -> None:
        got = kp.parse_products([{"fId": "2ETF01", "stkTicker": "069500", "fNm": "KODEX 200"}, {"fId": ""}])
        assert got == [kp.Product("2ETF01", "069500", "KODEX 200")]
        with pytest.raises(kp.KodexFailed):
            kp.parse_products({"msg": "x"})

    def test_구성종목은_평가금액_비중(self) -> None:
        doc = kp.parse_pdf(PDF, "2ETF01", "069500")
        assert doc is not None and doc.base_date == "2026-10-02" and len(doc.holdings) == 3
        assert abs(sum(h.pct for h in doc.holdings) - 100.0) < 1e-9
        assert doc.holdings[1].code == "005930" and abs(doc.holdings[1].pct - 1923444000 / 3482255753 * 100) < 1e-9

    def test_빈_날은_None_깨진_응답은_못_받음(self) -> None:
        assert kp.parse_pdf({"pdf": {"gijunYMD": "20261003", "list": []}}, "f", "t") is None
        with pytest.raises(kp.KodexFailed):
            kp.parse_pdf({"x": 1}, "f", "t")


class Test거슬러_보기:
    def test_빈_날이면_하루씩_거슬러(self, monkeypatch) -> None:
        c = kp.KodexClient()
        asked: list[str] = []

        def fake_get(url: str) -> object:
            asked.append(url.rsplit("=", 1)[1])
            return PDF if url.endswith("20261002") else {"pdf": {"list": []}}

        monkeypatch.setattr(c, "_get", fake_get)
        doc = c.pdf(kp.Product("2ETF01", "069500", "KODEX 200"), date(2026, 10, 4))
        assert doc is not None and asked == ["20261004", "20261003", "20261002"]


class Test429:
    """2026-10-07 첫 운영: 1초 간격으로 20개쯤 뒤부터 117개가 HTTP 429 (docs/infra.md 25.984)."""

    class _Resp:
        def __init__(self, status: int, retry: str | None = None) -> None:
            self.status_code = status
            self.headers = {"Retry-After": retry} if retry else {}

        def json(self) -> object:
            return PDF

    def test_쉬었다_다시_묻는다(self, monkeypatch) -> None:
        c = kp.KodexClient()
        slept: list[float] = []
        monkeypatch.setattr(kp.time, "sleep", slept.append)
        answers = [self._Resp(429, "7"), self._Resp(200)]
        monkeypatch.setattr(c.session, "get", lambda *a, **k: answers.pop(0))
        assert c._get(kp.PDF_URL.format(fid="2ETF01", ymd="20261002")) == PDF
        assert 7.0 in slept and c.calls == 2

    def test_쉬어도_막히면_멈춤(self, monkeypatch) -> None:
        c = kp.KodexClient()
        monkeypatch.setattr(kp.time, "sleep", lambda s: None)
        monkeypatch.setattr(c.session, "get", lambda *a, **k: self._Resp(429))
        with pytest.raises(kp.KodexBlocked):
            c._get(kp.LIST_URL.format(page=1))
        assert c.calls == 1 + kp.RETRY_429

    def test_Retry_After_가_엉뚱하면_기본값_길면_상한(self) -> None:
        assert kp._retry_after(None) == kp.WAIT_429
        assert kp._retry_after("Wed, 07 Oct 2026 07:00:00 GMT") == kp.WAIT_429
        assert kp._retry_after("9999") == kp.WAIT_429_MAX


def test_막히면_남은_것은_부르지_않고_기준을_먼저_받는다() -> None:
    from batch.jobs import etf_tilt as job

    class Client:
        def __init__(self) -> None:
            self.asked: list[str] = []

        def pdf(self, product, base):
            self.asked.append(product.ticker)
            if len(self.asked) == 2:
                raise kp.KodexBlocked("HTTP 429")
            return kp.PdfDoc(product.fid, product.ticker, "2026-10-02", [kp.Holding("005930", "삼성전자", 100.0)])

    products = {t: kp.Product(f"f{t}", t, t) for t in ("069500", "091160", "102780", "229200")}
    client, failed = Client(), {}
    got = job.fetch_kr(client, products, ["069500", "091160", "102780", "229200"], date(2026, 10, 2), failed)
    assert list(got) == ["069500"] and client.asked == ["069500", "091160"]
    assert set(failed) == {"091160", "102780", "229200"} and "429" in failed["229200"]
