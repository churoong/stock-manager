"""유니버스 밖 종목 참고 분석 (docs/analysis.md 8장, docs/infra.md 25.1018).

2026-10-08 사용자 결정 "B 하고 C 를 채택해서 지금 분석(C)를 실행할 경우 관심종목에 자동으로 추가되고 분석이 완료되면
알람을 가게". 두 길이 이 작업 하나를 쓴다.
  B. 매일 — 일일 배치가 관심 종목 가운데 유니버스 밖인 것을 다시 분석한다(알림 없음)
  C. 지금 — 종목 화면의 "지금 분석" 이 관심 종목에 넣고 `analyze-stock.yml` 을 깨운다(`--stock-id … --notify`)

**유니버스 종목의 점수·추천은 건드리지 않는다.** 그 종목의 재무·성과 지표만 받고, 점수는 "그 시장 유니버스 전 종목 + 이
종목" 으로 같은 계산(`scoring.score_factors`)을 한 번 더 돌려 **이 종목의 결과만** 쓴다. `scores` 에 쓰지 않는다 —
쓰면 추천·순위·스크리너에 섞인다. 결과는 `stock_verdicts` 에 결론 "참고 분석" 으로만 둔다.

성격상 빠진 종목(관리종목·스팩·상장 1년 미만 등, `HARD_REASONS`)은 점수를 내지 않고 "판단 보류(사유)" 로 둔다 —
재무·가격 이력이 점수의 전제와 맞지 않는다.

실행
  python -m batch.jobs.analyze_extra --market KR              # 관심 종목 중 유니버스 밖 전부 (B)
  python -m batch.jobs.analyze_extra --stock-id 123 --notify   # 한 종목, 끝나면 알림 (C)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs import scores as sj
from batch.services import scoring as sc
from batch.services import verdict as vd

log = logging.getLogger("analyze_extra")
JOB_NAME = "analyze_extra"
#: 점수를 내지 않는 제외 사유 — 성격상 점수의 전제(정상 거래·충분한 재무·가격 이력)와 맞지 않는다
HARD_REASONS = ("관리종목", "스팩", "보통주아님", "주권아님", "상장폐지", "상장1년미만", "데이터없음")
#: 한 번에 분석하는 상한 — 관심 종목이 많아도 일일 배치가 길어지지 않게
MAX_STOCKS = 30
#: 이미 유니버스 종목을 "지금 분석" 했을 때 — 참고 분석으로 덮지 않고 이렇게 알린다
IN_UNIVERSE_NOTE = "유니버스 종목이라 일일 배치의 분석 의견을 그대로 봅니다"

TARGETS_SQL = (
    "SELECT s.id, s.ticker, s.country, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.asset_type"
    " FROM watchlist w JOIN stocks s ON s.id = w.stock_id"
    " WHERE s.country = ? AND s.asset_type = 'stock' AND NOT EXISTS (SELECT 1 FROM universe_members u"
    "   WHERE u.stock_id = s.id AND u.included = 1 AND u.snapshot_date = (SELECT MAX(u2.snapshot_date)"
    "   FROM universe_members u2 JOIN stocks s2 ON s2.id = u2.stock_id WHERE s2.country = s.country))"
    " ORDER BY w.added_at LIMIT ?"
)
ONE_SQL = (
    "SELECT id, ticker, country, COALESCE(name_ko, name_en, ticker) AS name, asset_type FROM stocks WHERE id = ?"
)
#: 유니버스 행과 같은 모양(`scores.load_universe`) — 그 종목의 가장 최근 스냅샷 행(제외여도)
EXTRA_UNIVERSE_SQL = (
    "SELECT s.id AS stock_id, s.ticker, s.market, s.sector, u.market_cap, s.currency, u.snapshot_date,"
    " s.market_cap_date, u.included, u.exclude_reason"
    " FROM stocks s LEFT JOIN universe_members u ON u.stock_id = s.id AND u.snapshot_date ="
    "   (SELECT MAX(u2.snapshot_date) FROM universe_members u2 WHERE u2.stock_id = s.id AND u2.snapshot_date <= ?)"
    " WHERE s.id IN (SELECT value FROM json_each(?))"
)
EXTRA_SERIES_SQL = (
    "SELECT p.stock_id, p.date, COALESCE(p.adj_close, p.close) AS px, p.value, p.close, p.volume, p.change_pct"
    " FROM prices p WHERE p.stock_id IN (SELECT value FROM json_each(?)) AND p.date >= ? AND p.date <= ?"
    " AND p.close IS NOT NULL ORDER BY p.stock_id, p.date"
)
VERDICT_UPSERT = (
    "INSERT INTO stock_verdicts (stock_id, market, verdict, headline, detail_json, evidence_json, score_as_of,"
    " signal_as_of, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)"
    " ON CONFLICT (stock_id) DO UPDATE SET market = excluded.market, verdict = excluded.verdict,"
    " headline = excluded.headline, detail_json = excluded.detail_json, evidence_json = excluded.evidence_json,"
    " score_as_of = excluded.score_as_of, signal_as_of = NULL, computed_at = excluded.computed_at"
)
REQUEST_UPDATE = "UPDATE analysis_requests SET status = ?, finished_at = ?, note = ? WHERE stock_id = ?"
ALERT_UPSERT = (
    "INSERT INTO alerts (stock_id, market, trade_date, trigger_type, message, data, created_at, sent_at)"
    " VALUES (?, ?, ?, 'analysis', ?, ?, ?, ?) ON CONFLICT (stock_id, trigger_type, trade_date) DO UPDATE SET"
    " message = excluded.message, data = excluded.data, created_at = excluded.created_at, sent_at = excluded.sent_at"
)


def ensure_data(client: TursoClient, country: str, rows: list[dict]) -> list[str]:
    """그 종목들의 재무·성과 지표를 받는다. 실패는 경고로 — 있는 것으로 분석한다."""
    from batch.jobs import financials, metrics, us_financials

    warnings: list[str] = []
    ids = [int(r["id"]) for r in rows]
    try:
        if country == "KR":
            plan = financials.collection_plan(None, 5, None, datetime.now(UTC).date())
            financials.run(plan, universe_only=False, only_ids=ids)
        else:
            us_financials.run(None, None, only_ids=ids)
    except Exception as exc:  # noqa: BLE001 — 받지 못하면 있는 재무로
        warnings.append(f"재무를 받지 못했습니다: {exc}")
    for r in rows:
        try:
            metrics.run(list(metrics.WINDOW_DAYS), ticker=str(r["ticker"]), countries=(country,))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{r['ticker']} 성과 지표를 내지 못했습니다: {exc}")
    return warnings


def score_extra(client: TursoClient, country: str, as_of: str, extra: list[dict]) -> dict[int, dict]:
    """유니버스 + 이 종목들로 같은 계산을 돌려 **이 종목들의 결과만** 돌려준다.

    {stock_id: {total, factors, rank, ranked, skip_reason}}. rank = 유니버스 종합 점수 가운데 이보다 높은 수 + 1
    ("유니버스 기준 몇 위 상당")."""
    universe = sj.load_universe(client, country, as_of)
    if not universe:
        return {}
    days = max(sc.MOMENTUM_OFFSETS) + 1
    등락률: dict[int, dict[str, float | None]] = {}
    series = sj.load_series(client, country, as_of, days, moves=등락률)
    since = sj.series_window_start(client, country, as_of, days)
    ids = json.dumps(sorted(int(r["stock_id"]) for r in extra))
    if since:
        series.update(sj.series_from_rows(client.execute(EXTRA_SERIES_SQL, [ids, since, as_of]).dicts(), days, 등락률))
    못읽음: list[str] = []
    financials = sj.load_financials(client, country, as_of)
    sj.drop_stale_annual(financials, as_of)
    배당연도: dict[int, int] = {}
    dividends = sj.load_dividends(client, country, as_of, 못읽음, 배당연도)
    if country == "US" and 배당연도:
        sj.fill_us_no_dividend(dividends, 배당연도, financials, as_of)
    성과 = sj.load_metrics(client, country, as_of)
    sj.attach_unrecovered_rows(client, 성과, as_of)
    합친 = universe + [{k: r[k] for k in universe[0]} for r in extra]
    inputs = sj.build_inputs(
        합친, financials, 성과, series, dividends,
        sj.load_benchmark_closes(client, country, days, as_of, 못읽음),
        as_of=as_of, moves=등락률, pending_adjust=sj.load_pending_adjust(client) if country == "US" else set(),
    )  # fmt: skip
    weights, sentiment_weight, _ = sj.load_weights(client)
    by_stock: dict[int, dict[str, float | None]] = {}
    for r in sc.score_factors(inputs):
        by_stock.setdefault(r.stock_id, {})[r.factor] = r.score
    sentiments, _ = sj.load_sentiments(client, country, as_of)
    totals = {sid: sc.total_score(s, weights, sentiment=sentiments.get(sid), sentiment_weight=sentiment_weight)
              for sid, s in by_stock.items()}  # fmt: skip
    extra_ids = {int(r["stock_id"]) for r in extra}
    유니버스점수 = [t.total for sid, t in totals.items() if sid not in extra_ids and t.total is not None]
    out = {}
    for sid in extra_ids:
        t = totals.get(sid)
        if t is None:
            continue
        out[sid] = {"total": t.total, "factors": by_stock.get(sid, {}), "skip_reason": t.skip_reason,
                    "rank": None if t.total is None else 1 + sum(1 for v in 유니버스점수 if v > t.total),
                    "ranked": len(유니버스점수), "as_of": as_of}  # fmt: skip
    return out


def analyze(client: TursoClient, country: str, rows: list[dict], warnings: list[str]) -> dict[int, dict]:
    """종목들 → {stock_id: verdict 결과}. 저장까지 한다."""
    if not rows:
        return {}
    as_of = cal.default_as_of(country)
    ids = json.dumps(sorted(int(r["id"]) for r in rows))
    meta = {int(r["stock_id"]): r for r in client.execute(EXTRA_UNIVERSE_SQL, [as_of, ids]).dicts()}
    # 이미 유니버스 종목이면 일일 배치의 종목 분석 의견이 맡는다 — 참고 분석으로 덮지 않는다
    rows = [r for r in rows if not meta.get(int(r["id"]), {}).get("included")]
    if not rows:
        return {}
    hard = {sid for sid, m in meta.items() if any(h in str(m.get("exclude_reason") or "") for h in HARD_REASONS)}
    soft = [m for sid, m in meta.items() if sid not in hard]
    if soft:
        warnings += ensure_data(client, country, [r for r in rows if int(r["id"]) not in hard])
    점수 = score_extra(client, country, as_of, soft) if soft else {}
    from batch.jobs import verdicts as vj

    보유 = {int(r["stock_id"]): r for r in vj._safe(client, vj.POSITIONS_SQL, [country], warnings, "보유")}
    곁 = vj.against_kr(client, sorted(점수), cal.user_today(), warnings) if country == "KR" and 점수 else {}
    stamp = db.now_iso()
    out: dict[int, dict] = {}
    stmts: list[tuple[str, list[Any]]] = []
    for r in rows:
        sid = int(r["id"])
        m = meta.get(sid, {})
        사유 = str(m.get("exclude_reason") or "유니버스 판정 기록 없음")
        p = 보유.get(sid)
        s = 점수.get(sid)
        손익 = (float(p["unrealized_pnl_krw"]) / float(p["cost_krw"]) * 100
                if p and p["unrealized_pnl_krw"] is not None and p["cost_krw"] else None)
        inp = vd.Inputs(
            name=str(r["name"]), ticker=str(r["ticker"]), currency=str(m.get("currency") or "KRW"),
            score=None if s is None else {**s, "skip_reason": s.get("skip_reason") or (f"유니버스 밖({사유})"
                                                                                       if sid in hard else None)},
            position=None if not p else {"quantity": p["quantity"], "price_date": p["price_date"], "pnl_pct": 손익},
            against=[a for a in 곁.get(sid, []) if a["against"]],
            excluded_reason=사유,
        )  # fmt: skip
        if sid in hard:
            inp.score = {"total": None, "skip_reason": f"유니버스 밖({사유}) — 성격상 점수를 내지 않습니다"}
        res = vd.build(inp)
        res["reasons"] += [a["text"] for a in 곁.get(sid, []) if not a["against"]]
        detail = {k: res[k] for k in ("label", "reasons", "against", "nearest")} | {"excluded_reason": 사유}
        stmts.append((VERDICT_UPSERT, [sid, country, res["verdict"], res["headline"],
                                       json.dumps(detail, ensure_ascii=False),
                                       json.dumps(res["evidence"], ensure_ascii=False),
                                       None if s is None else s["as_of"], stamp]))  # fmt: skip
        out[sid] = res
    client.batch(stmts)
    return out


def notify(client: TursoClient, country: str, row: dict, res: dict | None, error: str | None) -> bool:
    """분석이 끝났다고 알린다 — 텔레그램 + 알림 센터(트리거 `analysis`).

    조용시간이면 저장만 하고 해제 뒤 웹이 보낸다."""
    from batch.notify import telegram
    from batch.services import after_hours as ah

    now = datetime.now(UTC)
    조용, 해제뒤 = ah.quiet_state(client.execute("SELECT value FROM settings WHERE key = 'quiet_hours'").scalar(), now)
    if res is not None:
        글 = f"🔎 분석 완료 — {row['name']}({row['ticker']})\n{res['headline']}"
        if res.get("against"):
            글 += "\n반대 목소리: " + " / ".join(res["against"][:3])
    elif error is None:
        글 = f"🔎 분석 — {row['name']}({row['ticker']}): {IN_UNIVERSE_NOTE}"
    else:
        글 = f"🔎 분석 실패 — {row['name']}({row['ticker']}): {error}"
    sent_at = ah.QUIET_SKIPPED if (조용 and not 해제뒤) else None
    보냄 = False
    if not 조용:
        try:
            telegram.send(글)
            sent_at, 보냄 = now.isoformat(), True
        except Exception as exc:  # noqa: BLE001 — 못 보내면 알림 센터에 남고 웹이 다시 보낸다
            log.warning("분석 완료 알림 실패: %s", type(exc).__name__)
    client.execute(ALERT_UPSERT, [int(row["id"]), country, cal.user_today().isoformat(), 글,
                                  json.dumps({"verdict": (res or {}).get("verdict")}, ensure_ascii=False),
                                  now.isoformat(), sent_at])  # fmt: skip
    return 보냄


def run(market: str | None = None, stock_id: int | None = None, send: bool = False) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        if stock_id is not None:
            rows = client.execute(ONE_SQL, [stock_id]).dicts()
            country = str(rows[0]["country"]) if rows else "KR"
        else:
            country = "KR" if (market or "KR").upper() == "KR" else "US"
            rows = client.execute(TARGETS_SQL, [country, MAX_STOCKS]).dicts()
        # 실패도 기록에 남게 먼저 연다 — 안 남으면 화면이 '안 불렸다' 와 똑같이 본다 (25.1018)
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=country, trade_date=cal.user_today().isoformat())
        if stock_id is not None and (not rows or rows[0]["asset_type"] != "stock"):
            사유 = "분석할 수 없는 종목입니다(없거나 ETF)"
            client.execute(REQUEST_UPDATE, ["failed", db.now_iso(), 사유, stock_id])
            db.finish_batch_run(client, run_id, status="failed", error_text=사유, step_log={"stocks": 0})
            print(사유)
            return 1
        if stock_id is not None:
            client.execute(REQUEST_UPDATE, ["running", None, None, stock_id])
        warnings: list[str] = []
        error = None
        try:
            결과 = analyze(client, country, rows, warnings)
        except Exception as exc:  # noqa: BLE001 — 요청이면 실패도 알린다
            결과, error = {}, str(exc)[:200]
        if stock_id is not None:
            안내 = IN_UNIVERSE_NOTE if not error and stock_id not in 결과 else None
            client.execute(REQUEST_UPDATE, ["failed" if error else "done", db.now_iso(), error or 안내, stock_id])
            if send:
                notify(client, country, rows[0], 결과.get(stock_id), error)
        status = "failed" if error else ("partial" if warnings else "success")
        기록 = {"stocks": len(rows), "analyzed": len(결과), "warnings": warnings[:10]}
        db.finish_batch_run(client, run_id, status=status, error_text=error, step_log=기록)
        print(f"참고 분석({country}): {len(결과)}/{len(rows)}종목" + (f" — 실패 {error}" if error else ""))
        return 1 if error else 0
    finally:
        client.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="유니버스 밖 종목 참고 분석")
    parser.add_argument("--market", choices=["KR", "US"])
    parser.add_argument("--stock-id", type=int)
    parser.add_argument("--notify", action="store_true", help="끝나면 텔레그램·알림 센터로 알린다")
    args = parser.parse_args()
    if args.stock_id is None and args.market is None:
        parser.error("--market 이나 --stock-id 가 필요합니다")
    return run(args.market, args.stock_id, args.notify)


if __name__ == "__main__":
    sys.exit(guard(main))
