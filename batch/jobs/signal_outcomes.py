"""신호 성적표 적재 (docs/signals.md 10장). 주 1회. 계산은 services/outcomes.

실행
  python -m batch.jobs.signal_outcomes --market KR
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import calibration as cb
from batch.services import outcomes as oc
from batch.services import trend

log = logging.getLogger("signal_outcomes")

JOB_NAME = "signal_outcomes"
LOOKBACK_DAYS = 400  # 60 거래일 창이 찬 신호까지 넉넉히. 그보다 오래된 신호는 성적이 이미 굳었다
# **굳은 성적은 지우지 않는다** (docs/infra.md 25.336). 예전에는 나라 것을 통째로 지우고 이 창 안의 것만
# 다시 넣어, 400일이 지난 신호의 성적이 표와 집계에서 조용히 사라졌다 — "지난 신호 성적" 이 모르는 새
# 최근 400일 성적으로 바뀐다. 창 밖의 행은 그대로 두고, 집계에는 저장된 행을 읽어 함께 넣는다.

_OUT_COLS = (
    "stock_id, as_of_date, horizon, entry_date, entry_close, ret_5d, ret_20d, ret_60d, max_up, max_down,"
    " hit_target, hit_stop, bench_ret_20d, bench_ret_60d, days_available, computed_at"
)
_STAT_COLS = (
    "country, horizon, window_days, n, avg_ret, win_rate, avg_excess, hit_target_rate, hit_stop_rate,"
    " since, computed_at"
)


def load_signals(client: TursoClient, country: str, since: str) -> list[dict[str, Any]]:
    """**그 기준일의 가장 새 판**의 신호만 (docs/infra.md 25.423·25.464).

    예전에는 (종목·기준일·기간) 마다 가장 새 판을 골랐다. 그러면 새 판에서 **더 이상 나오지 않는** 신호의 옛 판 행이
    남아 성적표에 들어갔다 — 새 판이 걸러 낸 종목을 "신호였다" 로 셌다. 25.423 이 아침 리포트·감시·웹 추천에 넣은
    규칙(나라·기준일마다 MAX 판)과 같게 한다. 같은 판 안에서 겹치면 그대로 마지막 행."""
    rs = client.execute(
        "SELECT sg.stock_id, sg.as_of_date, sg.horizon, sg.target_price, sg.stop_price, s.market, sg.calc_version,"
        " sg.rationale_data"
        " FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = ? AND sg.as_of_date >= ? ORDER BY sg.stock_id, sg.as_of_date, sg.horizon, sg.calc_version",
        [country, since],
    )
    rows = rs.dicts()
    최신판: dict[str, int] = {}
    for row in rows:
        d, v = str(row["as_of_date"]), int(row["calc_version"])
        최신판[d] = max(v, 최신판.get(d, v))
    by_key: dict[tuple[int, str, str], dict[str, Any]] = {}
    for row in rows:
        if int(row["calc_version"]) != 최신판[str(row["as_of_date"])]:
            continue
        # 신호 날의 기준 종가 (25.215). 옛 신호에는 없다 — 그때는 25.208 의 비율로 옮긴다
        try:
            row["ref_close"] = (json.loads(row.pop("rationale_data") or "{}") or {}).get("ref_close")
        except (TypeError, ValueError):
            row["ref_close"] = None
        by_key[(int(row["stock_id"]), str(row["as_of_date"]), str(row["horizon"]))] = row
    return list(by_key.values())


def load_closes(
    client: TursoClient, stock_ids: list[int], since: str, raw: dict[int, dict[str, float]] | None = None
) -> dict[int, list[tuple[str, float]]]:
    """수정주가 계열. raw 를 넘기면 같은 행의 원래 종가도 담는다 (목표·손절 단위 맞추기, docs/infra.md 25.208)."""
    out: dict[int, list[tuple[str, float]]] = {}
    size = db.in_chunk(reserve=1)  # 기준일 하나 몫. D1 은 질의당 파라미터 100개 (infra 25.5)
    for start in range(0, len(stock_ids), size):
        ids = stock_ids[start : start + size]
        rs = client.execute(
            # **분할만 반영한 가격** (docs/infra.md 25.216, `db.SPLIT_ONLY_PRICE_SQL`). 초과수익을 견주는 지수
            # (KOSPI·^GSPC)는 배당이 빠진 **가격 지수**다. 미국 Adj Close(배당 포함)와 빼면 배당률만큼 부푼다
            f"SELECT p.stock_id, p.date, {db.SPLIT_ONLY_PRICE_SQL} AS px, p.close AS raw"
            f" FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id IN ({', '.join(['?'] * len(ids))})"
            # 0 이하 종가는 뺀다 — 수익률 −100%·최저가 0 으로 거짓 손절이 찍혔다. 포트폴리오와 같은 조건 (25.203·25.680)
            " AND p.date >= ? AND p.close > 0 AND COALESCE(p.adj_close, p.close) > 0 ORDER BY p.stock_id, p.date",
            [*ids, since],
        )
        for row in rs.dicts():
            out.setdefault(int(row["stock_id"]), []).append((str(row["date"]), float(row["px"])))
            if raw is not None:
                raw.setdefault(int(row["stock_id"]), {})[str(row["date"])] = float(row["raw"])
    return out


def to_row(o: oc.Outcome, now: str) -> tuple:
    return (
        o.stock_id, o.as_of_date, o.horizon, o.entry_date, o.entry_close,
        o.rets.get(5), o.rets.get(20), o.rets.get(60), o.max_up, o.max_down,
        None if o.hit_target is None else int(o.hit_target), None if o.hit_stop is None else int(o.hit_stop),
        o.bench.get(20), o.bench.get(60), o.days_available, now,
    )


#: "그 시장이 정말 거래·수집된 날" 로 볼 최소 비율 — 최근 40일 가운데 가장 많이 들어온 날 종목 수의 절반 (25.702).
#: 일부 종목만 늦게 들어온 행(수정주가 재수집 100종목·구간 백필)이 끝날을 끌어올리지 못하게 한다
BROAD_SHARE = 0.5
BROAD_LOOKBACK_DAYS = 40


def market_last_dates(client: TursoClient, country: str, today: str) -> dict[str, str]:
    """시장마다 **대부분의 종목이 들어온** 마지막 날 (docs/infra.md 25.702, 교차검증 재현).

    25.697 은 나라 전체 `MAX(date)` 였다. 국내는 KOSPI·KOSDAQ 을 따로 받고(한쪽만 실패할 수 있다) 미국은 일부
    종목만 받는 경로(수정주가 재수집·구간 백필)가 있어, 늦게 들어온 행 하나가 끝날을 끌어올려 정상 거래 중인 종목이
    "끊김" 으로 채워졌다.
    """
    since = (datetime.fromisoformat(today) - timedelta(days=BROAD_LOOKBACK_DAYS)).date().isoformat()
    rs = client.execute(
        "SELECT s.market, p.date, COUNT(*) FROM prices p JOIN stocks s ON s.id = p.stock_id"
        " WHERE s.country = ? AND p.date >= ? AND p.date <= ? GROUP BY s.market, p.date",
        [country, since, today],
    )
    # **미국은 나라 하나로 센다** (25.706, 교차검증 재현). 미국 시장들(NYSE·NASDAQ·NYSE American·IEX…)은 달력도 수집
    # 경로도
    # 같다 — 시장마다 세면 한 종목뿐인 시장은 그 종목의 끝이 곧 시장 끝이라 끊김 감지가 꺼졌다. 국내는 KOSPI·KOSDAQ 을
    # 따로
    # 받아 한쪽만 멈출 수 있어 시장마다 센다. 돌려줄 때는 미국 종목 누구나 찾도록 시장 이름마다 같은 값을 붙인다
    by_market: dict[str, list[tuple[str, int]]] = {}
    이름들: set[str] = set()
    for market, day, n in rs.rows:
        이름들.add(str(market))
        키 = str(market) if country == "KR" else country
        by_market.setdefault(키, []).append((str(day), int(n)))
    if country != "KR":
        합: dict[str, int] = {}
        for day, n in by_market.get(country, []):
            합[day] = 합.get(day, 0) + n
        by_market = {country: list(합.items())} if 합 else {}
    out: dict[str, str] = {}
    for market, days in by_market.items():
        최대 = max(n for _d, n in days)
        넓은 = [d for d, n in days if n >= 최대 * BROAD_SHARE]
        if 넓은:
            out[market] = max(넓은)
    if country != "KR" and country in out:
        out.update(dict.fromkeys(이름들, out[country]))
    if not out:
        # 40일 동안 한 행도 없다 — 수집이 멈춘 것이지 종목이 끊긴 것이 아니다. 채우지 않되 말한다 (25.706)
        log.warning("%s 최근 %d일 시세가 없어 성적표의 시세 끊김 보정을 하지 않습니다", country, BROAD_LOOKBACK_DAYS)
    return out


def load_frozen(client: TursoClient, country: str, since: str) -> list[oc.Outcome]:
    """창(`since`) 밖으로 나가 다시 계산하지 않는, 이미 저장된 성적 (25.336). 집계에 함께 넣는다."""
    rs = client.execute(
        "SELECT o.stock_id, o.as_of_date, o.horizon, o.entry_date, o.entry_close, o.ret_5d, o.ret_20d,"
        " o.ret_60d, o.max_up, o.max_down, o.hit_target, o.hit_stop, o.bench_ret_20d, o.bench_ret_60d,"
        " o.days_available FROM signal_outcomes o JOIN stocks s ON s.id = o.stock_id"
        " WHERE s.country = ? AND o.as_of_date < ?",
        [country, since],
    )
    out: list[oc.Outcome] = []
    for r in rs.dicts():
        out.append(oc.Outcome(
            stock_id=int(r["stock_id"]), as_of_date=str(r["as_of_date"]), horizon=str(r["horizon"]),
            entry_date=r["entry_date"], entry_close=r["entry_close"],
            rets={5: r["ret_5d"], 20: r["ret_20d"], 60: r["ret_60d"]},
            max_up=r["max_up"], max_down=r["max_down"],
            hit_target=None if r["hit_target"] is None else bool(r["hit_target"]),
            hit_stop=None if r["hit_stop"] is None else bool(r["hit_stop"]),
            bench={5: None, 20: r["bench_ret_20d"], 60: r["bench_ret_60d"]},
            days_available=int(r["days_available"] or 0),
        ))
    return out


def since_of(today: str) -> str:
    return (datetime.fromisoformat(today) - timedelta(days=LOOKBACK_DAYS)).date().isoformat()


def store(
    client: TursoClient, country: str, rows: list[tuple], stats: list[oc.Stat], now: str, since: str
) -> None:
    """창(`since` 이후) 안의 것만 지우고 다시 넣는다. 창 밖의 굳은 성적은 남긴다 (25.336)."""
    if any(len(r) != db.column_count(_OUT_COLS) for r in rows):
        raise ValueError("signal_outcomes 값 묶음과 열 개수가 어긋납니다")
    statements: list[tuple[str, list[Any]]] = [
        (
            "DELETE FROM signal_outcomes WHERE stock_id IN (SELECT id FROM stocks WHERE country = ?)"
            " AND as_of_date >= ?",
            [country, since],
        ),
        ("DELETE FROM signal_outcome_stats WHERE country = ?", [country]),
    ]
    holes = "(" + ", ".join(["?"] * db.column_count(_OUT_COLS)) + ")"
    for start in range(0, len(rows), 300):
        chunk = rows[start : start + 300]
        args: list[Any] = []
        for row in chunk:
            args.extend(row)
        statements.append((
            f"INSERT INTO signal_outcomes ({_OUT_COLS}) VALUES " + ", ".join([holes] * len(chunk)), args,
        ))
    for st in stats:
        statements.append((
            f"INSERT INTO signal_outcome_stats ({_STAT_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [country, st.horizon, st.window, st.n, st.avg_ret, st.win_rate, st.avg_excess,
             st.hit_target_rate, st.hit_stop_rate, st.since, now],
        ))
    client.batch(statements)


def compute(client: TursoClient, country: str, today: str) -> tuple[list[oc.Outcome], list[oc.Stat]]:
    since = since_of(today)
    signals = load_signals(client, country, since)
    frozen = load_frozen(client, country, since)
    if not signals:
        return [], oc.summarize(frozen)
    ids = sorted({int(s["stock_id"]) for s in signals})
    raw: dict[int, dict[str, float]] = {}
    closes = load_closes(client, ids, since, raw)
    index_cache: dict[str, list[tuple[str, float]]] = {}
    results: list[oc.Outcome] = []
    # 시장마다 대부분의 종목이 들어온 마지막 날 — 한 종목만 끊겼는지, 수집이 멈췄는지를 가른다 (25.697·25.702)
    끝날 = market_last_dates(client, country, today)
    for sig in signals:
        code = trend.index_for_market(sig.get("market")) or ""
        if code and code not in index_cache:
            index_cache[code] = trend.load_index_closes(client, code, today, days=LOOKBACK_DAYS)[0]
        sid = int(sig["stock_id"])
        results.append(
            oc.evaluate(
                sig, closes.get(sid, []), index_cache.get(code), raw.get(sid),
                # 미국은 나라 값으로 물러난다 — 40일 넘게 행이 없는 작은 시장은 키가 없었다 (25.710, 교차검증)
                끝날.get(str(sig.get("market"))) or (끝날.get(country) if country != "KR" else None),
            )  # fmt: skip
        )
    return results, oc.summarize(results + frozen)


def load_scores(client: TursoClient, country: str, since: str) -> dict[tuple[int, str], float]:
    """신호가 있던 날의 종합 점수 — 점수 보정표(docs/signals.md 10.1)의 가로축. 그날의 가장 새 판 하나."""
    rs = client.execute(
        "SELECT sc.stock_id, sc.as_of_date, sc.total_score FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
        " WHERE s.country = ? AND sc.as_of_date >= ? AND sc.total_score IS NOT NULL"
        "  AND sc.calc_version = (SELECT MAX(c.calc_version) FROM scores c"
        "                         WHERE c.stock_id = sc.stock_id AND c.as_of_date = sc.as_of_date)"
        "  AND EXISTS (SELECT 1 FROM signals g WHERE g.stock_id = sc.stock_id AND g.as_of_date = sc.as_of_date)",
        [country, since],
    )
    return {(int(r["stock_id"]), str(r["as_of_date"])): float(r["total_score"]) for r in rs.dicts()}


def calibrations(
    client: TursoClient, country: str, outcomes: list[oc.Outcome]
) -> tuple[list[dict[str, Any]], str | None]:
    """창(5·20·60일)마다 점수 보정표 payload 와 경고. 성적이 없으면 빈 목록.

    점수를 못 읽으면 보정표만 비우고 **경고로 말한다**(실행 기록 `warnings`) — 성적표 저장은 이미 끝났고, 곁다리가 본
    작업을 실패로 만들면 안 된다. 표가 없는 것과 한도·인증 실패를 가르지 않는 대신 사유를 그대로 남긴다.
    """
    if not outcomes:
        return [], None
    since = min(o.as_of_date for o in outcomes)
    try:
        scores = load_scores(client, country, since)
    except Exception as exc:  # noqa: BLE001
        말 = f"점수 보정표 — 점수를 읽지 못해 뺐다: {exc}"
        logging.getLogger("signal_outcomes").warning(말)
        return [], 말
    return [cb.calibrate(cb.pairs_for(outcomes, scores, w), w).as_payload() for w in oc.WINDOWS], None


def run(market: str) -> int:
    country = "KR" if market.upper() == "KR" else "US"
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date().isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=today)
        try:
            results, stats = compute(client, country, today)
            now = db.now_iso()
            store(client, country, [to_row(o, now) for o in results], stats, now, since_of(today))
            # 점수 보정표 (docs/signals.md 10.1, 25.949) — 굳은 성적까지 포함해 점수와 수익을 짝짓는다
            보정, 보정_경고 = calibrations(client, country, results + load_frozen(client, country, since_of(today)))
        except Exception as exc:
            db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise
        db.finish_batch_run(
            client, run_id, status="success",
            step_log={"signals": len(results), "stats": [(s.horizon, s.window, s.n) for s in stats],
                      "calibration": 보정, "warnings": [보정_경고] if 보정_경고 else []},
        )  # fmt: skip
        print(f"신호 성적표 {country}: 신호 {len(results)}건")
        for 표 in 보정:
            for 줄 in cb.render_lines(표):
                print(f"  점수 보정 {줄}")
        for s in stats:
            if s.avg_ret is not None:
                win = f"{s.win_rate * 100:.0f}%" if s.win_rate is not None else "-"
                print(f"  {s.horizon:5s} {s.window:3d}일 n={s.n:3d} 평균 {s.avg_ret * 100:+.1f}% 이긴 {win}")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="신호 성적표")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.market)


if __name__ == "__main__":
    sys.exit(guard(main))
