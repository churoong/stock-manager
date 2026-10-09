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
from datetime import UTC, datetime, timedelta
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
#: 참고 점수의 출처 — `scores` 에 쓰지 않으므로 근거표가 `scores` 라고 말하면 거짓이다 (25.1019)
REFERENCE_SOURCE = vd.REFERENCE_SOURCE
#: 이미 유니버스 종목을 "지금 분석" 했는데 의견을 만들지 못했을 때(점수가 없음) — 참고 분석으로 덮지 않는다
IN_UNIVERSE_NOTE = "유니버스 종목인데 점수가 없어 분석 의견을 만들지 못했습니다(다음 일일 배치가 만듭니다)"
STORED_SQL = "SELECT verdict, headline, detail_json FROM stock_verdicts WHERE stock_id = ?"


def universe_verdict(client: TursoClient, country: str, stock_id: int) -> dict | None:
    """유니버스 종목을 "지금 분석" 했을 때 — 그 시장의 종목 분석 의견(`jobs/verdicts`)을 지금 만들고
    이 종목 것을 돌려준다.

    25.1022: 종목 분석 의견이 생긴 날(25.1016) 일일 배치가 아직 안 돌아 유니버스 종목에 의견이 없었는데,
    단추는 "일일 배치의 의견을 그대로 봅니다" 로 끝나 볼 것이 없었다. 의견 계산은 이미 계산된
    점수·신호·판정표를 모을 뿐이라 가볍다."""
    from batch.jobs import verdicts as vj

    vj.run(country)
    r = client.execute(STORED_SQL, [stock_id]).dicts()
    if not r:
        return None
    try:
        detail = json.loads(r[0]["detail_json"] or "{}")
    except (TypeError, ValueError):
        detail = {}
    return {"verdict": r[0]["verdict"], "headline": r[0]["headline"], "against": detail.get("against") or [],
            "outlook": detail.get("outlook")}  # fmt: skip

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
    " s.market_cap_date, u.included, u.exclude_reason, s.listed_shares,"
    " COALESCE(s.name_ko, s.name_en, s.ticker) AS name"
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


#: 유니버스의 저장된 팩터 원값과 종합 점수 (25.1027). 기준일·계산 판을 걸어 인덱스로 읽는다
#: (`idx_factors_date`·`idx_scores_date`)
STORED_FACTORS_SQL = (
    "SELECT f.stock_id, f.factor, f.raw_json, s.market, s.sector FROM factors f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.as_of_date = ? AND f.calc_version = ?"
)
STORED_TOTALS_SQL = (
    "SELECT sc.stock_id, sc.total_score FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = ? AND sc.calc_version = ?"
)


def stored_universe(client: TursoClient, country: str, as_of: str) -> tuple[list[sc.StockInput], list[float]] | None:
    """그날 점수 작업이 저장한 유니버스의 팩터 원값 → z 입력, 그리고 유니버스 종합 점수 (docs/infra.md 25.1027).

    없거나(점수 작업 전), 25.1027 전에 쓴 원값이라 회복 기간 하한이 없으면 None — 부르는 쪽이 재료를 처음부터 읽는다."""
    rows = []
    for r in client.execute(STORED_FACTORS_SQL, [country, as_of, sc.CALC_VERSION]).dicts():
        try:
            rows.append({**r, "raw": json.loads(r["raw_json"] or "{}")})
        except (TypeError, ValueError):
            return None
    if not rows:
        return None
    inputs = sc.inputs_from_stored(rows)
    if inputs is None:
        return None
    점수행 = client.execute(STORED_TOTALS_SQL, [country, as_of, sc.CALC_VERSION]).dicts()
    totals = [float(r["total_score"]) for r in 점수행 if r["total_score"] is not None]
    return inputs, totals


