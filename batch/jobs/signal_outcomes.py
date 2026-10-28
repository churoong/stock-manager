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
from batch.services import adjust as adj
from batch.services import calibration as cb
from batch.services import outcomes as oc
from batch.services import plan_fill as pf
from batch.services import shadow as sh
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
        " sg.rationale_data, sg.tranche_plan"  # 분할 계획 체결 성적 (25.1327) — 같은 행의 칸 하나
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
            # 수정주가 가운데 빈 행은 직전 계수로 메운다 (`adjust.split_only_prices`, 25.1165) — 식 그대로 읽으면
            # 그 행에 원 종가(1:10 이면 10배)가 들어가 60일 창 최고가가 +900% 로 튀고 "목표가 도달" 이 거짓으로 찍혔다
            "SELECT p.stock_id, p.date, p.adj_close, p.close, s.country, p.close AS raw"
            f" FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id IN ({', '.join(['?'] * len(ids))})"
            # 0 이하 종가는 뺀다 — 수익률 −100%·최저가 0 으로 거짓 손절이 찍혔다. 포트폴리오와 같은 조건 (25.203·25.680)
            " AND p.date >= ? AND p.close > 0 AND COALESCE(p.adj_close, p.close) > 0 ORDER BY p.stock_id, p.date",
            [*ids, since],
        )
        묶음: dict[int, list[dict]] = {}
        for row in rs.dicts():
            묶음.setdefault(int(row["stock_id"]), []).append(row)
        for sid, 줄 in 묶음.items():
            for row, px in zip(줄, adj.split_only_prices(줄, str(줄[0]["country"])), strict=True):
                if px is None or px <= 0:
                    continue
                out.setdefault(sid, []).append((str(row["date"]), float(px)))
                if raw is not None:
                    raw.setdefault(sid, {})[str(row["date"])] = float(row["raw"])
    return out


#: 그림자 장부(docs/paper.md 16장, 25.1862)의 하루 시세. 시가·고가·저가는 시세 사본(`price_replica.PRICE_COLS`)에 없다 —
#: 사본으로 보내진 질의가 열 없음 오류로 Turso 로 물러난다(`ReplicaClient` 의 폴백). 단기 신호 종목·가장 이른 신호
#: 날부터만이라 위 종가 읽기보다 작다. 0 이하 수정주가 거르개는 `load_closes` 와 같다(25.1863)
BARS_SQL = (
    "SELECT p.stock_id, p.date, p.open, p.high, p.low, p.close, p.adj_close, s.country"
    " FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id IN (SELECT value FROM json_each(?))"
    " AND p.date >= ? AND p.close > 0 AND COALESCE(p.adj_close, p.close) > 0 ORDER BY p.stock_id, p.date"
)
#: 그 종목 마지막 시세가 시장 마지막 날보다 이만큼(달력일) 넘게 앞이면 계열이 끝났다고 본다 — 스윙 기한 10거래일 ≈ 14일
SHADOW_ENDED_DAYS = 14


def load_bars(client: TursoClient, stock_ids: list[int], since: str) -> dict[int, list[sh.Bar]]:
    """분할만 반영한 하루 시세 — 종가의 분할 계수(`adj.split_only_prices` ÷ 원 종가)를 시가·고가·저가에도 곱한다.
    시가·고가·저가가 비거나 0 이하인 날은 뺀다(그날은 판정하지 않는다 — 다음 날로 넘어간다)."""
    out: dict[int, list[sh.Bar]] = {}
    묶음: dict[int, list[dict]] = {}
    for row in client.execute(BARS_SQL, [json.dumps(stock_ids), since]).dicts():
        묶음.setdefault(int(row["stock_id"]), []).append(row)
    for sid, 줄 in 묶음.items():
        for row, px in zip(줄, adj.split_only_prices(줄, str(줄[0]["country"])), strict=True):
            값 = [row.get(k) for k in ("open", "high", "low")]
            if px is None or px <= 0 or any(v is None or float(v) <= 0 for v in 값):
                continue
            f = float(px) / float(row["close"])
            o, h, lo = (float(v) * f for v in 값)  # type: ignore[arg-type]
            out.setdefault(sid, []).append(sh.Bar(str(row["date"]), o, h, lo, float(px)))
    return out


