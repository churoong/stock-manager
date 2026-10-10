"""우리 점수를 많이 담은 ETF — 가장 새 핵심 판정에 점수 가중평균을 붙인다 (docs/etf.md 11.2·11.3, docs/infra.md 25.967).

흐름
  가장 새 핵심 판정(etf_picks, 나라별 최신 기준일·판)의 행 — 후보 풀은 `in_pool`
    미국 상장 → 그 ETF 의 N-PORT
    국내 상장 미국 지수 → 같은 지수의 미국 ETF(대리, `etf_tilt.PROXY_BY_INDEX`)의 N-PORT
    국내 상장 국내 지수 → 같은 지수의 KODEX ETF 구성종목(25.974, `sources/kodex_pdf`)
  → 미국: CUSIP → 심볼(SEC 결제실패 파일) → 미국 stocks · 국내: 단축코드 → 국내 stocks
  → 그 시장의 가장 새 종합 점수로 상위 비중·가중평균 (시장끼리만 — 국내·미국 점수는 잣대가 다르다)
  → 그 행의 `rationale_data.tilt` 에 적는다 (판정·점수·순위 칸은 건드리지 않는다)

ETF 판정(etf.yml, 월 1회) 뒤에 돈다. SEC 호출은 ETF 하나에 2~3회 + 결제실패 파일 6회 남짓이다.
한 ETF 가 실패하면 그 ETF 만 빠지고 실행은 partial 로 닫는다.

실행
  python -m batch.jobs.etf_tilt
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any

from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import etf as etf_svc
from batch.services import etf_tilt as tilt
from batch.sources import kis, kodex_pdf, sec_edgar, sec_nport

log = logging.getLogger("etf_tilt")
JOB_NAME = "etf_tilt"


def latest_picks(client: TursoClient) -> list[dict[str, Any]]:
    """나라별 가장 새 기준일·가장 높은 판의 핵심 판정 행 — **통과·제외 모두** (후보 풀은 `in_pool` 이 가른다, 11.5)."""
    return client.execute(
        "SELECT pk.id AS pick_id, e.symbol, e.name, e.country, pk.bucket, pk.passed, pf.category, pk.as_of_date,"
        " pf.stock_position, pf.total_assets"
        " FROM etf_picks pk JOIN etfs e ON e.id = pk.etf_id"
        " LEFT JOIN etf_profiles pf ON pf.etf_id = pk.etf_id AND pf.as_of_date = pk.as_of_date"
        " WHERE pk.as_of_date = (SELECT MAX(p2.as_of_date) FROM etf_picks p2 JOIN etfs e2 ON e2.id = p2.etf_id"
        "                        WHERE e2.country = e.country)"
        "   AND pk.calc_version = (SELECT MAX(p3.calc_version) FROM etf_picks p3 JOIN etfs e3 ON e3.id = p3.etf_id"
        "                          WHERE e3.country = e.country AND p3.as_of_date = pk.as_of_date)"
    ).dicts()


def kr_name_excluded(name: str) -> bool:
    """국내 핵심 판정(`etf.evaluate_kr`)과 같은 이름 거르기 — 레버리지·인버스·합성·커버드콜·버퍼·옵션·배수 표기."""
    folded = name.casefold()
    if any(marker.casefold() in folded for marker, _ in etf_svc.KR_NAME_EXCLUSIONS):
        return True
    return etf_svc.name_looks_leveraged(name) or etf_svc.KR_MULTIPLE_PATTERN.search(name) is not None


def kr_proxy_ok(pick: dict[str, Any]) -> bool:
    """국내 상장 행이 대리 보유를 쓸 수 있는 상품인가.

    이름 제외(25.970)·액티브·선물(25.971)·순자산 100억원(또는 핵심 통과)."""
    name = str(pick.get("name") or "")
    if kr_name_excluded(name) or any(m in name for m in tilt.PROXY_EXCLUDED_NAME_MARKERS):
        return False
    assets = pick.get("total_assets")
    return bool(pick.get("passed")) or (isinstance(assets, int | float) and assets >= tilt.POOL_MIN_ASSETS_KRW)


def kodex_by_index(picks: list[dict[str, Any]], kodex_tickers: set[str]) -> dict[str, str]:
    """국내 지수 → 그 지수를 따르는 KODEX ETF(대리 보유로 쓸 것) 단축코드 (25.974).

    같은 지수의 KODEX 가 여럿이면(환헤지·TR 등) 대리 조건을 통과한 것 가운데 순자산이 가장 큰 것.
    미국 지수(대리 표)는 N-PORT 가 맡으므로 여기서 다루지 않는다."""
    best: dict[str, tuple[float, str]] = {}
    for p in picks:
        index = str(p.get("category") or "")
        if p["country"] != "KR" or not index or index in tilt.PROXY_BY_INDEX:
            continue
        if str(p["symbol"]) not in kodex_tickers or not kr_proxy_ok(p):
            continue
        assets = float(p.get("total_assets") or 0.0)
        if index not in best or assets > best[index][0]:
            best[index] = (assets, str(p["symbol"]))
    return {index: sym for index, (_, sym) in best.items()}


def in_pool(pick: dict[str, Any], kodex_index: dict[str, str] | None = None) -> bool:
    """점수 담음을 낼 행인가 (docs/etf.md 11.5·11.6, 25.968·25.970·25.974).

    미국: 핵심 통과이거나, **넓은 지수가 아니어도** 주식 비중 0.8 이상·순자산 1억달러 이상·레버리지·인버스 아님·
    옵션 전략 분류 아님(25.969).
    국내: 대리 보유가 있는 지수 — 미국 지수는 같은 지수의 미국 ETF, 국내 지수는 같은 지수의 KODEX(`kodex_index`)."""
    if pick["country"] != "US":
        index = str(pick.get("category") or "")
        if index not in tilt.PROXY_BY_INDEX and index not in (kodex_index or {}):
            return False
        return kr_proxy_ok(pick)
    if pick.get("passed"):
        return True
    category = str(pick.get("category") or "")
    leveraged = etf_svc.name_looks_leveraged(str(pick.get("name") or ""))
    if etf_svc.name_looks_option(str(pick.get("name") or "")):  # 분류가 Large Blend 인 커버드콜·버퍼 (25.1107)
        return False
    if category.startswith(etf_svc.TRADING_CATEGORY_PREFIX) or leveraged:
        return False
    if category in tilt.POOL_EXCLUDED_CATEGORIES:  # 옵션 전략 상품 (25.969)
        return False
    stock, assets = pick.get("stock_position"), pick.get("total_assets")
    return (
        isinstance(stock, int | float) and stock >= tilt.POOL_MIN_STOCK_POSITION
        and isinstance(assets, int | float) and assets >= tilt.POOL_MIN_ASSETS_USD
    )  # fmt: skip


def source_of(pick: dict[str, Any], kodex_index: dict[str, str] | None = None) -> tuple[str, str] | None:
    """이 행의 보유를 어디서 보나 — ("us", 미국 ETF 심볼) 또는 ("kr", KODEX 단축코드). 풀 밖이면 None."""
    if not in_pool(pick, kodex_index):
        return None
    if pick["country"] == "US":
        sym = str(pick["symbol"]).upper()
        return ("us", tilt.US_UIT_PROXY.get(sym, sym))  # 단위형 신탁은 같은 지수 ETF 보유로 (25.982)
    index = str(pick.get("category") or "")
    if index in tilt.PROXY_BY_INDEX:
        return ("us", tilt.PROXY_BY_INDEX[index])
    return ("kr", (kodex_index or {})[index])


def source_symbol(pick: dict[str, Any]) -> str | None:
    """미국 N-PORT 경로만 볼 때의 심볼 (예전 호출부·테스트용)."""
    src = source_of(pick)
    return src[1] if src and src[0] == "us" else None


def load_stocks(client: TursoClient, country: str = "US") -> dict[str, tuple[int, str, str]]:
    """정규화 심볼 → (stock_id, 표시 심볼, 이름). ticker 와 yahoo_symbol 둘 다 키로 둔다.

    국내는 단축코드가 ticker 다."""
    out: dict[str, tuple[int, str, str]] = {}
    for r in client.execute(
        "SELECT id, ticker, yahoo_symbol, COALESCE(name_ko, name_en, ticker) AS name FROM stocks"
        " WHERE country = ? AND asset_type = 'stock'",
        [country],
    ).dicts():
        for key in (r["ticker"], r["yahoo_symbol"]):
            if key:
                out.setdefault(tilt.norm_symbol(str(key)), (int(r["id"]), str(r["ticker"]), str(r["name"])))
    return out


def load_scores(client: TursoClient, country: str = "US") -> tuple[dict[int, float], str | None]:
    """그 시장의 가장 새 점수 기준일의 종합 점수(그날 가장 높은 판). 반환 (stock_id → 점수, 기준일)."""
    as_of = client.execute(
        "SELECT MAX(sc.as_of_date) FROM scores sc JOIN stocks s ON s.id = sc.stock_id WHERE s.country = ?", [country]
    ).scalar()
    if not as_of:
        return {}, None
    best: dict[int, tuple[int, float]] = {}
    for r in client.execute(
        "SELECT sc.stock_id, sc.calc_version, sc.total_score FROM scores sc JOIN stocks s ON s.id = sc.stock_id"
        " WHERE s.country = ? AND sc.as_of_date = ? AND sc.total_score IS NOT NULL",
        [country, str(as_of)],
    ).dicts():
        sid, ver = int(r["stock_id"]), int(r["calc_version"])
        if sid not in best or ver > best[sid][0]:
            best[sid] = (ver, float(r["total_score"]))
    return {sid: v for sid, (_, v) in best.items()}, str(as_of)


def load_long_signals(client: TursoClient, country: str = "US") -> tuple[set[int], str | None]:
    """그 시장의 가장 새 장기 신호 종목 — 추천 비중(R, 참고)에만 쓴다."""
    as_of = client.execute(
        "SELECT MAX(sg.as_of_date) FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = ? AND sg.horizon = 'long'",
        [country],
    ).scalar()
    if not as_of:
        return set(), None
    ids = client.execute(
        "SELECT DISTINCT sg.stock_id FROM signals sg JOIN stocks s ON s.id = sg.stock_id"
        " WHERE s.country = ? AND sg.horizon = 'long' AND sg.as_of_date = ?",
        [country, str(as_of)],
    ).rows
    return {int(r[0]) for r in ids}, str(as_of)


@dataclass(frozen=True)
class Fetched:
    """한 출처의 보유 — 미국 N-PORT 든 KODEX 구성종목이든 같은 모양으로 맞춘다."""

    holdings: list[tuple[str, float]]  # (CUSIP 또는 단축코드, 비중 %)
    accession: str  # 근거표의 "어느 문서" — N-PORT 공시 번호 또는 "KODEX {fId} {기준일}"
    filed: str
    report_date: str | None
    source: str


@dataclass(frozen=True)
class Market:
    """한 시장의 점수 맥락 — 국내·미국 점수는 잣대가 달라 시장끼리만 쓴다."""

    key_to_symbol: dict[str, str]
    stocks: dict[str, tuple[int, str, str]]
    scores: dict[int, float]
    score_as_of: str | None
    rec_ids: set[int] | None
    signals_as_of: str | None
    top_ids: set[int]
    top_cut: float | None
    benchmark: str  # 시장 대비의 기준 ETF


def load_market(client: TursoClient, country: str, key_to_symbol: dict[str, str], benchmark: str) -> Market:
    stocks = load_stocks(client, country)
    scores, score_as_of = load_scores(client, country)
    rec_ids, signals_as_of = load_long_signals(client, country)
    top_ids, top_cut = tilt.top_ids_of(scores)
    # 국내는 보유 키가 단축코드 그대로라 이름표가 필요 없다 — 종목 표의 키를 그대로 쓴다
    mapping = key_to_symbol if key_to_symbol else {k: k for k in stocks}
    return Market(mapping, stocks, scores, score_as_of, rec_ids if signals_as_of else None, signals_as_of,
                  top_ids, top_cut, benchmark)  # fmt: skip


def build_payload(
    t: tilt.Tilt, doc: Fetched, src: str, proxy: bool, market: Market, bench: tilt.Tilt | None,
    rank: tuple[int, int] | None, rank_all: tuple[int, int] | None, group: str, pool: str,
) -> dict[str, Any]:
    bench_avg = bench.avg_score if bench else None
    vs = None if t.avg_score is None or bench_avg is None else t.avg_score - bench_avg
    return {
        **t.as_dict(),
        "vs_market": vs,
        "benchmark": tilt.KR_BENCHMARK_NAME if market.benchmark == tilt.KR_BENCHMARK else market.benchmark,
        "rank": rank[0] if rank else None,
        "rank_size": rank[1] if rank else None,
        "rank_all": rank_all[0] if rank_all else None,
        "rank_all_size": rank_all[1] if rank_all else None,
        "market_top_pct": bench.top_pct if bench and bench.avg_score is not None else None,
        "top_share_pct": tilt.TOP_SHARE_PCT,
        "top_cut_score": market.top_cut,
        "source_symbol": src,
        "proxy": proxy,
        "accession": doc.accession,
        "filed": doc.filed,
        "report_date": doc.report_date,
        "score_as_of": market.score_as_of,
        "signals_as_of": market.signals_as_of,
        "coverage_min_pct": tilt.COVERAGE_MIN_PCT,
        "source": doc.source,
        # us = 미국 상장 · kr_us = 국내 상장 미국 지수 · kr_kr = 국내 상장 국내 지수 — 순위는 이 무리 안에서 (25.974)
        "group": group,
        "pool": pool,
    }


def fetch_us(targets: list[str], failed: dict[str, str]) -> tuple[dict[str, Fetched], dict[str, str], int]:
    """미국 N-PORT. 반환 (심볼 → 보유, CUSIP → 심볼, SEC 호출 수)."""
    if not targets:
        return {}, {}, 0
    sec = sec_edgar.SecClient()
    nport = sec_nport.NportClient(sec)
    funds = nport.fund_tickers()
    cusips = nport.ftd_cusips(date.today())
    out: dict[str, Fetched] = {}
    for sym in targets:
        if sym not in funds:
            failed[sym] = "SEC 펀드 티커 목록에 없음"
            continue
        try:
            doc = nport.latest(*funds[sym])
        except (sec_edgar.SecFailed, sec_edgar.SecBlocked) as exc:
            failed[sym] = str(exc)
            if isinstance(exc, sec_edgar.SecBlocked):
                break  # 계속 부르면 더 오래 막힌다
            continue
        if doc is None:
            failed[sym] = "NPORT-P 공시 없음"
            continue
        out[sym] = Fetched([(h.cusip, h.pct) for h in doc.holdings], doc.accession, doc.filed, doc.report_date,
                           sec_nport.SOURCE)  # fmt: skip
    return out, cusips, sec.calls


def fetch_kr(
    client: kodex_pdf.KodexClient, products: dict[str, kodex_pdf.Product], targets: list[str], base: date,
    failed: dict[str, str],
) -> dict[str, Fetched]:
    """KODEX 구성종목 (25.974). 반환 단축코드 → 보유."""
    out: dict[str, Fetched] = {}
    for sym in targets:
        product = products.get(sym)
        if product is None:
            failed[sym] = "KODEX 상품 목록에 없음"
            continue
        try:
            doc = client.pdf(product, base)
        except kodex_pdf.KodexBlocked as exc:
            # 쉬었다 물어도 막혔다 — 남은 것은 부르지 않는다 (25.984). 계속 부르면 더 오래 막힌다
            for rest in targets[targets.index(sym):]:
                failed.setdefault(rest, f"KODEX 429 로 이번 실행 멈춤 ({exc})")
            break
        except (kodex_pdf.KodexFailed, OSError) as exc:  # requests 의 접속 오류도 OSError 계열이다
            failed[sym] = str(exc)
            continue
        if doc is None:
            failed[sym] = f"KODEX 구성종목이 {kodex_pdf.BACK_DAYS}일 동안 비었음"
            continue
        out[sym] = Fetched([(h.code, h.pct) for h in doc.holdings], f"KODEX {doc.fid} {doc.base_date}",
                           doc.base_date, doc.base_date, kodex_pdf.SOURCE)  # fmt: skip
    return out


def fetch_kr_kis(client: TursoClient, targets: list[str], out: dict[str, Fetched], failed: dict[str, str]) -> int:
    """KODEX 홈페이지에서 못 받은 국내 ETF 를 KIS 구성종목 **상위 30** 으로 (25.990). 받은 것은 `failed` 에서 지운다.

    상위 30 이라 코스피200 ETF 는 비중 85% 쯤만 덮는다 — 덮은 비중(`coverage_pct`)이 그대로 근거표에 남고,
    하한(70%) 아래면 평균을 내지 않는다. 키가 없거나 토큰을 못 받으면 아무것도 하지 않는다. 반환: 나간 호출 수."""
    if not targets or not kis.configured():
        return 0
    try:
        token, issued = kis.access_token(client)
    except (kis.KisFailed, OSError, KeyError) as exc:
        for sym in targets:
            failed.setdefault(sym, f"KIS 토큰 실패 ({exc})")
        return 0
    qc = kis.QuoteClient(token)
    오늘 = date.today().isoformat()
    for sym in targets:
        try:
            got = qc.etf_components(sym)
        except (kis.KisFailed, OSError) as exc:
            failed[sym] = f"{failed.get(sym, '')} · KIS 도 실패 ({exc})".lstrip(" ·")
            continue
        if not got:
            failed[sym] = f"{failed.get(sym, '')} · KIS 구성종목 비어 있음".lstrip(" ·")
            continue
        out[sym] = Fetched(got, f"KIS 구성종목 상위 {len(got)} {오늘}", 오늘, 오늘, kis.SOURCE)
        failed.pop(sym, None)
    return qc.calls + int(issued)


def needs_kodex(picks: list[dict[str, Any]]) -> bool:
    """국내 지수 ETF 후보가 하나라도 있으면 KODEX 목록을 받는다 — 없으면 부르지 않는다."""
    return any(
        p["country"] == "KR" and p.get("category") and str(p["category"]) not in tilt.PROXY_BY_INDEX
        and kr_proxy_ok(p)
        for p in picks
    )  # fmt: skip


HELD_ETFS_SQL = (
    "SELECT s.id, s.ticker, s.country FROM positions p JOIN stocks s ON s.id = p.stock_id"
    " WHERE s.asset_type = 'etf' AND p.quantity > 0"
)
LOOKTHROUGH_INSERT = (
    "INSERT INTO etf_lookthrough (etf_stock_id, stock_id, weight_pct, as_of, basis, source, fetched_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
)


def store_lookthrough(
    client: TursoClient, picks: list[dict[str, Any]], sources: dict[int, tuple[str, str]],
    docs: dict[tuple[str, str], Fetched], markets: dict[str, Market],
) -> dict[str, Any]:  # fmt: skip
    """보유 중인 ETF 의 구성종목 비중을 `etf_lookthrough` 에 남긴다 (docs/portfolio.md 8장, 25.1002).

    이번에 문서를 받은 보유 ETF 만 다시 쓴다 — 못 받은 것은 지난 값(기준일이 함께 있다)을 둔다. 더는 들고 있지 않은
    ETF 의 행은 지운다. 판정 행에 없는 ETF(후보 풀 밖)는 구성을 모른다."""
    held = client.execute(HELD_ETFS_SQL).rows
    by_symbol = {(str(p["country"]), str(p["symbol"]).upper()): int(p["pick_id"]) for p in picks}
    stmts: list[tuple[str, list[Any]]] = [
        ("DELETE FROM etf_lookthrough WHERE etf_stock_id NOT IN (SELECT p.stock_id FROM positions p"
         " JOIN stocks s ON s.id = p.stock_id WHERE s.asset_type = 'etf' AND p.quantity > 0)", []),
    ]  # fmt: skip
    written, missing = 0, []
    stamp = db.now_iso()
    for sid, ticker, country in held:
        pid = by_symbol.get((str(country), str(ticker).upper()))
        src = sources.get(pid) if pid is not None else None
        doc = docs.get(src) if src else None
        if src is None or doc is None:
            missing.append(str(ticker))
            continue
        kind, sym = src
        w = tilt.lookthrough_weights(doc.holdings, markets[kind].key_to_symbol, markets[kind].stocks)
        basis = doc.accession + (f" (대리 {sym})" if sym != str(ticker).upper() else "")
        stmts.append(("DELETE FROM etf_lookthrough WHERE etf_stock_id = ?", [int(sid)]))
        as_of = doc.report_date or doc.filed
        rows = [[int(sid), k, round(v, 4), as_of, basis, doc.source, stamp] for k, v in w.items()]
        stmts += [(LOOKTHROUGH_INSERT, r) for r in rows]
        written += 1
    for i in range(0, len(stmts), 500):
        client.batch(stmts[i : i + 500])
    return {"held": len(held), "written": written, "missing": missing}


def run() -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        picks = latest_picks(client)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market=None, trade_date=max((str(p["as_of_date"]) for p in picks), default=None)
        )
        if not picks:
            db.finish_batch_run(client, run_id, status="skipped", error_text="통과한 핵심 판정이 없습니다")
            return 0
        failed: dict[str, str] = {}

        # 국내 지수 → KODEX (25.974). 목록을 못 받으면 국내 지수 ETF 만 빠지고 나머지는 돈다
        kodex = kodex_pdf.KodexClient()
        products: dict[str, kodex_pdf.Product] = {}
        if needs_kodex(picks):
            try:
                products = {p.ticker: p for p in kodex.products()}
            except (kodex_pdf.KodexFailed, OSError) as exc:
                failed["KODEX 목록"] = str(exc)
        # 목록을 못 받았으면(429 등) 이름으로 KODEX 를 가려 KIS 로 대신 받는다 (25.990)
        kodex_tickers = set(products) or {str(p["symbol"]) for p in picks
                                          if p["country"] == "KR" and str(p.get("name") or "").startswith("KODEX")}
        kodex_index = kodex_by_index(picks, kodex_tickers)
        sources = {int(p["pick_id"]): src for p in picks if (src := source_of(p, kodex_index))}

        us_targets = sorted({s for k, s in sources.values() if k == "us"} | {tilt.MARKET_BENCHMARK})
        # 기준(KODEX 200)을 **맨 앞에** — 429 로 중간에 멈춰도 "시장 대비" 의 기준은 받는다 (25.984)
        kr_targets = sorted({s for k, s in sources.values() if k == "kr"} - {tilt.KR_BENCHMARK})
        if tilt.KR_BENCHMARK in kodex_tickers:
            kr_targets.insert(0, tilt.KR_BENCHMARK)
        us_docs, cusips, sec_calls = fetch_us(us_targets, failed)
        kr_base = max((date.fromisoformat(str(p["as_of_date"])[:10]) for p in picks if p["country"] == "KR"),
                      default=date.today())  # fmt: skip
        kr_docs = fetch_kr(kodex, products, kr_targets, kr_base, failed) if kr_targets and products else {}
        # KODEX 홈페이지에서 못 받은 것은 KIS 상위 30 으로 (25.990)
        kis_calls = fetch_kr_kis(client, [s for s in kr_targets if s not in kr_docs], kr_docs, failed)
        if sec_calls:
            db.record_api_call(client, "sec_edgar", count=sec_calls)
        if kodex.calls:
            db.record_api_call(client, "samsungfund_kodex", count=kodex.calls)
        if kis_calls:
            db.record_api_call(client, "kis_openapi", count=kis_calls)

        markets = {
            "us": load_market(client, "US", cusips, tilt.MARKET_BENCHMARK),
            "kr": load_market(client, "KR", {}, tilt.KR_BENCHMARK),
        }
        docs = {("us", s): d for s, d in us_docs.items()} | {("kr", s): d for s, d in kr_docs.items()}
        tilts = {
            key: tilt.compute(d.holdings, markets[key[0]].key_to_symbol, markets[key[0]].stocks,
                              markets[key[0]].scores.get, markets[key[0]].rec_ids, markets[key[0]].top_ids)
            for key, d in docs.items()
        }  # fmt: skip
        benches = {"us": tilts.get(("us", tilt.MARKET_BENCHMARK)), "kr": tilts.get(("kr", tilt.KR_BENCHMARK))}

        def group_of(p: dict[str, Any]) -> str:
            return "us" if p["country"] == "US" else f"kr_{sources[int(p['pick_id'])][0]}"

        # 분류 안 순위는 미국 핵심 통과끼리 (11.2)
        groups: dict[str, list[tuple[int, float | None]]] = {}
        for p in picks:
            src = sources.get(int(p["pick_id"]))
            if p["country"] == "US" and p.get("passed") and src in tilts:
                groups.setdefault(str(p.get("category") or ""), []).append((int(p["pick_id"]), tilts[src].avg_score))
        ranks = tilt.rank_within(groups)
        # 전체 순위 — 무리마다 따로(미국 상장 · 국내 상장 미국 지수 · 국내 상장 국내 지수) 상위 비중으로 (11.5·11.6)
        ranks_all: dict[int, tuple[int, int]] = {}
        for g in ("us", "kr_us", "kr_kr"):
            whole = [
                (int(p["pick_id"]), tilts[src].top_pct if tilts[src].avg_score is not None else None,
                 float(p.get("total_assets") or 0.0))
                for p in picks
                if (src := sources.get(int(p["pick_id"]))) in tilts and group_of(p) == g
            ]  # fmt: skip
            ranks_all.update(tilt.rank_by(whole))

        updates = []
        for p in picks:
            pid = int(p["pick_id"])
            src = sources.get(pid)
            if src not in tilts:
                continue
            kind, sym = src
            payload = build_payload(
                tilts[src], docs[src], sym, sym != str(p["symbol"]).upper(), markets[kind], benches[kind],
                ranks.get(pid), ranks_all.get(pid), group_of(p), "core" if p.get("passed") else "wide",
            )
            updates.append((
                "UPDATE etf_picks SET rationale_data = json_set(rationale_data, '$.tilt', json(?)) WHERE id = ?",
                [json.dumps(payload, ensure_ascii=False), pid],
            ))
        # **이번에 다시 내지 않은 행의 옛 tilt 를 지운다** (25.969). 풀에서 빠진 ETF(옵션 전략 등)나 이번에 보유를
        # 못 받은 ETF 에 지난 실행의 순위가 남으면 화면이 그 순위를 그대로 읽는다
        갱신 = {int(args[1]) for _, args in updates}
        지우기 = [
            (
                "UPDATE etf_picks SET rationale_data = json_remove(rationale_data, '$.tilt') WHERE id = ?",
                [int(p["pick_id"])],
            )
            for p in picks
            if int(p["pick_id"]) not in 갱신
        ]
        if updates or 지우기:
            client.batch(지우기 + updates)

        # 계좌 전체 노출용 — 보유 ETF 의 구성 (25.1002). 실패해도 점수 쪽은 이미 적었다
        try:
            투시 = store_lookthrough(client, picks, sources, docs, markets)
        except Exception as exc:  # noqa: BLE001 — 곁다리
            투시 = {"error": str(exc)}
            failed["보유 ETF 구성 저장"] = str(exc)
        step = {
            "lookthrough": 투시,
            "picks": len(picks),
            "targets": len(us_targets) + len(kr_targets),
            "fetched": len(docs),
            "updated": len(updates),
            "with_avg": sum(1 for t in tilts.values() if t.avg_score is not None),
            "cusips": len(cusips),
            "kodex_products": len(products),
            "kodex_index": len(kodex_index),
            "score_as_of": {k: m.score_as_of for k, m in markets.items()},
            "benchmark_top_pct": {k: (b.top_pct if b else None) for k, b in benches.items()},
            "top_cut_score": {k: m.top_cut for k, m in markets.items()},
            "leaders": {
                g: [
                    (p["symbol"], round(tilts[sources[int(p["pick_id"])]].top_pct, 1))
                    for p in sorted(
                        (p for p in picks if int(p["pick_id"]) in ranks_all and group_of(p) == g),
                        key=lambda p: ranks_all[int(p["pick_id"])][0],
                    )[:10]
                ]
                for g in ("us", "kr_us", "kr_kr")
            },
            "failed": failed,
            "sec_calls": sec_calls,
            "kodex_calls": kodex.calls,
            "kis_calls": kis_calls,
            "kis_docs": sorted(s for s, d in kr_docs.items() if d.source == kis.SOURCE),
            "coverage": {f"{k}:{s}": round(t.coverage_pct, 1) for (k, s), t in sorted(tilts.items())},
        }
        db.finish_batch_run(
            client, run_id, status="partial" if failed else "success", step_log=step,
            error_text=f"보유를 받지 못한 ETF {len(failed)}개: {', '.join(sorted(failed))}" if failed else None,
        )  # fmt: skip
        print(f"ETF 점수 담음: 대상 {step['targets']} · 받음 {len(docs)} · 행 갱신 {len(updates)}")
        print(f"  실패 {failed or '없음'}")
        for (k, s), t in sorted(tilts.items()):
            a = "-" if t.avg_score is None else f"{t.avg_score:.1f}"
            print(f"  {k}:{s}: A {a} · T {t.top_pct:.1f}% · 덮은 {t.coverage_pct:.1f}% · 매칭 {t.matched_pct:.1f}%")
        return 0
    finally:
        client.close()


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(guard(main))