def extra_inputs(client: TursoClient, country: str, as_of: str, extra: list[dict], 등락률: dict) -> list[sc.StockInput]:
    """이 종목들만의 z 입력 — 점수 작업과 같은 로더를 종목을 골라 부른다 (25.1027)."""
    ids = sorted(int(r["stock_id"]) for r in extra)
    days = max(sc.MOMENTUM_OFFSETS) + 1
    series: dict = {}
    since = sj.series_window_start(client, country, as_of, days)
    if since:
        행 = client.execute(EXTRA_SERIES_SQL, [json.dumps(ids), since, as_of]).dicts()
        series = sj.series_from_rows(행, days, 등락률)
    못읽음: list[str] = []
    financials = sj.load_financials(client, country, as_of, stock_ids=ids)
    sj.drop_stale_annual(financials, as_of)
    배당연도: dict[int, int] = {}
    dividends = sj.load_dividends(client, country, as_of, 못읽음, 배당연도, stock_ids=ids)
    if country == "US" and 배당연도:
        sj.fill_us_no_dividend(dividends, 배당연도, financials, as_of)
    성과 = sj.load_metrics(client, country, as_of, stock_ids=ids)
    sj.attach_unrecovered_rows(client, 성과, as_of)
    return sj.build_inputs(
        extra, financials, 성과, series, dividends, sj.load_benchmark_closes(client, country, days, as_of, 못읽음),
        as_of=as_of, moves=등락률, pending_adjust=sj.load_pending_adjust(client) if country == "US" else set(),
    )  # fmt: skip


def full_inputs(client: TursoClient, country: str, as_of: str, extra: list[dict], 등락률: dict) -> list[sc.StockInput]:
    """유니버스 전 종목 + 이 종목들의 재료를 처음부터 읽는다 — 저장된 원값을 쓸 수 없을 때만 (25.1018 의 방식)."""
    universe = sj.load_universe(client, country, as_of)
    if not universe:
        return []
    days = max(sc.MOMENTUM_OFFSETS) + 1
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
    return sj.build_inputs(
        합친, financials, 성과, series, dividends,
        sj.load_benchmark_closes(client, country, days, as_of, 못읽음),
        as_of=as_of, moves=등락률, pending_adjust=sj.load_pending_adjust(client) if country == "US" else set(),
    )  # fmt: skip


def score_extra(client: TursoClient, country: str, as_of: str, extra: list[dict]) -> dict[int, dict]:
    """유니버스 + 이 종목들로 같은 계산을 돌려 **이 종목들의 결과만** 돌려준다.

    {stock_id: {total, factors, rank, ranked, skip_reason, momentum, path}}. rank = 유니버스 종합 점수 가운데
    이보다 높은 수 + 1 ("유니버스 기준 몇 위 상당").

    **유니버스 재료를 다시 읽지 않는다** (docs/infra.md 25.1027, 2026-10-08 사용자 "관심종목 참고 분석 DB 사용량
    줄여줘"). z 는 그 시장 전 종목의 지표 원값으로 내는데, 그 원값은 그날 점수 작업이 `factors.raw_json` 에 이미
    저장했다. 그것과 이 종목만의 재료로 같은 `score_factors` 를 돌린다 — 결과가 처음부터 읽은 것과 같다
    (`tests/test_reference_light_1027.py`). 예전(25.1018)에는 유니버스 전 종목의 시세·재무·성과 지표를 다시 읽어
    한 번에 약 149만 행이었다. 저장된 원값이 없으면(점수 작업 전·25.1027 전 원값) 예전처럼 처음부터 읽는다."""
    extra_ids = {int(r["stock_id"]) for r in extra}
    등락률: dict[int, dict[str, float | None]] = {}
    저장 = stored_universe(client, country, as_of)
    if 저장 is not None:
        유니버스입력, 유니버스점수 = 저장
        inputs = [s for s in 유니버스입력 if s.stock_id not in extra_ids]
        inputs += extra_inputs(client, country, as_of, extra, 등락률)
        경로 = "stored"
    else:
        inputs = full_inputs(client, country, as_of, extra, 등락률)
        유니버스점수 = []
        경로 = "full"
    if not inputs:
        return {}
    weights, sentiment_weight, _ = sj.load_weights(client)
    by_stock: dict[int, dict[str, float | None]] = {}
    모멘텀: dict[int, dict] = {}
    재무원값: dict[int, dict] = {}
    for r in sc.score_factors(inputs):
        by_stock.setdefault(r.stock_id, {})[r.factor] = r.score
        if r.factor == "momentum":
            모멘텀[r.stock_id] = r.raw  # 가격·가치 진단의 현재 주가 위치 (docs/analysis.md 9.1)
        elif r.factor in ("value", "quality", "growth"):
            재무원값.setdefault(r.stock_id, {"as_of": as_of}).update(getattr(r, "raw", None) or {})  # 14·15장
    # 감성도 이 종목들만 — 유니버스 종합 점수는 저장된 것을 쓰니 필요 없다 (25.1028). 처음부터 읽는 길은 나라 전체
    sentiments, _ = sj.load_sentiments(client, country, as_of, stock_ids=None if 경로 == "full" else sorted(extra_ids))
    totals = {sid: sc.total_score(s, weights, sentiment=sentiments.get(sid), sentiment_weight=sentiment_weight)
              for sid, s in by_stock.items() if 경로 == "full" or sid in extra_ids}  # fmt: skip
    if 경로 == "full":
        유니버스점수 = [t.total for sid, t in totals.items() if sid not in extra_ids and t.total is not None]
    out = {}
    for sid in extra_ids:
        t = totals.get(sid)
        if t is None:
            continue
        out[sid] = {"total": t.total, "factors": by_stock.get(sid, {}), "skip_reason": t.skip_reason,
                    "rank": None if t.total is None else 1 + sum(1 for v in 유니버스점수 if v > t.total),
                    "ranked": len(유니버스점수), "as_of": as_of, "path": 경로,
                    "momentum": {**(모멘텀.get(sid) or {}), "as_of": as_of},
                    "fundamentals": 재무원값.get(sid)}  # fmt: skip
    return out