def shadow_trades(signals: list[dict[str, Any]], bars: dict[int, list[sh.Bar]], costs: sh.Costs, country: str,
                  market_last: dict[str, str] | None = None) -> list[sh.Trade]:  # fmt: skip
    """넘긴 신호마다 단타·스윙 규칙으로 가상 체결 (25.1862). 돈·종목 수 제약 없이 모두 — 계좌보다 표본이 크다.
    단기 신호로 거르는 것은 부르는 쪽(`compute`) 한 곳이다(25.1863 — 두 곳에 겹쳐 하나를 빼도 시험이 못 잡았다).
    `market_last`(시장 → 대부분의 종목이 들어온 마지막 날)가 그 종목 마지막 시세보다 `SHADOW_ENDED_DAYS` 넘게
    뒤면 끊김"""
    from datetime import date as _date

    out: list[sh.Trade] = []
    for sig in signals:
        sid, d = int(sig["stock_id"]), str(sig["as_of_date"])
        줄 = bars.get(sid, [])
        뒤 = [b for b in 줄 if b.date > d]
        앞 = [b for b in 줄 if b.date <= d]
        시장끝 = (market_last or {}).get(str(sig.get("market"))) or (market_last or {}).get(country)
        종목끝 = 줄[-1].date if 줄 else d
        끊김 = bool(시장끝) and (
            _date.fromisoformat(str(시장끝)) - _date.fromisoformat(종목끝)
        ).days > SHADOW_ENDED_DAYS
        for rule in sh.RULES:
            out.append(sh.simulate(sid, d, 뒤, rule, costs, country, 앞[-1].close if 앞 else None, ended=끊김))
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


_SAVED_COLS = (
    "SELECT o.stock_id, o.as_of_date, o.horizon, o.entry_date, o.entry_close, o.ret_5d, o.ret_20d,"
    " o.ret_60d, o.max_up, o.max_down, o.hit_target, o.hit_stop, o.bench_ret_20d, o.bench_ret_60d,"
    " o.days_available FROM signal_outcomes o JOIN stocks s ON s.id = o.stock_id"
)
#: 창 안이지만 이번에 다시 채점하지 않고 지난 행을 그대로 두는 종목 (25.1822 — 미국 분할 재수집 대기)
HELD_SQL = _SAVED_COLS + " WHERE s.country = ? AND o.as_of_date >= ? AND o.stock_id IN (SELECT value FROM json_each(?))"


def load_frozen(client: TursoClient, country: str, since: str) -> list[oc.Outcome]:
    """창(`since`) 밖으로 나가 다시 계산하지 않는, 이미 저장된 성적 (25.336). 집계에 함께 넣는다."""
    rs = client.execute(_SAVED_COLS + " WHERE s.country = ? AND o.as_of_date < ?", [country, since])
    return _saved(rs)


def load_held(client: TursoClient, country: str, since: str, ids: set[int]) -> list[oc.Outcome]:
    """창 안에서 이번에 다시 채점하지 않는 종목의 지난 성적 (25.1822). 집계·보정표에 함께 넣는다."""
    if not ids:
        return []
    return _saved(client.execute(HELD_SQL, [country, since, json.dumps(sorted(ids))]))


