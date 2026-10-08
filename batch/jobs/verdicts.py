"""종목 분석 의견 — 그 시장의 점수가 있는 종목마다 결론·근거·반대 목소리를 `stock_verdicts` 에 둔다
(docs/analysis.md, docs/infra.md 25.1016).

일일 배치의 매도 플래그 뒤에 돈다(`daily.refresh_portfolio`). 읽기는 그 시장 한 번씩 — 점수·신호·판정표·보유·플래그,
국내면 수급·증권사 의견·최근 공시와 공시 반응 통계. 계산은 `services/verdict.build`.

실행
  python -m batch.jobs.verdicts --market KR
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs import daily
from batch.services import disclosure_reaction as dr
from batch.services import divergence
from batch.services import forecast_track as ft
from batch.services import verdict as vd

JOB_NAME = "verdicts"

SCORES_SQL = (
    "SELECT sc.stock_id, sc.as_of_date, sc.total_score, sc.rank_in_market, sc.factor_scores, sc.skip_reason,"
    " sc.calc_version, s.ticker, COALESCE(s.name_ko, s.name_en, s.ticker) AS name, s.currency, s.market"
    " FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
    " WHERE s.country = ? AND sc.as_of_date = (SELECT MAX(sc2.as_of_date) FROM scores sc2"
    "   JOIN stocks s2 ON s2.id = sc2.stock_id WHERE s2.country = ?)"
    " ORDER BY sc.stock_id, sc.calc_version DESC"
)
SIGNALS_SQL = (
    "SELECT g.stock_id, g.as_of_date AS as_of, g.horizon, g.buy_zone_low, g.buy_zone_high, g.target_price, g.stop_price"
    " FROM signals g JOIN stocks s ON s.id = g.stock_id"
    " WHERE s.country = ? AND g.as_of_date = (SELECT MAX(g2.as_of_date) FROM signals g2"
    "   JOIN stocks s2 ON s2.id = g2.stock_id WHERE s2.country = ?)"
)
CHECKS_SQL = (
    "SELECT c.stock_id, c.as_of_date, c.horizon, c.passed, c.failed_count, c.checks_json"
    " FROM signal_checks c JOIN stocks s ON s.id = c.stock_id"
    " WHERE s.country = ? AND c.as_of_date = (SELECT MAX(c2.as_of_date) FROM signal_checks c2"
    "   JOIN stocks s2 ON s2.id = c2.stock_id WHERE s2.country = ?)"
)
# 가격·가치 진단 재료 (docs/analysis.md 9장, 25.1023). 종가는 밴드와 같은 잣대(분할만 반영, `db.SPLIT_ONLY_PRICE_SQL`)
CLOSE_SQL = (
    "SELECT p.stock_id, p.date,"
    " CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS close"
    " FROM prices p JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND p.date = ?"
)
MOMENTUM_SQL = (
    "SELECT f.stock_id, f.raw_json, f.as_of_date FROM factors f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.factor = 'momentum' AND f.as_of_date = ? ORDER BY f.stock_id, f.calc_version DESC"
)
BAND_SQL = (
    "SELECT v.stock_id, v.p20, v.p50, v.p80, v.current_value, v.band_rank, v.price_date, v.skip_reason,"
    " CASE WHEN s.country = 'US' THEN p.close ELSE COALESCE(p.adj_close, p.close) END AS band_close"
    " FROM valuation_bands v JOIN stocks s ON s.id = v.stock_id"
    " LEFT JOIN prices p ON p.stock_id = v.stock_id AND p.date = v.price_date"
    " WHERE s.country = ? AND v.metric = 'PBR' AND v.as_of_date = (SELECT MAX(v2.as_of_date) FROM valuation_bands v2"
    "   JOIN stocks s2 ON s2.id = v2.stock_id WHERE s2.country = ?) ORDER BY v.stock_id, v.calc_version DESC"
)
OPINIONS_SQL = "SELECT stock_id, date, broker, target_price FROM kr_opinions WHERE date >= ?"
# 예상 주가의 시장 기대수익률 — 지수의 첫·마지막 종가 (docs/analysis.md 10.1, 25.1024). 기본 키 범위라 한 행씩 읽는다
INDEX_FIRST_SQL = (
    "SELECT date, close FROM index_prices WHERE index_code = ? AND date >= ? AND date <= ? ORDER BY date LIMIT 1"
)
INDEX_LAST_SQL = "SELECT date, close FROM index_prices WHERE index_code = ? AND date <= ? ORDER BY date DESC LIMIT 1"


def market_returns(client: TursoClient, country: str, as_of: str, warnings: list[str]) -> dict[str, dict]:
    """그 나라 지수마다 지난 최대 `MARKET_YEARS` 년 연환산 수익률. {지수: market_return(...) + index}"""
    from batch.services import trend

    since = (date.fromisoformat(as_of[:10]) - timedelta(days=round(vd.MARKET_YEARS * 365.25))).isoformat()
    out: dict[str, dict] = {}
    for code in trend.COUNTRY_INDEXES.get(country, ()):
        첫 = _safe(client, INDEX_FIRST_SQL, [code, since, as_of], warnings, f"{code} 지수")
        끝 = _safe(client, INDEX_LAST_SQL, [code, as_of], warnings, f"{code} 지수")
        m = vd.market_return((str(첫[0]["date"]), float(첫[0]["close"])) if 첫 else None,
                             (str(끝[0]["date"]), float(끝[0]["close"])) if 끝 else None)  # fmt: skip
        if m:
            out[code] = {**m, "index": code}
    return out


def risk_free(client: TursoClient, country: str) -> float | None:
    """설정의 무위험수익률 (연, 소수). 없으면 None — 예상 주가는 0 으로 두고 그렇게 적는다."""
    from batch.jobs import metrics

    return metrics.risk_free_for(client, country)

POSITIONS_SQL = (
    "SELECT p.stock_id, p.quantity, p.unrealized_pnl_krw, p.cost_krw, p.price_date FROM positions p"
    " JOIN stocks s ON s.id = p.stock_id WHERE s.country = ? AND p.quantity > 0"
)
FLAGS_SQL = (
    "SELECT f.stock_id, f.level, f.rationale_text, f.as_of_date FROM sell_flags f JOIN stocks s ON s.id = f.stock_id"
    " WHERE s.country = ? AND f.is_active = 1 AND f.as_of_date = (SELECT MAX(as_of_date) FROM sell_flags)"
    " ORDER BY CASE f.level WHEN 'red' THEN 0 WHEN 'yellow' THEN 1 ELSE 2 END"
)
DISCLOSURES_SQL = (
    "SELECT d.stock_id, d.title, d.disclosed_at, d.receipt_no FROM disclosures d JOIN stocks s ON s.id = d.stock_id"
    " WHERE s.country = 'KR' AND d.disclosed_at >= ? ORDER BY d.disclosed_at DESC"
)
REACTION_SQL = "SELECT type, label, n, mean_pct, median_pct, pos_pct FROM disclosure_reaction"
INSERT = (
    "INSERT INTO stock_verdicts (stock_id, market, verdict, headline, detail_json, evidence_json, score_as_of,"
    " signal_as_of, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    # 유니버스에 새로 든 종목의 참고 분석 행을 덮는다 (25.1022)
    " ON CONFLICT (stock_id) DO UPDATE SET market = excluded.market, verdict = excluded.verdict,"
    " headline = excluded.headline, detail_json = excluded.detail_json, evidence_json = excluded.evidence_json,"
    " score_as_of = excluded.score_as_of, signal_as_of = excluded.signal_as_of, computed_at = excluded.computed_at"
)
# 예측 성적표 (docs/analysis.md 11장, 25.1037) — 오늘 낸 예측을 쌓고, 기간이 찬 예측을 실제와 견준다
LOG_INSERT = (
    "INSERT INTO forecast_log (as_of_date, stock_id, country, close, models_json, computed_at)"
    " VALUES (?, ?, ?, ?, ?, ?)"
    " ON CONFLICT (as_of_date, stock_id) DO UPDATE SET close = excluded.close, models_json = excluded.models_json,"
    " computed_at = excluded.computed_at"
)
#: 기간이 찬 예측의 날 — 그 기간 앞(달력)보다 늦지 않은 가장 최근 기록일
LOG_DUE_SQL = "SELECT MAX(as_of_date) FROM forecast_log WHERE country = ? AND as_of_date <= ?"
LOG_ROWS_SQL = "SELECT stock_id, models_json FROM forecast_log WHERE as_of_date = ? AND country = ?"
LOG_FIRST_SQL = "SELECT MIN(as_of_date) FROM forecast_log WHERE country = ?"
#: 종목마다의 누계는 의견 행(`detail_json.track.stock`)이 날마다 이어 간다 —
#: 따로 표를 두면 날마다 종목×모델×기간만큼 쓴다
PRIOR_TRACK_SQL = (
    "SELECT stock_id, json_extract(detail_json, '$.track.stock') AS t FROM stock_verdicts"
    " WHERE market = ? AND json_extract(detail_json, '$.excluded_reason') IS NULL"
)


def track_key(country: str) -> str:
    """시장 누계와 "어디까지 평가했나" 를 두는 설정 열쇠 (배치만 쓰는 기록 키)."""
    return f"forecast_track_{country}"


def evaluate_due(client: TursoClient, country: str, as_of: str, now_close: dict[int, dict],
                 prior: dict[int, dict], state: dict, warnings: list[str]) -> list[str]:  # fmt: skip
    """기간이 찬 예측을 견줘 `prior`(종목 누계)와 `state["market"]`(시장 누계)에 더한다. 평가한 기록일 목록.

    기간마다 "그 기간 앞의 가장 최근 기록일" 하나만 본다 — 같은 날 다시 돌아도 `state["through"]` 가 막아
    두 번 세지 않는다.
    기준 종가는 **지금 계열에서 그날 것을 다시 읽는다**(수정주가가 다시 매겨져도 두 끝이 같은 잣대)."""
    through = state.setdefault("through", {})
    market = state.setdefault("market", {})
    본날: list[str] = []
    for 달 in vd.FORECAST_MONTHS:
        cutoff = ft.months_back(date.fromisoformat(as_of[:10]), 달).isoformat()
        rows = _safe(client, LOG_DUE_SQL, [country, cutoff], warnings, "예측 기록")
        d = rows[0][next(iter(rows[0]))] if rows else None
        if not d or str(d) <= str(through.get(str(달)) or ""):
            continue
        기준 = {int(r["stock_id"]): r for r in _safe(client, CLOSE_SQL, [country, str(d)], warnings, "기준 종가")}
        for r in _safe(client, LOG_ROWS_SQL, [str(d), country], warnings, "예측 기록"):
            sid = int(r["stock_id"])
            b, n = (기준.get(sid) or {}).get("close"), (now_close.get(sid) or {}).get("close")
            if not b or not n:
                continue
            try:
                models = json.loads(r["models_json"] or "{}")
            except (TypeError, ValueError):
                continue
            for model, cell in ft.evaluate(models, 달, float(b), float(n)).items():
                ft.add(prior.setdefault(sid, {}), model, 달, cell)
                ft.add(market, model, 달, cell)
        through[str(달)] = str(d)
        본날.append(f"{달}개월←{d}")
    return 본날


#: 그 시장의 **유니버스 의견만** 지운다 — 참고 분석(`jobs/analyze_extra`, `detail.excluded_reason` 이 있는 행)은
#: 남긴다 (25.1022).
#: 예전엔 시장 전체를 지워, 일일 배치의 참고 분석 단계가 실패하거나 30종목 상한을 넘으면 "지금 분석" 결과가 사라졌다
CLEAR = "DELETE FROM stock_verdicts WHERE market = ? AND json_extract(detail_json, '$.excluded_reason') IS NULL"


def _safe(client: TursoClient, sql: str, args: list, warnings: list[str], what: str) -> list[dict]:
    """곁 재료는 못 읽어도 의견은 낸다 — 표가 없으면(마이그레이션 전) 조용히, 그 밖은 경고."""
    try:
        return client.execute(sql, args).dicts()
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"{what}을(를) 읽지 못했습니다: {exc}")
        return []


def against_kr(client: TursoClient, ids: list[int], today: date, warnings: list[str]) -> dict[int, list[dict]]:
    """국내 반대 목소리와 공시 근거 (docs/analysis.md 5장). 종목 → [{text, evidence, against}]."""
    out: dict[int, list[dict]] = defaultdict(list)
    # 엇갈림 — 리포트 3.9 와 같은 질의·식
    try:
        oid = json.dumps(sorted(ids))
        흐름시작 = (today - timedelta(days=divergence.FLOW_DAYS * 3)).isoformat()
        흐름 = client.execute(daily.DIVERGENCE_FLOWS_SQL, [oid, today.isoformat(), 흐름시작]).rows
        의견 = client.execute(daily.DIVERGENCE_OPINIONS_SQL,
                            [oid, today.isoformat(), (today - timedelta(days=400)).isoformat()]).rows  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        if not db.표가_없나(exc):
            warnings.append(f"수급·의견을 읽지 못했습니다: {exc}")
        흐름, 의견 = [], []
    합: dict[int, list[int]] = {}
    최근날: dict[int, str] = {}
    for sid, d, frgn, orgn in 흐름:  # 종목마다 최근 날부터 (질의가 날짜 내림차순)
        s = 합.setdefault(int(sid), [0, 0, 0])
        최근날.setdefault(int(sid), str(d))
        if s[0] < divergence.FLOW_DAYS:
            s[0] += 1
            s[1] += int(frgn)
            s[2] += int(orgn)
    목표: dict[int, list[tuple[str, str, float | None]]] = defaultdict(list)
    for sid, d, broker, target in 의견:
        목표[int(sid)].append((str(d), str(broker), float(target) if target else None))
    since = (today - timedelta(days=divergence.OPINION_DAYS)).isoformat()
    for sid in ids:
        n, frgn, orgn = 합.get(sid, [0, 0, 0])
        날 = 최근날.get(sid)
        up, down = divergence.target_changes(목표.get(sid, []), since)
        v = divergence.View("", "", n, frgn, orgn, up, down)
        if v.flow_against:
            합계 = f"{(frgn + orgn) / 100:+,.1f}억 ({n}거래일)"
            out[sid].append({"against": True, "text": divergence.against_lines(v)[0], "evidence": vd._row(
                "외국인+기관 순매수 합", 합계, "< 0 이면 반대", "kr_flows (KIS)", 날)})
        if v.opinion_against:
            out[sid].append({"against": True, "text": divergence.against_lines(v)[-1], "evidence": vd._row(
                "증권사 목표가 변경", f"내림 {down}·올림 {up}", "내림 > 올림이면 반대", "kr_opinions (KIS)", since)})
    # 최근 공시 + 그 유형의 과거 반응 — 중앙값이 음수면 반대 목소리, 아니면 근거
    반응 = {str(r["type"]): r for r in _safe(client, REACTION_SQL, [], warnings, "공시 반응 통계")}
    본것: set[tuple[int, str]] = set()
    아는종목 = set(ids)
    시작 = (today - timedelta(days=vd.RECENT_DISCLOSURE_DAYS)).isoformat()
    for r in _safe(client, DISCLOSURES_SQL, [시작], warnings, "최근 공시"):
        sid = int(r["stock_id"])
        kind = dr.classify(str(r["title"]))
        row = 반응.get(kind or "")
        줄 = dr.line(row) if row else None
        if sid not in 아는종목 or not 줄 or (sid, kind) in 본것:
            continue
        본것.add((sid, str(kind)))
        글 = f"최근 공시 \"{r['title']}\" ({r['disclosed_at']}) — {줄}"
        근거 = vd._row("공시", str(r["title"]), "—", f"DART 접수번호 {r['receipt_no']}", str(r["disclosed_at"]))
        out[sid].append({"against": float(row["median_pct"]) < 0, "text": 글, "evidence": 근거})
    return out


def outlook_inputs(client: TursoClient, country: str, as_of: str, today: date, warnings: list[str]) -> dict:
    """가격·가치 진단의 재료를 시장 한 번에 읽는다 (docs/analysis.md 9장). 못 읽은 것은 경고로 — 그 줄만 빠진다."""
    from batch.jobs import signals as sig

    종가 = {int(r["stock_id"]): r for r in _safe(client, CLOSE_SQL, [country, as_of], warnings, "종가")}
    모멘텀: dict[int, dict] = {}
    for r in _safe(client, MOMENTUM_SQL, [country, as_of], warnings, "모멘텀"):
        if int(r["stock_id"]) not in 모멘텀:
            with contextlib.suppress(TypeError, ValueError):
                모멘텀[int(r["stock_id"])] = {**json.loads(r["raw_json"] or "{}"), "as_of": r["as_of_date"]}
    밴드: dict[int, dict] = {}
    for r in _safe(client, BAND_SQL, [country, country], warnings, "밸류에이션 밴드"):
        밴드.setdefault(int(r["stock_id"]), r)
    try:
        위험 = sig.load_metrics(client, country, as_of)
    except Exception as exc:  # noqa: BLE001 — 그 줄만 빠진다
        warnings.append(f"성과 지표를 읽지 못했습니다: {exc}")
        위험 = {}
    의견: dict[int, list[dict]] = defaultdict(list)
    if country == "KR":
        since = (today - timedelta(days=vd.CONSENSUS_DAYS)).isoformat()
        for r in _safe(client, OPINIONS_SQL, [since], warnings, "증권사 목표가"):
            의견[int(r["stock_id"])].append(r)
    try:
        무위험 = risk_free(client, country)
    except Exception as exc:  # noqa: BLE001 — 0 으로 두고 그렇게 적는다
        warnings.append(f"무위험수익률을 읽지 못했습니다: {exc}")
        무위험 = None
    return {"close": 종가, "momentum": 모멘텀, "band": 밴드, "risk": 위험, "opinions": 의견,
            "market": market_returns(client, country, as_of, warnings), "rf": 무위험}  # fmt: skip


def outlook_for(재료: dict, sid: int, currency: str, today: date, market: str | None = None) -> dict:
    from batch.services import trend

    c = 재료["close"].get(sid) or {}
    b = 재료["band"].get(sid)
    return vd.outlook(
        close=c.get("close"), close_date=c.get("date"), currency=currency, momentum=재료["momentum"].get(sid),
        risk=재료["risk"].get(sid), band=b, opinions=재료["opinions"].get(sid, []), today=today,
        band_note=(b or {}).get("skip_reason"),
        market=재료.get("market", {}).get(trend.index_for_market(market) or ""), rf=재료.get("rf"),
    )  # fmt: skip


def build_market(client: TursoClient, market: str, today: date, warnings: list[str]) -> list[tuple[str, list[Any]]]:
    country = "KR" if market == "KR" else "US"
    점수: dict[int, dict] = {}
    for r in client.execute(SCORES_SQL, [country, country]).dicts():
        점수.setdefault(int(r["stock_id"]), r)  # 같은 날 여러 판이면 큰 calc_version (정렬)
    if not 점수:
        return []
    순위있음 = sum(1 for r in 점수.values() if r["total_score"] is not None)
    신호: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, SIGNALS_SQL, [country, country], warnings, "신호"):
        신호[int(r["stock_id"])].append(r)
    판정: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, CHECKS_SQL, [country, country], warnings, "판정표"):
        try:
            rows = json.loads(r["checks_json"] or "[]")
        except (TypeError, ValueError):
            rows = []
        판정[int(r["stock_id"])].append({"horizon": r["horizon"], "as_of": r["as_of_date"], "passed": bool(r["passed"]),
                                       "failed_count": r["failed_count"], "rows": rows})  # fmt: skip
    보유 = {int(r["stock_id"]): r for r in _safe(client, POSITIONS_SQL, [country], warnings, "보유")}
    플래그: dict[int, list[dict]] = defaultdict(list)
    for r in _safe(client, FLAGS_SQL, [country], warnings, "매도 플래그"):
        플래그[int(r["stock_id"])].append({"level": r["level"], "rationale_text": r["rationale_text"],
                                         "as_of": r["as_of_date"]})  # fmt: skip
    곁 = against_kr(client, sorted(점수), today, warnings) if country == "KR" else {}
    as_of = max(str(r["as_of_date"]) for r in 점수.values())
    재료 = outlook_inputs(client, country, as_of, today, warnings)
    # 예측 성적표 (25.1037) — 지난 누계를 읽고 기간이 찬 예측을 더한다
    누계: dict[int, dict] = {}
    for r in _safe(client, PRIOR_TRACK_SQL, [country], warnings, "지난 예측 성적"):
        with contextlib.suppress(TypeError, ValueError):
            if r["t"]:
                누계[int(r["stock_id"])] = json.loads(r["t"])
    상태 = db.get_setting(client, track_key(country), {}) or {}
    평가 = evaluate_due(client, country, as_of, 재료["close"], 누계, 상태, warnings)
    if not 상태.get("since"):
        첫 = _safe(client, LOG_FIRST_SQL, [country], warnings, "예측 기록")
        상태["since"] = (첫[0][next(iter(첫[0]))] if 첫 else None) or as_of
    시장성적 = 상태.get("market") or {}
    # 첫 성적이 나오는 날 — 처음 쌓은 날의 한 달 뒤(가장 짧은 기간)
    첫평가 = ft.months_back(date.fromisoformat(str(상태["since"])[:10]), -vd.FORECAST_MONTHS[0]).isoformat()
    stamp = db.now_iso()
    rows: list[tuple[str, list[Any]]] = [(CLEAR, [country])]
    기록: list[tuple[str, list[Any]]] = []
    for sid, r in 점수.items():
        try:
            factors = json.loads(r["factor_scores"] or "{}")
        except (TypeError, ValueError):
            factors = {}
        p = 보유.get(sid)
        inp = vd.Inputs(
            name=str(r["name"]), ticker=str(r["ticker"]), currency=str(r["currency"] or "KRW"),
            score={"as_of": r["as_of_date"], "total": r["total_score"], "rank": r["rank_in_market"],
                   "ranked": 순위있음, "factors": factors, "skip_reason": r["skip_reason"]},
            signals=신호.get(sid, []), checks=판정.get(sid, []),
            position=None if not p else {
                "quantity": p["quantity"], "price_date": p["price_date"],
                "pnl_pct": (float(p["unrealized_pnl_krw"]) / float(p["cost_krw"]) * 100
                            if p["unrealized_pnl_krw"] is not None and p["cost_krw"] else None)},
            flags=플래그.get(sid, []),
            against=[a for a in 곁.get(sid, []) if a["against"]],
            outlook=outlook_for(재료, sid, str(r["currency"] or "KRW"), today, r.get("market")),
        )  # fmt: skip
        out = vd.build(inp)
        out["reasons"] += [a["text"] for a in 곁.get(sid, []) if not a["against"]]
        out["evidence"] += [a["evidence"] for a in 곁.get(sid, []) if not a["against"]]
        종목성적 = 누계.get(sid) or {}
        for 줄 in (ft.line(종목성적, scope="이 종목"), ft.line(시장성적, scope="시장 전체")):
            if 줄:
                out["reasons"].append(f"예측 성적표: {줄}")
        detail = {k: out[k] for k in ("label", "reasons", "against", "nearest", "outlook")}
        detail["track"] = {"stock": 종목성적, "market": 시장성적, "since": 상태.get("since"), "first_due": 첫평가}
        o = out.get("outlook") or {}
        모델 = ft.log_models(o)
        if 모델 and o.get("close_date") == as_of:
            기록.append((LOG_INSERT, [as_of, sid, country, float(o["close"]),
                                     json.dumps(모델, separators=(",", ":")), stamp]))  # fmt: skip
        신호일 = max((g["as_of"] for g in 신호.get(sid, [])), default=None) or max(
            (c["as_of"] for c in 판정.get(sid, [])), default=None)
        rows.append((INSERT, [
            sid, country, out["verdict"], out["headline"], json.dumps(detail, ensure_ascii=False),
            json.dumps(out["evidence"], ensure_ascii=False), r["as_of_date"], 신호일, stamp,
        ]))  # fmt: skip
    if 평가:
        print(f"예측 성적 평가: {', '.join(평가)}")
    # "어디까지 평가했나" 는 지우기와 같은 첫 묶음에 — 뒤 묶음이 깨지면 그날 평가를 잃을 뿐 **두 번 세지 않는다**
    # (덜 센 성적이 더 센 성적보다 덜 틀리게 읽힌다)
    rows.insert(1, db.setting_statement(track_key(country), 상태))
    return rows + 기록


def run(market: str) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = cal.user_today()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=market, trade_date=today.isoformat())
        warnings: list[str] = []
        rows = build_market(client, market, today, warnings)
        for i in range(0, len(rows), 400):
            client.batch(rows[i : i + 400])
        세기: dict[str, int] = defaultdict(int)
        의견수 = 0
        for sql, args in rows:
            if sql == INSERT:
                의견수 += 1
                세기[str(args[2])] += 1
        기록수 = sum(1 for sql, _ in rows if sql == LOG_INSERT)
        db.finish_batch_run(client, run_id, status="partial" if warnings else "success",
                            step_log={"stocks": 의견수, "by_verdict": dict(세기), "forecast_logged": 기록수,
                                      "warnings": warnings[:10]})  # fmt: skip
        # 결론별 수는 찍지 않는다 — "보유 점검·보유 유지" 수가 보유 종목 수다(공개 로그, 25.979). step_log 에만
        print(f"종목 분석 의견({market}): {의견수}종목 · 예측 기록 {기록수}건")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    return run(parser.parse_args().market)


if __name__ == "__main__":
    sys.exit(guard(main))