ONE_CLOSE_SQL = (
    "SELECT p.date, CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS close"
    " FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE p.stock_id = ? AND p.date <= ? AND p.close IS NOT NULL"
    " ORDER BY p.date DESC LIMIT 1"
)
ONE_OPINIONS_SQL = "SELECT stock_id, date, broker, target_price FROM kr_opinions WHERE stock_id = ? AND date >= ?"


def reference_outlook(client: TursoClient, country: str, as_of: str, meta: dict, s: dict | None,
                      warnings: list[str]) -> dict:  # fmt: skip
    """참고 분석 종목의 가격·가치 진단 (docs/analysis.md 9장, 25.1023).

    일일 의견(`verdicts.outlook_inputs`)과 같은 재료를 이 종목만 읽는다. 밸류에이션 밴드는 `valuation_bands` 에
    없으니 같은 식(`services/valuation_band.compute`)으로 낸다."""
    from batch.jobs import signals as sig
    from batch.jobs import valuation_bands as vbj
    from batch.services import valuation_band as vb

    sid = int(meta["stock_id"])
    today = cal.user_today()
    c = (client.execute(ONE_CLOSE_SQL, [sid, as_of]).dicts() or [{}])[0]
    band, note = None, None
    시세: list[tuple[str, float]] = []
    try:
        시세 = vbj.load_prices(client, [sid], as_of).get(sid, [])
        r = vb.compute(시세, vbj.load_equities(client, country, as_of, stock_ids=[sid]).get(sid, []),
                       meta.get("listed_shares"))
        if r.skip_reason:
            note = r.skip_reason
        else:
            종가 = dict(시세)
            band = {"p20": r.p20, "p50": r.p50, "p80": r.p80, "current_value": r.current_value,
                    "band_rank": r.band_rank, "price_date": r.price_date, "band_close": 종가.get(str(r.price_date))}
    except Exception as exc:  # noqa: BLE001 — 밴드 줄만 빠진다
        warnings.append(f"가치 밴드를 내지 못했습니다: {exc}")
    try:
        위험 = sig.load_metrics(client, country, as_of, stock_ids=[sid]).get(sid)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"성과 지표를 읽지 못했습니다: {exc}")
        위험 = None
    의견 = []
    증권사성적: dict[str, dict] = {}
    if country == "KR":
        since = (today - timedelta(days=vd.CONSENSUS_DAYS)).isoformat()
        의견 = client.execute(ONE_OPINIONS_SQL, [sid, since]).dicts()
        try:  # 잘 맞힌 증권사 가중 (27장) — 일일 의견과 같은 성적표
            from batch.jobs import verdicts as _vj

            증권사성적 = {str(r["broker"]): r for r in client.execute(_vj.BROKER_STATS_SQL).dicts()}
        except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
            warnings.append(f"증권사 성적표를 읽지 못했습니다: {exc}")
    from batch.jobs import verdicts as vj
    from batch.services import trend

    시장 = vj.market_returns(client, country, as_of, warnings).get(trend.index_for_market(meta.get("market")) or "")
    try:
        무위험 = vj.risk_free(client, country)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"무위험수익률을 읽지 못했습니다: {exc}")
        무위험 = None
    # 비슷한 국면 (13장) — 밴드에 읽은 3년 시세(분할만 반영)로 그 자리에서.
    # 주간 표(`price_patterns`)는 유니버스 종목만이다
    from batch.services import patterns

    m = (s or {}).get("momentum") or {}
    국면 = patterns.pick(patterns.table([str(d) for d, _ in 시세], [float(x) for _, x in 시세]) if 시세 else None,
                       m.get("momentum_3m"), m.get("high_52w_proximity"))  # fmt: skip
    return vd.outlook(close=c.get("close"), close_date=c.get("date"), currency=str(meta.get("currency") or "KRW"),
                      momentum=(s or {}).get("momentum"), risk=위험, band=band, opinions=의견, today=today,
                      band_note=note, market=시장, rf=무위험, analog=국면,
                      fundamentals=(s or {}).get("fundamentals"), broker_stats=증권사성적,
                      vol=patterns.ewma_vol([str(d) for d, _ in 시세[-patterns.EWMA_DAYS :]],
                                            [float(x) for _, x in 시세[-patterns.EWMA_DAYS :]]) if 시세 else None,
                      history=_이력(시세))  # fmt: skip


