"""스트레스 테스트 실행과 적재.

방법은 docs/stress.md, 계산은 batch/services/stress.py 에 있다.
지금 추천된 바스켓(최신 신호의 권장 비중)을 과거 가격에 그대로 얹어
최악의 구간을 찾는다.

실행
  python -m batch.jobs.stress --market KR
  python -m batch.jobs.stress --market KR --equal     # 동일가중으로
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import stress as st

log = logging.getLogger("stress")

JOB_NAME = "stress"
LOOKBACK_YEARS = 5


def load_basket(client: TursoClient, country: str) -> tuple[dict[int, float], str | None]:
    """최신 신호의 권장 비중. 같은 종목이 여러 기간에 있으면 가장 큰 비중.

    비중은 %(0~100)로 저장돼 있으므로 0~1 로 바꾼다. 합이 1 을 넘으면 넘는
    만큼 줄인다 — 여러 기간의 신호를 한 바스켓으로 합치면 그럴 수 있다.
    """
    # **최신 기준일은 나라별로 잡는다.** 신호는 나라마다 따로 계산되고(`signals.run(market)`),
    # 국내 배치와 미국 배치가 도는 UTC 날짜가 달라 as_of_date 가 어긋난다. 나라 구분 없이
    # MAX 를 잡으면 **늦게 돈 나라의 날짜**가 나오고, 다른 나라는 행이 0건이 된다. 그러면
    # "신호가 없습니다. 매수 신호 배치를 먼저 돌리세요" 로 끝난다 — 신호는 멀쩡히 있는데도.
    # 유니버스 스냅샷에서 같은 일을 겪었다(2026-09-17, db.latest_snapshot_sql 주석).
    # 다른 곳(monitor_targets·accumulation·daily·recommend.ts)은 모두 나라별로 잡고 있었다
    #
    # **기준일은 "마지막으로 계산한 날" 이다** (docs/infra.md 25.359, 25.337 과 같은 규칙). `signals` 의 MAX 만 보면
    # 오늘 계산했는데 한 건도 안 걸린 날 **어제 바스켓**으로 스트레스를 돌렸다. 그날 신호가 없으면 빈 바스켓이다
    as_of = db.last_signal_calc_date(client, country, None)
    if not as_of:
        return {}, None
    rs = client.execute(
        "SELECT sg.stock_id, sg.suggested_weight_pct, sg.as_of_date"
        " FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        # 추천 화면·리포트와 같은 상태 조건 — 제외·폐지된 종목이 바스켓에 들지 않게 (25.802·25.824)
        " WHERE s.country = ? AND sg.as_of_date = ? AND s.status = 'active'"
        # 그 나라·그 기준일의 가장 새 판만 — 새 판이 걸러 낸 종목의 옛 판 행이 섞이지 않게 (infra 25.423·25.464).
        # 바깥 행을 가리키지 않는다(25.428)
        "   AND sg.calc_version = (SELECT MAX(c.calc_version) FROM signals c JOIN stocks s3 ON s3.id = c.stock_id"
        "     WHERE s3.country = ? AND c.as_of_date = ?)",
        [country, as_of, country, as_of],
    )
    rows = rs.dicts()
    if not rows:
        return {}, as_of

    weights: dict[int, float] = {}
    for row in rows:
        pct = row["suggested_weight_pct"]
        if pct is None:
            continue
        sid = int(row["stock_id"])
        weights[sid] = max(weights.get(sid, 0.0), float(pct) / 100)

    total = sum(weights.values())
    if total > 1:
        weights = {sid: w / total for sid, w in weights.items()}
    return weights, str(rows[0]["as_of_date"])


def load_prices(client: TursoClient, stock_ids: list[int], since: str) -> dict[int, dict[str, float]]:
    if not stock_ids:
        return {}
    # D1 은 질의당 파라미터 100개라 종목을 나눠 읽는다 (docs/infra.md 25.5)
    size = db.in_chunk(reserve=1)
    out: dict[int, dict[str, float]] = {}
    for start in range(0, len(stock_ids), size):
        ids = stock_ids[start : start + size]
        rs = client.execute(
            "SELECT stock_id, date, COALESCE(adj_close, close) AS px FROM prices"
            f" WHERE stock_id IN ({', '.join(['?'] * len(ids))}) AND date >= ? AND close IS NOT NULL",
            [*ids, since],
        )
        for row in rs.dicts():
            out.setdefault(int(row["stock_id"]), {})[str(row["date"])] = float(row["px"])
    return out


_COLS = (
    "market, as_of_date, weighting, cash_weight, basket_json, excluded_json,"
    " windows_json, skipped_json, curve_start, curve_end, warnings_json,"
    " calc_version, created_at"
)


def store(client: TursoClient, market: str, as_of: str, result: st.StressResult, now: str) -> None:
    row = (
        market, as_of, result.weighting, result.cash_weight,
        json.dumps({str(k): v for k, v in result.weights_used.items()}),
        json.dumps(result.excluded),
        json.dumps([w.__dict__ for w in result.windows], ensure_ascii=False),
        json.dumps(result.skipped_windows),
        result.curve[0][0], result.curve[-1][0],
        json.dumps(result.warnings, ensure_ascii=False),
        st.CALC_VERSION, now,
    )
    if len(row) != db.column_count(_COLS):
        raise ValueError("stress_runs 값 묶음과 열 개수가 어긋납니다")
    placeholders = ", ".join(["?"] * len(row))
    client.execute(f"INSERT INTO stress_runs ({_COLS}) VALUES ({placeholders})", list(row))


def run(market: str, equal: bool = False) -> int:
    market = market.upper()
    country = "KR" if market == "KR" else "US"
    client = TursoClient()
    try:
        db.apply_migrations(client)
        now = db.now_iso()
        today = datetime.now(UTC).date()
        batch_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=market, trade_date=today.isoformat()
        )

        basket, as_of = load_basket(client, country)
        if not basket and as_of:
            # 계산은 했는데 걸린 신호가 없다 — 고장이 아니다. 어제 바스켓으로 돌리지 않는다 (25.359)
            db.finish_batch_run(
                client, batch_id, status="success", step_log={"stocks": 0, "as_of": as_of, "note": "그날 신호 없음"}
            )
            print(f"{as_of} 에 걸린 신호가 없어 스트레스 테스트를 돌리지 않았습니다")
            return 0
        if not basket:
            db.finish_batch_run(client, batch_id, status="failed", error_text="바스켓이 비어 있습니다")
            print("신호가 없습니다. 매수 신호 배치를 먼저 돌리세요")
            return 1

        if equal:
            basket = {sid: 1.0 / len(basket) for sid in basket}

        since = (today - timedelta(days=365 * LOOKBACK_YEARS)).isoformat()
        prices = load_prices(client, sorted(basket), since)
        dates = sorted({d for by_date in prices.values() for d in by_date})
        if len(dates) < st.WINDOWS[0] * st.MIN_SAMPLE_MULTIPLE:
            db.finish_batch_run(client, batch_id, status="failed", error_text="거래일 부족")
            print("가격이 너무 적습니다. 백필을 먼저 돌리세요")
            return 1

        result = st.run(basket, prices, dates, weighting="equal" if equal else "suggested")
        store(client, market, as_of or today.isoformat(), result, now)

        db.finish_batch_run(
            client, batch_id, status="success",
            step_log={"stocks": len(basket), "excluded": len(result.excluded),
                      "windows": [w.length for w in result.windows], "skipped": result.skipped_windows},
        )
        print(f"바스켓 {len(basket)}종목, 현금 {result.cash_weight:.0%}, 뺀 종목 {len(result.excluded)}")
        for w in result.windows:
            print(f"  {w.length:>3}일 최악: {w.start}~{w.end}  {w.return_pct:+.1%}  낙폭 {w.max_drawdown:.1%}")
        for warning in result.warnings:
            print(f"  경고: {warning}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="스트레스 테스트")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument("--equal", action="store_true", help="권장 비중 대신 동일가중")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.market, args.equal)


if __name__ == "__main__":
    sys.exit(guard(main))
