"""국내 기업행위 바로 다시 내기가 이력의 구멍 위 계수를 말한다 (docs/infra.md 25.1250, 국내 시세 감사 재현).

09-08 전 시장 수집 구멍(되받지 않음) 위에서 +10% 가 계수로 잡히고, 09-11 진짜 1:5 분할로 바로 다시 내면 그 계수가 이력에 곱해진다.
"""

from __future__ import annotations

from batch.jobs import daily
from tests.test_report_picks import SqliteClient


class _행:
    def __init__(self, isu: str, close: float, chg: float) -> None:
        self.isu_cd, self.close, self.change_pct = isu, close, chg


def test_구멍_위_계수를_경고한다() -> None:
    c = SqliteClient()
    k = c.conn
    k.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at, asset_type)"
              " VALUES (1,'111111','KOSPI','KR','A','KRW','active','t','t','stock'),"
              " (2,'222222','KOSPI','KR','B','KRW','active','t','t','stock')")  # fmt: skip
    for sid, d, close in ((1, "2026-09-07", 1000), (1, "2026-09-09", 1100), (1, "2026-09-10", 1100),
                          (2, "2026-09-07", 500), (2, "2026-09-09", 500), (2, "2026-09-10", 500),
                          (1, "2026-09-11", 220), (2, "2026-09-11", 500)):  # fmt: skip
        k.execute("INSERT INTO prices (stock_id, date, close, change_pct, currency, source, fetched_at)"
                  " VALUES (?, ?, ?, 0.0, 'KRW', 'krx_openapi', 't')", [sid, d, close])  # fmt: skip
    말 = daily._detect_kr_actions(c, "2026-09-11", [_행("111111", 220, 0.0), _행("222222", 500, 0.0)],
                                {"111111": 1, "222222": 2})  # fmt: skip
    assert any("바로 다시 냈습니다" in m for m in 말)
    경고 = [m for m in 말 if m.startswith("⚠") and "빠진 거래일" in m]
    assert 경고 and "2026-09-09" in 경고[0]
    # 단서 없는 감지는 날짜와 함께 남긴다 — 가상모의투자 이어 멈춤이 경계 창 뒤에도 본다 (25.1548)
    import json

    from batch.services import adjust_drift as adjd

    기록 = c.conn.execute("SELECT value FROM settings WHERE key = ?", [adjd.KR_ACTION_LOG_KEY]).fetchone()
    assert 기록 and json.loads(기록[0]) == {"1": "2026-09-11"}