def _이력(시세: list) -> dict | None:
    """자기 시세 이력의 사실들 (31~34장) — 밴드에 읽은 3년 시세로 그 자리에서. 시장 전체 분포는 없다"""
    from batch.services import history, simulation

    if not 시세:
        return None
    d, c = [str(x) for x, _ in 시세], [float(y) for _, y in 시세]
    return {"drawdown": history.drawdowns(d, c), "tail": history.tail(d, c), "season": history.season(d, c),
            "breakout": history.breakout(d, c)[0],
            # 실제 수익률 경로 (43장) — 3년 시세라 주간 표(5년)보다 짧다. 매물대·움직임 분해는 거래대금·업종이 없어
            # 빠진다
            "boot": simulation.bootstrap(c, seed=0)}  # fmt: skip


def reference_checks(client: TursoClient, country: str, as_of: str, metas: list[dict],
                     점수: dict[int, dict]) -> dict[int, list[dict]]:  # fmt: skip
    """참고 판정표 — 신호 규칙(`services/signals.judgements`)을 이 종목에 그대로 돌린 결과 (docs/analysis.md 8장).

    25.1019. 입력은 일일 신호 작업과 같은 것(최근 시세·연간 재무 성장률·성과 지표·밸류에이션 밴드)을
    **이 종목만** 읽고, 팩터 점수는 참고 점수를 쓴다. `signal_checks`·`signals` 에는 쓰지 않는다
    — 신호(매수 구간·금액)는 유니버스 종목만 낸다.
    {stock_id: [{horizon, passed, failed_count, as_of, rows}]}"""
    from batch.jobs import signals as sig

    후보 = []
    for m in metas:
        s = 점수.get(int(m["stock_id"]))
        if s is None or s.get("total") is None:
            continue
        후보.append({**m, "scores": {k: v for k, v in (s.get("factors") or {}).items()}, "total_score": s["total"],
                     "score_date": s["as_of"]})  # fmt: skip
    if not 후보:
        return {}
    ids = [int(r["stock_id"]) for r in 후보]
    prices = sig.load_recent_prices_for(client, ids, as_of)
    growth = sig.load_growth(client, country, as_of, stock_ids=ids)
    metrics = sig.load_metrics(client, country, as_of, stock_ids=ids)
    bands = {}
    for r in 후보:
        if not sig.needs_band(r["scores"]):
            continue
        sid = int(r["stock_id"])
        band = sig.load_band(client, sid, as_of, r.get("listed_shares"))
        if band is not None:
            bands[sid] = band
            r["_latest_equity"] = sig._latest_equity(client, sid, as_of)
            r["_band_close"] = sig._split_only_close(client, sid, as_of)
    now = db.now_iso()
    out: dict[int, list[dict]] = {}
    for inp in sig.build_inputs(후보, prices, growth, metrics, bands):
        out[inp.stock_id] = [
            {"horizon": t[2], "passed": bool(t[3]), "failed_count": t[4], "as_of": t[1],
             "levels": json.loads(t[8] or "[]"),
             # 팩터 점수 행의 출처를 바로 적는다 — 참고 점수는 `scores` 표에 없다 (25.1019)
             "rows": [{**r, "source": REFERENCE_SOURCE} if r.get("source") == sig.sg.SOURCE_SCORES else r
                      for r in json.loads(t[5])]}
            for t in sig.check_rows(inp, as_of, now)
        ]  # fmt: skip
    return out


