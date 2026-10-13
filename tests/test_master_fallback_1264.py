"""국내 마스터도 기준일 응답이 비면 직전 거래일로 한 번 물러난다 (docs/infra.md 25.1264, 유니버스 감사 재현)."""

from __future__ import annotations

import pytest

from batch.core import db
from batch.jobs import universe
from batch.sources import krx
from batch.sources.yfinance_src import FetchResult
from tests.test_kr_yahoo_fallback import _client


def test_금요일_치가_비면_목요일_목록으로_갱신한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "usage_blocked_today", lambda client, api: False)
    c = _client()
    불림: list[tuple[str, str]] = []

    def fake(market: str, bas_dd: str, *a, **k) -> FetchResult:  # noqa: ANN002, ANN003
        불림.append((market, bas_dd))
        if bas_dd == "20261016":  # 금요일 치는 아직 안 나왔다
            return FetchResult(ok=True, data=[])
        행 = krx.KrxMasterRow.from_raw({
            "ISU_SRT_CD": "999990" if market == "KOSPI" else "999991", "ISU_CD": "KR7999990003", "ISU_ABBRV": "새회사",
            "ISU_NM": "새회사보통주", "LIST_DD": "20240102", "MKT_TP_NM": market, "SECUGRP_NM": "주권",
            "KIND_STKCERT_TP_NM": "보통주", "LIST_SHRS": "100",
        })  # fmt: skip
        return FetchResult(ok=True, data=[행])

    monkeypatch.setattr(krx, "fetch_master", fake)
    n, 경고 = universe.refresh_kr_master(c, "20261016")  # type: ignore[arg-type]
    assert ("KOSPI", "20261015") in 불림 and ("KOSDAQ", "20261015") in 불림
    assert n == 2
    assert c.conn.execute("SELECT COUNT(*) FROM stocks WHERE ticker IN ('999990', '999991')").fetchone()[0] == 2
    assert any("직전 거래일 20261015 목록으로 갱신" in w for w in 경고)
    assert not any("한 시장의 종목 목록을 못 받아" in w for w in 경고)  # 두 시장 다 받아 폐지 판정이 돈다
