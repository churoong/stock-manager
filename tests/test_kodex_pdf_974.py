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
