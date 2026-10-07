"""마스터 갱신이 빈 상장일로 좋은 값을 덮지 않는다 (docs/infra.md 25.654, 감사)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import universe
from batch.sources import krx
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_yahoo_fallback import _client


def test_빈_상장일은_있던_값을_지우지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: False)
    c = _client()
    c.conn.execute("UPDATE stocks SET listed_date = '1975-06-11' WHERE ticker = '005930'")
    행 = krx.KrxMasterRow.from_raw({
        "ISU_SRT_CD": "005930", "ISU_CD": "KR7005930003", "ISU_ABBRV": "삼성전자", "ISU_NM": "삼성전자보통주",
        "LIST_DD": "", "MKT_TP_NM": "KOSPI", "SECUGRP_NM": "주권", "KIND_STKCERT_TP_NM": "보통주", "LIST_SHRS": "100",
    })  # fmt: skip
    monkeypatch.setattr(krx, "fetch_master", lambda *a, **k: FetchResult(ok=True, data=[행]))
    universe.refresh_kr_master(c, "20260925")  # type: ignore[arg-type]
    assert c.conn.execute("SELECT listed_date FROM stocks WHERE ticker = '005930'").fetchone() == ("1975-06-11",)
