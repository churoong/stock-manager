"""밸류에이션 밴드 주간 계산 (docs/stock_detail.md 2장, Step 10).

유니버스 편입 종목 전부의 PBR 3년 밴드와 업종 안 백분위를 valuation_bands 에 쓴다.
종목 상세 화면이 읽는다. 신호 계산은 이 표를 읽지 않고 예전처럼 직접 만든다(기준일이 매일이라).

종목마다 DB 를 부르면 4,000종목 × 2번이라 느리다. 가격은 종목 묶음으로, 자본총계는 나라 전체를 한 번에 읽는다.

실행
  python -m batch.jobs.valuation_bands                 # 두 나라
  python -m batch.jobs.valuation_bands --market KR
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import adjust as adj
from batch.services import valuation_band as vb
from batch.sources import dart

log = logging.getLogger("valuation_bands")

JOB_NAME = "valuation_bands"
#: 정의처는 `batch/sources/dart.ANNUAL_REPORT_CODE` 하나다 (docs/infra.md 25.143)
ANNUAL_REPORT_CODE = dart.ANNUAL_REPORT_CODE
# 가격을 한 번에 읽을 종목 수. 40 × 750일 = 3만 행, 응답 수 MB 안쪽
PRICE_CHUNK = 40
# 750 거래일을 넉넉히 덮는 달력 일수 (휴장일 포함). 1100 은 국내 거래일 732~735 로 750 에 못 미쳐 이 표의 밴드가
# 신호 밴드(최근 750 행)보다 짧았다 — 1150 은 국내 763~768·미국 789~791 (25.1146, XKRX·XNYS 달력으로 셈)
LOOKBACK_CALENDAR_DAYS = 1150

_COLS = (
    "stock_id, as_of_date, metric, current_value, p20, p30, p50, p80, sample, band_rank,"
    " peer_percentile, peer_group, peer_size, price_date, equity_report_date, listed_shares,"
    " currency, skip_reason, calc_version, created_at"
)


def load_members(client: TursoClient, country: str, as_of: str) -> list[dict]:
    """기준일에 편입돼 있던 종목 (docs/infra.md 25.106).

    같은 파일의 `load_equities`·`load_prices` 는 기준일을 거는데 **여기만 오늘의
    유니버스**를 썼다. 과거 기준일로 밴드를 다시 만들면 그때는 편입되지 않았던 종목의
    밴드가 생긴다 — `scores.load_universe` 에서 고친 것과 같은 모양이다(25.97).
    """
    return client.execute(
        "SELECT s.id AS stock_id, s.listed_shares, s.sector, s.currency, s.market_cap_date FROM universe_members u"
        " JOIN stocks s ON s.id = u.stock_id"
        f" WHERE u.included = 1 AND s.country = ? AND u.snapshot_date = {db.snapshot_as_of_sql()}"
        " ORDER BY s.id",
        [country, country, as_of],
    ).dicts()


def shares_before_split(members: list[dict], detections: dict[int, str]) -> set[int]:
    """분할을 감지한 뒤 **주식수가 아직 분할 전 값인** 미국 종목 (docs/infra.md 25.1403, 주간 작업 감사).

    `us_shares` 는 분할 감지 뒤 다음 10-Q 까지 `listed_shares` 를 분할 전 값으로 둔다(25.524) — 그 사이
    `market_cap_date` 도 그대로다. 그런데 밴드의 분할 대기(`db.split_pending_us`)는 수정주가 재수집이 끝나면(done)
    풀려, 분할 뒤 가격 × 분할 전 주식수로 PBR·밴드가 10배 어긋났다(1:10). 감지일 > 주식수 날짜면 대기로 본다.
    주식수 날짜를 모르면 대기"""
    out: set[int] = set()
    for m in members:
        감지 = detections.get(int(m["stock_id"]))
        if not 감지:
            continue
        날 = str(m.get("market_cap_date") or "")[:10]
        if not 날 or 감지[:10] > 날:
            out.add(int(m["stock_id"]))
    return out


def load_equities(client: TursoClient, country: str, as_of: str,
                  stock_ids: list[int] | None = None) -> dict[int, list[tuple[str, float, int]]]:  # fmt: skip
    """나라 전체의 연간 연결 자본총계 (접수일, 자본, 사업연도). 접수일 오름차순. 기준일 뒤에 접수된 것은 읽지 않는다.

    `stock_ids` 를 주면 그 종목만 — 참고 분석의 가격·가치 진단 (`jobs/analyze_extra`, 25.1023)"""
    골라 = None if stock_ids is None else json.dumps(stock_ids)
    out: dict[int, list[tuple[str, float, int]]] = {}
    for r in client.execute(
        # **기준(연결·별도)은 종목마다 한 번만 고른다** (docs/infra.md 25.890). 예전에는 재무 행마다 같은 종목의
        # 기준 고르기를 다시 돌려 나라 전체 질의 하나가 재무 표를 7배쯤 읽었다(인구 DB 실측 47만 → 14만 행).
        # `MATERIALIZED` 가 없으면 SQLite 가 펼쳐 예전과 같아진다. 결과는 같다(`tests/test_financial_basis_890.py`)
        "WITH b AS MATERIALIZED (SELECT s.id AS sid, (SELECT fb.consolidated FROM financials fb"
        " WHERE fb.stock_id = s.id AND fb.report_code = ? AND fb.report_date <= ?"
        " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1) AS cons FROM stocks s WHERE s.country = ?"
        " AND (? IS NULL OR s.id IN (SELECT value FROM json_each(?))))"
        " SELECT f.stock_id, f.report_date, f.total_equity, f.fiscal_year"
        " FROM b CROSS JOIN financials f ON f.stock_id = b.sid AND f.consolidated = b.cons"
        " WHERE f.report_code = ? AND f.total_equity IS NOT NULL AND f.report_date <= ?"
        # 종목 통화로 낸 자본만 (25.915) — USD 로 공시한 국내 회사의 PBR 밴드가 1,400배 틀렸다
        " AND f.currency = (SELECT st.currency FROM stocks st WHERE st.id = f.stock_id)"
        " ORDER BY f.stock_id, f.report_date",
        [ANNUAL_REPORT_CODE, as_of, country, 골라, 골라, ANNUAL_REPORT_CODE, as_of],  # 기준 고르기(25.856)·본 질의
    ).dicts():
        out.setdefault(int(r["stock_id"]), []).append(
            (str(r["report_date"]), float(r["total_equity"]), int(r["fiscal_year"]))
        )
    return out


def load_prices(client: TursoClient, stock_ids: list[int], as_of: str) -> dict[int, list[tuple[str, float]]]:
    """종목 묶음의 최근 종가. 종목마다 마지막 BAND_DAYS 일만 남긴다.

    **분할만 반영한 가격을 쓴다**(docs/adjust.md 7장). 밴드는 3년을 한 잣대로 보는 값이라
    분할이 하나 끼면 밴드 전체가 무의미해진다. 미국 `Adj Close` 는 **배당까지** 반영해 과거 시총(= 가격 × 지금
    주식수)이 배당만큼 낮게 나오고, 지금 PBR 이 밴드 안에서 실제보다 비싸 보인다 (docs/infra.md 25.213)
    """
    since = (date.fromisoformat(as_of) - timedelta(days=LOOKBACK_CALENDAR_DAYS)).isoformat()
    out: dict[int, list[tuple[str, float]]] = {}
    for start in range(0, len(stock_ids), PRICE_CHUNK):
        chunk = stock_ids[start : start + PRICE_CHUNK]
        marks = ", ".join(["?"] * len(chunk))
        묶음: dict[int, list[dict]] = {}
        나라: dict[int, str] = {}
        for r in client.execute(
            "SELECT p.stock_id, p.date, p.adj_close, p.close, s.country"
            f" FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id IN ({marks})"
            " AND p.date > ? AND p.date <= ? AND p.close IS NOT NULL ORDER BY p.stock_id, p.date",
            [*chunk, since, as_of],
        ).dicts():
            묶음.setdefault(int(r["stock_id"]), []).append(r)
            나라[int(r["stock_id"])] = str(r["country"])
        # 수정주가 가운데 빈 행은 직전 계수로 메운다 (`adjust.split_only_prices`, 25.1144)
        for sid, 줄 in 묶음.items():
            for r, px in zip(줄, adj.split_only_prices(줄, 나라[sid]), strict=True):
                if px is not None:
                    out.setdefault(sid, []).append((str(r["date"]), px))
    return {sid: rows[-vb.BAND_DAYS :] for sid, rows in out.items()}


#: 분할 재수집 대기라 밴드를 내지 않은 까닭 (25.1245)
SPLIT_PENDING_REASON = "분할 재수집 대기 — 과거 가격이 분할 전이라 밴드를 내지 않음"


def build_rows(
    members: list[dict],
    prices: dict[int, list[tuple[str, float]]],
    equities: dict[int, list[tuple[str, float]]],
    country: str,
    as_of: str,
    now: str,
    split_pending: set[int] | None = None,
) -> list[tuple]:
    results = {
        int(m["stock_id"]): vb.compute(prices.get(int(m["stock_id"]), []), equities.get(int(m["stock_id"]), []),
                                       m.get("listed_shares"))  # fmt: skip
        for m in members
    }
    # 미국 분할 재수집 대기 종목은 밴드를 내지 않는다 (25.1245) — 과거 가격이 분할 전이라 밴드 위치가 거짓이다
    for sid in split_pending or set():
        if sid in results:
            results[sid] = vb.BandResult(None, None, None, None, None, 0, None, None, None, SPLIT_PENDING_REASON)
    peers = vb.peer_percentiles(
        [(int(m["stock_id"]), m.get("sector"), results[int(m["stock_id"])].current_value) for m in members], country
    )
    rows: list[tuple] = []
    for m in members:
        sid = int(m["stock_id"])
        r = results[sid]
        rank, group, size = peers.get(sid, (None, None, None))
        rows.append((
            sid, as_of, vb.METRIC, r.current_value, r.p20, r.p30, r.p50, r.p80, r.sample, r.band_rank,
            rank, group, size, r.price_date, r.equity_report_date, m.get("listed_shares"),
            str(m.get("currency") or ("KRW" if country == "KR" else "USD")), r.skip_reason, vb.CALC_VERSION, now,
        ))  # fmt: skip
    return rows


def store(client: TursoClient, rows: list[tuple]) -> int:
    if not rows:
        return 0
    width = db.column_count(_COLS)
    for row in rows:
        if len(row) != width:
            raise ValueError(f"값 {len(row)}개인데 열은 {width}개입니다. 열과 값 묶음을 함께 고치세요")
    updates = ", ".join(
        f"{c.strip()} = excluded.{c.strip()}"
        for c in _COLS.split(",")
        if c.strip() not in ("stock_id", "as_of_date", "metric", "calc_version")
    )
    placeholder = "(" + ", ".join(["?"] * width) + ")"
    per = max(1, 20_000 // width // 10)  # 한 문장 100행 안쪽
    statements: list[tuple[str, list[Any]]] = []
    for start in range(0, len(rows), per):
        chunk = rows[start : start + per]
        statements.append((
            f"INSERT INTO valuation_bands ({_COLS}) VALUES {', '.join([placeholder] * len(chunk))}"
            f" ON CONFLICT (stock_id, as_of_date, metric, calc_version) DO UPDATE SET {updates}",
            [v for row in chunk for v in row],
        ))  # fmt: skip
    for start in range(0, len(statements), 20):
        client.batch(statements[start : start + 20])
    return len(rows)


def clear_statement(country: str, as_of: str) -> tuple[str, list[Any]]:
    """같은 날 다시 돌렸을 때를 대비해 **넣기 전에** 그 나라·그 기준일 행을 지운다.

    남길 종목을 나열하면 파라미터가 종목 수만큼 붙어 D1 한도(질의당 100개)를 넘는다
    (docs/infra.md 25.5). 밴드는 다시 계산하면 되는 값이라 지우고 새로 넣는다.
    """
    return (
        "DELETE FROM valuation_bands WHERE as_of_date = ? AND calc_version = ?"
        " AND stock_id IN (SELECT id FROM stocks WHERE country = ?)",
        [as_of, vb.CALC_VERSION, country],
    )


def run(countries: tuple[str, ...], as_of: str | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        기준일 = as_of
        for country in countries:
            started = time.monotonic()
            # 나라마다 직전 거래일이 다르다 (docs/infra.md 25.234). 손으로 준 기준일은 그대로 쓴다
            as_of = 기준일 or cal.default_as_of(country)
            run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=as_of)
            members = load_members(client, country, as_of)
            if not members:
                db.finish_batch_run(client, run_id, status="failed", error_text="유니버스가 비어 있습니다")
                print(f"{country} 유니버스가 비어 있습니다")
                continue
            ids = [int(m["stock_id"]) for m in members]
            prices = load_prices(client, ids, as_of)
            equities = load_equities(client, country, as_of)
            분할대기 = db.split_pending_us(client) if country == "US" else set()
            if country == "US":
                from batch.jobs import us_shares

                분할대기 |= shares_before_split(members, us_shares.split_detections(client))
            rows = build_rows(members, prices, equities, country, as_of, db.now_iso(), 분할대기)
            client.execute(*clear_statement(country, as_of))  # 넣기 전에 지운다
            written = store(client, rows)
            with_band = sum(1 for r in rows if r[4] is not None)
            skipped: dict[str, int] = {}
            for r in rows:
                if r[17]:
                    key = "PBR 표본 250일 미만" if str(r[17]).startswith("PBR 표본") else str(r[17])
                    skipped[key] = skipped.get(key, 0) + 1
            step = {"stocks": written, "with_band": with_band, "skipped": skipped,
                    "seconds": round(time.monotonic() - started, 1)}  # fmt: skip
            db.finish_batch_run(client, run_id, status="success", step_log=step)
            print(f"{country} 밴드 {written}종목 (밴드 있음 {with_band}) 사유 {skipped} {step['seconds']}초")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="밸류에이션 밴드 주간 계산")
    parser.add_argument("--market", choices=["KR", "US"])
    parser.add_argument("--as-of", dest="as_of")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run((args.market,) if args.market else ("KR", "US"), args.as_of)


if __name__ == "__main__":
    sys.exit(guard(main))
