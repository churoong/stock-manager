"""추천 ETF 를 `stocks` 에 잇는다 — 매일 종가·매매 기록용 (docs/infra.md 25.896).

    python -m batch.jobs.etf_link            # 두 나라
    python -m batch.jobs.etf_link --market KR

**왜.** 매매 기록(`trades.stock_id`)과 시세(`prices.stock_id`)는 `stocks` 를 가리킨다. ETF 는 0010 에서 따로 두었다 —
그대로 두면 ETF 는 매매를 적을 수도, 날마다 종가를 받을 수도 없다
(2026-10-02 사용자 요청 "ETF 매일 종가랑 매매 기록도 되게 해줘").

**무엇을 잇나.** 그 나라 **가장 새 판정의 통과 ETF**(핵심 `etf_picks`·위성 `etf_satellite_picks`). 이미 이은 것은
그대로 둔다 —
판정에서 빠져도 끊지 않는다(그 ETF 를 들고 있을 수 있고, 시세가 이어져야 평가가 된다).
`stocks.asset_type = 'etf'` 로 가른다. 유니버스·점수·신호·종목 찾기는 `'stock'` 만 본다.

- 국내: `market = 'ETF'` — 거래소 시장별 주식 수집(KOSPI·KOSDAQ 루프)·빠진 날 메우기·폐지 대조가 이 행을 건드리지 않게
- 미국: 거래소 이름(`etfs.exchange`, 없으면 'ETF'). 같은 티커의 미국 행이 이미 있으면(예전 나스닥 목록에서 들어온 줄)
그 줄을 ETF 로 바꿔 쓴다
- 시세는 일일 배치가 받는다: 국내 `daily._store_kr_etf_day`(거래소 ETF 일별), 미국 `collect_us_prices`(야후,
`include_etf=True`)
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard

log = logging.getLogger("etf_link")

#: 그 나라 가장 새 판정의 통과 ETF (핵심·위성). 판정 표는 나라별로 날짜가 다르다 — 나라마다 MAX 를 잡는다.
#: **그날의 가장 새 계산 판만** 본다 (docs/infra.md 25.935, 감사) — 판을 올리고 같은 날 다시 판정하면 옛 판 행이 남는데
#: (지우기는 지금 판만),
#: 판을 안 봐 옛 규칙에서만 통과한 ETF 까지 종목표에 이었다. 화면(`web/lib/etf.ts`·`etfSatellite.ts`)과 같은 잣대다.
#: 하위 질의는 바깥 행을 가리키지 않는다(25.858 — 행마다 다시 돌면 읽기가 N² 가 된다)
PASSED_ETFS = (
    "SELECT e.id, e.symbol, e.country, e.name, e.exchange, e.yahoo_symbol, e.stock_id FROM etfs e"
    " WHERE e.country = ? AND e.status = 'active' AND (e.id IN ("
    "   SELECT pk.etf_id FROM etf_picks pk WHERE pk.passed = 1 AND pk.as_of_date = ("
    "     SELECT pk2.as_of_date FROM etf_picks pk2 CROSS JOIN etfs e2 ON e2.id = pk2.etf_id WHERE e2.country = ?"
    " ORDER BY pk2.as_of_date DESC LIMIT 1)"
    "     AND pk.calc_version = (SELECT MAX(pk3.calc_version) FROM etf_picks pk3 JOIN etfs e3 ON e3.id = pk3.etf_id"
    "       WHERE e3.country = ? AND pk3.as_of_date = ("
    "         SELECT pk4.as_of_date FROM etf_picks pk4 CROSS JOIN etfs e4 ON e4.id = pk4.etf_id WHERE e4.country = ?"
    " ORDER BY pk4.as_of_date DESC LIMIT 1)))"
    " OR e.id IN ("
    "   SELECT sp.etf_id FROM etf_satellite_picks sp WHERE sp.passed = 1 AND sp.as_of_date = ("
    "     SELECT sp2.as_of_date FROM etf_satellite_picks sp2 CROSS JOIN etfs e2 ON e2.id = sp2.etf_id"
    "     WHERE e2.country = ? ORDER BY sp2.as_of_date DESC LIMIT 1)"
    "     AND sp.calc_version = (SELECT MAX(sp3.calc_version) FROM etf_satellite_picks sp3"
    "       JOIN etfs e3 ON e3.id = sp3.etf_id WHERE e3.country = ? AND sp3.as_of_date = ("
    "         SELECT sp4.as_of_date FROM etf_satellite_picks sp4 CROSS JOIN etfs e4 ON e4.id = sp4.etf_id"
    "         WHERE e4.country = ? ORDER BY sp4.as_of_date DESC LIMIT 1))))"
)
#: 위 질의의 물음표 수 — 모두 나라 코드다
PASSED_ETFS_ARGS = PASSED_ETFS.count("?")

#: **ETF 전술 가상계좌가 사고파는 대표지수 ETF 와 그 인버스** (docs/paper.md 17장, 2026-11-06 사용자 지시
#: "지수 예측을 통한 대표지수ETF(ex. kodex 200, kodex 200 인버스) 를 매수매도하는 ETF 계좌" → "계좌는 두개를 만들거야.
#: 국내ETF, 미국ETF"). 추천 판정과 상관없이 늘 잇는다 — 인버스는 추천 탭에서 빠지므로(CLAUDE.md, docs/etf.md)
#: 판정 통과로는 영영 잇지 못한다. 국내는 거래소 ETF 목록의 **이름**으로 찾는다(코드를 박지 않는다 — 069500·114800
#: 으로 알고 있으나 `[확인필요]`), 미국은 티커
TACTICAL_ETFS: dict[str, dict[str, tuple[str, ...]]] = {
    "KR": {"names": ("KODEX 200", "KODEX 인버스"), "symbols": ()},
    "US": {"names": (), "symbols": ("SPY", "SH")},
}
TACTICAL_SQL = (
    "SELECT id, symbol, country, name, exchange, yahoo_symbol, stock_id FROM etfs"
    " WHERE country = ? AND status = 'active' AND (name IN (?, ?) OR symbol IN (?, ?))"
)


def tactical_args(country: str) -> list[str]:
    """`TACTICAL_SQL` 인자 — 이름 둘·티커 둘 자리를 빈 글로 채운다(빈 글은 어떤 행과도 같지 않다)"""
    t = TACTICAL_ETFS[country]
    이름 = (list(t["names"]) + ["", ""])[:2]
    티커 = (list(t["symbols"]) + ["", ""])[:2]
    return [country, *이름, *티커]

CURRENCY = {"KR": "KRW", "US": "USD"}


def _market(etf: dict[str, Any]) -> str:
    if etf["country"] == "KR":
        return "ETF"
    return str(etf.get("exchange") or "ETF")


def link(client: TursoClient, country: str) -> dict[str, int]:
    """그 나라 통과 ETF 중 아직 잇지 않은 것을 `stocks` 에 잇는다. (새로 이음, 이미 있던 줄을 바꿔 씀, 이미 이어짐)
    수."""
    country = country.upper()
    rows = client.execute(PASSED_ETFS, [country] * PASSED_ETFS_ARGS).dicts()
    # ETF 전술 계좌의 네 ETF 도 함께(위 `TACTICAL_ETFS`) — 이미 판정 통과로 들어온 것은 한 번만
    전술 = client.execute(TACTICAL_SQL, tactical_args(country)).dicts() if country in TACTICAL_ETFS else []
    본 = {int(r["id"]) for r in rows}
    rows = list(rows) + [r for r in 전술 if int(r["id"]) not in 본]
    찾을 = len(TACTICAL_ETFS.get(country, {}).get("names", ())) + len(TACTICAL_ETFS.get(country, {}).get("symbols", ()))
    새로 = 바꿈 = 이미 = 0
    now = db.now_iso()
    for etf in rows:
        if etf["stock_id"]:
            이미 += 1
            continue
        ticker = str(etf["symbol"])
        있던 = client.execute(
            "SELECT id FROM stocks WHERE country = ? AND ticker = ? ORDER BY id LIMIT 1", [country, ticker]
        ).scalar()
        if 있던:
            client.execute(
                "UPDATE stocks SET asset_type = 'etf', status = 'active', yahoo_symbol = COALESCE(yahoo_symbol, ?),"
                " fetched_at = ? WHERE id = ?",
                [etf["yahoo_symbol"], now, int(있던)],
            )
            stock_id = int(있던)
            바꿈 += 1
        else:
            rs = client.execute(
                "INSERT INTO stocks (ticker, market, country, name_ko, name_en, currency, yahoo_symbol, status,"
                " asset_type, source, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 'etf', 'etf_link', ?)",
                [
                    ticker, _market(etf), country,
                    etf["name"] if country == "KR" else None, etf["name"] if country == "US" else None,
                    CURRENCY[country], etf["yahoo_symbol"], now,
                ],
            )  # fmt: skip
            stock_id = int(rs.last_insert_rowid or client.execute(
                "SELECT id FROM stocks WHERE country = ? AND ticker = ? AND market = ?", [country, ticker, _market(etf)]
            ).scalar())  # fmt: skip
            새로 += 1
        client.execute("UPDATE etfs SET stock_id = ? WHERE id = ?", [stock_id, int(etf["id"])])
    # 목록에서 못 찾은 전술 ETF 수 — 0 이 아니면 그 계좌는 살 것이 없다(이름이 바뀌었거나 목록에 없음)
    return {"new": 새로, "converted": 바꿈, "already": 이미, "tactical_missing": 찾을 - len(전술)}


def main() -> int:
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="추천 ETF 를 stocks 에 잇는다 (매일 종가·매매 기록)")
    parser.add_argument("--market", choices=["KR", "US"])
    args = parser.parse_args()
    client = TursoClient()
    try:
        for country in [args.market] if args.market else ["KR", "US"]:
            print(country, link(client, country))
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(guard(main))
