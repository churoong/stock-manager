"""종가 0 인 옛 정지일 행이 국내 분할을 가리지 않는다 (docs/infra.md 25.1251, 국내 시세 감사 재현).

25.203 이 막기 전에 쌓인 정지일 행(종가 0)이 있으면 수정주가 계산과 일일 기업행위 감지가 그 분할을 놓쳤다.
"""

from __future__ import annotations

from batch.jobs import adjust_kr, daily
from tests.test_report_picks import SqliteClient


def _db(rows: list[tuple[str, float, float]]) -> SqliteClient:
    c = SqliteClient()
    c.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at,"
                   " asset_type) VALUES (1,'111111','KOSPI','KR','A','KRW','active','t','t','stock')")  # fmt: skip
    for d, close, chg in rows:
        c.conn.execute("INSERT INTO prices (stock_id, date, close, change_pct, currency, source, fetched_at)"
                       " VALUES (1, ?, ?, ?, 'KRW', 'krx_openapi', 't')", [d, close, chg])  # fmt: skip
    return c


def test_수정주가는_종가_0_행을_건너_분할을_찾는다() -> None:
    c = _db([("2021-05-03", 50000, 0.0), ("2021-05-04", 0, 0.0), ("2021-05-06", 5100, 2.0), ("2021-05-07", 5100, 0.0)])
    r = adjust_kr.adjust_stocks(c, [1])
    assert r["stocks_with_action"] == 1
    assert c.conn.execute("SELECT adj_close FROM prices WHERE date = '2021-05-03'").fetchone()[0] == 5000.0


class _행:
    def __init__(self, isu: str, close: float, chg: float) -> None:
        self.isu_cd, self.close, self.change_pct = isu, close, chg


def test_일일_감지는_전날_종가_0_이면_제_마지막_종가와_견준다() -> None:
    c = _db([("2021-05-03", 50000, 0.0), ("2021-05-04", 0, 0.0)])
    말 = daily._detect_kr_actions(c, "2021-05-06", [_행("111111", 5100, 2.0)], {"111111": 1})
    assert any("111111" in m and "2021-05-03" in m for m in 말), 말