def analyze(client: TursoClient, country: str, rows: list[dict], warnings: list[str],
            단계: db.ReadSteps | None = None) -> dict[int, dict]:  # fmt: skip
    """종목들 → {stock_id: verdict 결과}. 저장까지 한다.

    `단계` 를 주면 단계별 읽은 행을 적는다 — 요청 한 번이 149만 행을 읽어(25.1019) 어디서인지 가린다 (25.1020)"""
    단계 = 단계 or db.ReadSteps()
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
    단계.mark("meta")
    if soft:
        warnings += ensure_data(client, country, [r for r in rows if int(r["id"]) not in hard])
    단계.mark("ensure_data")
    점수 = score_extra(client, country, as_of, soft) if soft else {}
    단계.mark("score_extra")
    try:
        판정 = reference_checks(client, country, as_of, soft, 점수)
    except Exception as exc:  # noqa: BLE001 — 판정표가 없어도 참고 점수는 말한다
        판정 = {}
        warnings.append(f"참고 판정표를 내지 못했습니다: {exc}")
    단계.mark("reference_checks")
    진단: dict[int, dict] = {}
    for m in soft:
        try:
            sid = int(m["stock_id"])
            진단[sid] = reference_outlook(client, country, as_of, m, 점수.get(sid), warnings)
        except Exception as exc:  # noqa: BLE001 — 진단이 없어도 참고 점수는 말한다
            warnings.append(f"가격·가치 진단을 내지 못했습니다: {exc}")
    단계.mark("outlook")
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
            checks=판정.get(sid, []),
            excluded_reason=사유,
            outlook=None if sid in hard else 진단.get(sid),
        )  # fmt: skip
        if sid in hard:
            inp.score = {"total": None, "skip_reason": f"유니버스 밖({사유}) — 성격상 점수를 내지 않습니다"}
        res = vd.build(inp)
        res["reasons"] += [a["text"] for a in 곁.get(sid, []) if not a["against"]]
        detail = {k: res[k] for k in ("label", "reasons", "against", "nearest", "outlook")} | {
            "excluded_reason": 사유,
            # 화면의 점수 카드·매수 신호 카드가 이것을 "참고" 로 그린다 (25.1019)
            "reference_score": None if not s or s.get("total") is None else {
                k: s.get(k) for k in ("total", "rank", "ranked", "as_of", "factors")},  # 모멘텀 원값은 outlook 에
            "checks": 판정.get(sid, []),
        }  # fmt: skip
        stmts.append((VERDICT_UPSERT, [sid, country, res["verdict"], res["headline"],
                                       json.dumps(detail, ensure_ascii=False),
                                       json.dumps(res["evidence"], ensure_ascii=False),
                                       None if s is None else s["as_of"], stamp]))  # fmt: skip
        out[sid] = res
    client.batch(stmts)
    단계.mark("verdict")
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
        # 가격·가치 진단 (docs/analysis.md 9장, 25.1023) — 결론 다음에
        for 줄 in ((res.get("outlook") or {}).get("lines") or [])[:4]:
            글 += f"\n· {줄}"
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
        단계 = db.ReadSteps()
        try:
            결과 = analyze(client, country, rows, warnings, 단계)
        except Exception as exc:  # noqa: BLE001 — 요청이면 실패도 알린다
            결과, error = {}, str(exc)[:200]
        if stock_id is not None and not error and stock_id not in 결과 and rows:
            try:
                유니버스 = universe_verdict(client, country, stock_id)
            except Exception as exc:  # noqa: BLE001 — 요청이면 실패도 알린다
                유니버스, error = None, str(exc)[:200]
            if 유니버스 is not None:
                결과[stock_id] = 유니버스
        if stock_id is not None:
            안내 = IN_UNIVERSE_NOTE if not error and stock_id not in 결과 else None
            client.execute(REQUEST_UPDATE, ["failed" if error else "done", db.now_iso(), error or 안내, stock_id])
            if send:
                notify(client, country, rows[0], 결과.get(stock_id), error)
        status = "failed" if error else ("partial" if warnings else "success")
        기록 = {"stocks": len(rows), "analyzed": len(결과), "warnings": warnings[:10], "reads_by_step": 단계.steps}
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