def _saved(rs: Any) -> list[oc.Outcome]:
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
    client: TursoClient, country: str, rows: list[tuple], stats: list[oc.Stat], now: str, since: str,
    held: set[int] | None = None,
) -> None:
    """창(`since` 이후) 안의 것만 지우고 다시 넣는다. 창 밖의 굳은 성적은 남긴다 (25.336).
    `held` 종목은 창 안이어도 지우지 않는다 — 이번에 다시 채점하지 않은 종목의 지난 행 (25.1822)"""
    if any(len(r) != db.column_count(_OUT_COLS) for r in rows):
        raise ValueError("signal_outcomes 값 묶음과 열 개수가 어긋납니다")
    statements: list[tuple[str, list[Any]]] = [
        (
            "DELETE FROM signal_outcomes WHERE stock_id IN (SELECT id FROM stocks WHERE country = ?)"
            " AND as_of_date >= ? AND stock_id NOT IN (SELECT value FROM json_each(?))",
            [country, since, json.dumps(sorted(held or set()))],
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


def compute(
    client: TursoClient, country: str, today: str, plan: list[pf.PlanFill] | None = None,
    held: set[int] | None = None, shadow: tuple[list[sh.Trade], sh.Costs, dict[str, Any]] | None = None,
) -> tuple[list[oc.Outcome], list[oc.Stat]]:
    """`plan` 을 넘기면 창 안 신호의 분할 계획 체결 성적(docs/signals.md 10.2)을 거기에 더한다.
    굳은 성적은 계획이 없다.

    `held` 종목은 다시 채점하지 않고 지난 행을 집계에 넣는다 (25.1822). 미국 분할 재수집 대기 종목은 일일 배치가
    최근 10일만 분할 반영 값으로 덮어 그 앞은 토요일 재수집까지 분할 전 값이다 — 그대로 채점하면 4:1 분할이
    거짓 −74%·손절 도달로 저장되고 주간 사후 분석 텔레그램에 실렸다(결정 원장 25.1548·예측 성적표 25.1255 와 같은 꼴)"""
    since = since_of(today)
    held = held or set()
    signals = [s for s in load_signals(client, country, since) if int(s["stock_id"]) not in held]
    frozen = load_frozen(client, country, since) + load_held(client, country, since, held)
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
        if plan is not None and (f := pf.evaluate(sig, results[-1], closes.get(sid, []), raw.get(sid))) is not None:
            plan.append(f)
    if shadow is not None:
        _shadow(client, country, signals, 끝날, shadow)
    return results, oc.summarize(results + frozen)


def _shadow(client: TursoClient, country: str, signals: list[dict[str, Any]], 끝날: dict[str, str],
            shadow: tuple[list[sh.Trade], sh.Costs, dict[str, Any]]) -> None:  # fmt: skip
    """그림자 장부 (25.1862) — 같은 신호 목록(분할 재수집 대기는 이미 뺐다)의 **단기 신호만**. 기록 전용 곁다리라
    실패해도 본 성적표를 막지 않는다 (25.1863, 25.1862 교차검증 [필수] — 시세 읽기 예외가 성적표 저장 전에 실행을
    failed 로 닫았다). 까닭은 `shadow[2]['error']` 에 남는다"""
    try:
        단기 = [s for s in signals if str(s.get("horizon")) == "short"]
        if not 단기:
            return
        줄 = load_bars(client, sorted({int(s["stock_id"]) for s in 단기}), min(str(s["as_of_date"]) for s in 단기))
        shadow[2]["bars_rows"] = sum(len(v) for v in 줄.values())  # 읽기 예산 실측(paper.md 12장)
        shadow[0].extend(shadow_trades(단기, 줄, shadow[1], country, 끝날))
    except Exception as exc:  # noqa: BLE001 — 곁다리
        log.warning("그림자 장부 실패: %s", exc)
        shadow[0].clear()
        shadow[2]["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"


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
    # 날짜 평균을 뺀 ρ — 기록만, 판정·화면에 쓰지 않는다 (25.1359, 23회차 P2). 보정표 payload 모양은 그대로
    return [{**cb.calibrate(cb.pairs_for(outcomes, scores, w), w).as_payload(),
             "demeaned": cb.demeaned_check(outcomes, scores, w)} for w in oc.WINDOWS], None  # fmt: skip


#: 그림자 장부 집계를 두는 설정 키 앞부분 — `shadow_ledger_kr`·`shadow_ledger_us`
SHADOW_KEY = "shadow_ledger"


def shadow_costs(client: TursoClient, country: str, today: str) -> sh.Costs:
    """백테스트·가상모의투자와 같은 비용(설정이 있으면 설정, 없으면 기본값). 매도세는 오늘 시행 세율"""
    from batch.services import backtest as bt

    c = bt.Costs.for_country(country, db.get_setting(client, "fees", {}), db.get_setting(client, "taxes", {}))
    # 매도세는 청산일 세율(25.1863) — 일정이 없으면(미국) 오늘 값 하나
    return sh.Costs(c.commission_pct, c.commission_pct, c.tax_at(today), c.slippage_pct, tuple(c.tax_schedule))


def shadow_line(s: dict[str, Any]) -> str:
    """운영 출력 한 줄 — 끝난 거래 수·승률·비용 뒤 평균·중앙값·손익비·진행 중"""
    if not s.get("n"):
        return f"끝난 거래 없음 (진행 {s.get('open', 0)}, 체결 못 함 {s.get('no_entry', 0)})"
    pf_ = s.get("profit_factor")
    return (f"끝 {s['n']}건({s.get('stocks', 0)}종목) · 승률 {s['win_rate'] * 100:.0f}%"
            f" · 비용 뒤 평균 {s['avg_net'] * 100:+.2f}%"
            f" · 중앙 {s['median_net'] * 100:+.2f}% · 손익비 {'-' if pf_ is None else f'{pf_:.2f}'}"
            f" · 평균 {s['avg_sessions']}거래일 · 진행 {s['open']} · 체결 못 함 {s['no_entry']}")  # fmt: skip


def run(market: str) -> int:
    country = "KR" if market.upper() == "KR" else "US"
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date().isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=today)
        try:
            계획: list[pf.PlanFill] = []
            그림자: list[sh.Trade] = []
            그림자_메모: dict[str, Any] = {}
            비용 = shadow_costs(client, country, today)
            # 미국 분할 재수집 대기 종목은 이번에 다시 채점하지 않는다 (25.1822). 못 읽으면 실패로 닫는다 — 거짓 손실을
            # 저장하는 것보다 한 주 미루는 쪽이 덜 틀린다(창 안은 다음 주 실행이 다시 낸다)
            대기 = db.split_pending_us(client, today) if country == "US" else set()
            results, stats = compute(client, country, today, 계획, held=대기, shadow=(그림자, 비용, 그림자_메모))
            now = db.now_iso()
            store(client, country, [to_row(o, now) for o in results], stats, now, since_of(today), held=대기)
            # 점수 보정표 (docs/signals.md 10.1, 25.949) — 굳은 성적까지 포함해 점수와 수익을 짝짓는다
            남긴 = load_frozen(client, country, since_of(today)) + load_held(client, country, since_of(today), 대기)
            보정, 보정_경고 = calibrations(client, country, results + 남긴)
        except Exception as exc:
            if not db.quota_reason(exc):  # 한도는 `guard()` 가 skipped 로 닫는다 (docs/infra.md 25.1223)
                db.finish_batch_run(client, run_id, status="failed", error_text=str(exc))
            raise
        그림자_집계 = {"rules": {r.name: r.__dict__ for r in sh.RULES}, "costs": 비용.__dict__, "as_of": today,
                      "stats": {r.name: sh.summarize(그림자, r.name).as_dict() for r in sh.RULES},
                      **그림자_메모}  # fmt: skip
        그림자_경고 = f"그림자 장부 — {그림자_메모['error']}" if 그림자_메모.get("error") else None
        경고 = [w for w in (보정_경고, 그림자_경고) if w]
        db.finish_batch_run(
            client, run_id, status="success",
            step_log={"signals": len(results), "stats": [(s.horizon, s.window, s.n) for s in stats],
                      "calibration": 보정, "plan_fill": pf.summarize(계획),  # 기록 전용 (signals.md 10.2)
                      "post_touch": oc.summarize_post_touch(results),  # 기록 전용 (signals.md 10.3, 25.1408)
                      "horizon_fit": oc.horizon_fit(results),  # 기록 전용 (signals.md 10.4, 25.1441)
                      **({"split_pending_held": len(대기)} if 대기 else {}),  # 25.1822
                      "shadow": 그림자_집계,  # 그림자 장부 (25.1862)
                      "warnings": 경고},
        )  # fmt: skip
        # 웹·주간 요약이 읽을 자리(나라마다 한 키) — 실행 기록을 닫은 **뒤**, 실패해도 넘어간다(25.1863: 앞에 두면 이
        # 쓰기 하나가 실패해 성공한 실행이 running 으로 남았다). 집계는 실행 기록 step_log.shadow 에도 있다
        try:
            db.set_setting(client, f"{SHADOW_KEY}_{country.lower()}", 그림자_집계)
        except Exception as exc:  # noqa: BLE001 — 곁다리, 경고 로그
            log.warning("그림자 장부 집계를 설정에 적지 못함: %s", exc)
        print(f"신호 성적표 {country}: 신호 {len(results)}건")
        if 대기:
            print(f"  분할 재수집 대기 {len(대기)}종목은 다시 채점하지 않고 지난 성적을 둠")
        for 표 in 보정:
            for 줄 in cb.render_lines(표):
                print(f"  점수 보정 {줄}")
        for 이름, 집계 in 그림자_집계["stats"].items():
            print(f"  그림자 장부 {이름}: {shadow_line(집계)}")
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
