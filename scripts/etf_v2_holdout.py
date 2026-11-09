"""45회차 (나) 홀드아웃 문 — 한 번만 돌린다 (docs/factors.md 12.48 `etf_tactical_v2`·`etf_theme_v1`, 결과 전 사전 등록).

    python scripts/etf_v2_holdout.py [--kr-first 2019-05-01] [--us-first 2019-08-01] 2>&1 \
        | python scripts/ops_tee.py out.txt

무엇을 하나(12.48 글자 그대로 — 여기서 새로 정한 것은 아래 "여기서 정한 것" 뿐):
  - 자료: 야후 ^KS11(X)·^GSPC(Y) 일봉 **가격지수 종가**(배당·현금 이자 0) — 지수 ETF 몫·보유 = 지수, 인버스 = −1 × 지수
    일간 수익의 가상 대리(보수·괴리 0). 테마는 SPDR 업종 `etf_theme.UNIVERSE` + SPY·SH **수정종가**(짝도 SPY 수정종가로
    잰다 — 12.48 '공정 비교'), SH 상장 전은 −1 × ^GSPC 대리(두 판 대칭), 국면 판정은 ^GSPC 그대로.
  - 구간: `etf_tactical_v2.HOLDOUT_FROM`(2000-01-01) ≤ 날짜 < 그 시장 운영 `index_prices` 첫 행(인자). 모든 계열을 같은
    자리에서 자른다. 리플레이 시작 = 판 2 의 13번째 월말 다음 거래일(`common_start`), 판 1·판 2·보유가 같은 시작·끝.
  - 문: 시장마다 `etf_tactical_v2.holdout_gate` (a)(b)(c), 미국 테마 `etf_theme.holdout_gate` (i)(ii)(iii)
    (짝 = trading-2 Y 가 자기 문을 넘어야 연다).

여기서 정한 것(되돌릴 수 있는 실행 선택 — 판정 규칙이 아니다):
  - 운영 첫 행 날짜는 `[확인필요]` 라 인자로 받는다. 기본값은 문서가 적은 **가장 이른 날** = 그 달 1일
    (paper.md 17.3 표 "KOSPI 2019-05~·S&P 2019-08~", `etf_tactical_v2.OPERATING_FIRST_MONTH`). 구간 끝이 그 전날
    (2019-04-30·2019-07-31)이라 운영 자료와 겹칠 수 없다 — 실제 첫 행이 그 달 며칠이든 홀드아웃이 짧아질 뿐
    넓어지지 않는다.
    인자는 그 달 안의 날짜만 받는다(다른 달이면 문서와 어긋나므로 돌지 않는다).
  - 계열이 없거나 짧으면(첫 행이 2000-01 이 아님·마지막 행이 운영 첫 행보다 7일(`STALE_DAYS`) 넘게 앞섬·업종/SPY 가 구간
    시작보다 늦게 시작) 그 문은 **판정 불가** — 다른 구간으로 물러서지 않는다(구간을 바꾸면 판 3·시도 +1).
  - 출력은 지수 수준 집계만(보유·수량·금액·사용자 자료 없음). 그래도 운영 출력 관례대로 ops_tee 로 비공개 이슈에 보낸다.
  - Turso·D1·시세 사본을 읽지도 쓰지도 않는다(12.48 "홀드아웃은 Turso 0"). 야후 자료는 저장하지 않는다.

종료코드: 0 = 모든 문을 판정함(통과·실패 무관), 1 = 판정 불가가 하나라도 있음, 2 = 인자 오류.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch.services import etf_tactical as et  # noqa: E402
from batch.services import etf_tactical_v2 as v2  # noqa: E402
from batch.services import etf_theme as th  # noqa: E402

#: 시장 → (계좌, 야후 지수, 이름). 운영 지수와 같은 심볼(trend.py:21, 12.48 "데이터·비용")
INDEXES = {"X": ("^KS11", "KOSPI"), "Y": ("^GSPC", "S&P 500")}
#: 수정종가로 받는 ETF — 업종 11종 + SPY(채움·짝의 지수 몫) + SH(인버스, 상장 전은 대리)
ETFS = (*th.UNIVERSE, th.FILL, th.INVERSE)
#: 상장 전 구간이 정상인 ETF — 그 앞은 대리(SH)이거나 13개 월말이 찬 뒤 순위에 든다(XLRE·XLC).
#: 시작이 늦어도 짧다고 보지 않는다
LATE_OK = frozenset({*th.LATE_SECTORS, th.INVERSE})
#: 야후에서 받는 달력일 — 2000-01 앞 한 달 여유.
#: `period="Nd"` 로 이만큼 받을 수 있는지는 `[확인필요]`(운영은 1300d 까지 씀)
FETCH_FROM = date(1999, 12, 1)

Series = list[tuple[str, float]]
Fetch = Callable[[Sequence[str]], Mapping[str, Series]]


class NoVerdict(Exception):
    """판정 불가 — 까닭을 담는다. 다른 구간으로 물러서지 않는다"""


def yahoo_fetch(tickers: Sequence[str]) -> dict[str, Series]:
    """저장소의 야후 어댑터(`yfinance_src.fetch_daily_bars`, 재시도·호출 제한 판정 포함)로 한 번에 받는다.
    지수 = 종가(가격지수), ETF = 수정종가. 못 받은 심볼은 빠진다(부르는 쪽이 판정 불가로 적는다)"""
    from batch.sources import yfinance_src

    days = (date.today() - FETCH_FROM).days
    res = yfinance_src.fetch_daily_bars(list(tickers), lookback_days=days, chunk_size=len(tickers))
    if res.error:
        print(f"[야후] {res.error}")
    out: dict[str, Series] = {}
    for b in res.data or []:
        값 = b.close if b.ticker.startswith("^") else b.adj_close
        if 값 is not None:
            out.setdefault(b.ticker, []).append((b.date, float(값)))
    return out


def operating_first(market: str, given: str | None) -> str:
    """운영 `index_prices` 첫 행 날짜 — 주면 그것(문서가 적은 달 안이어야 함), 없으면 그 달 1일(가장 이른 날, 보수적)"""
    달 = v2.OPERATING_FIRST_MONTH[market]
    if not given:
        return f"{달}-01"
    d = date.fromisoformat(given[:10]).isoformat()
    if d[:7] != 달:
        raise ValueError(f"{market} 운영 첫 행 {d} 이 문서의 달({달}, paper.md 17.3)과 다르다 — 문서를 먼저 고쳐라")
    return d


def window(
    series: Series | None, ticker: str, first: str, *, late_ok: bool = False, start_by: str | None = None
) -> Series:
    """홀드아웃 구간으로 자른 계열 — 없거나 짧으면 `NoVerdict`. `start_by` = 이 날 이전(포함)에 시작해야 함"""
    if not series:
        raise NoVerdict(f"{ticker} 계열을 받지 못했다")
    rows = v2.holdout_rows(series, first)
    if not rows:
        raise NoVerdict(f"{ticker} 홀드아웃 구간({v2.HOLDOUT_FROM}~{first} 전날)에 행이 없다")
    if not late_ok:
        if rows[0][0][:7] != v2.HOLDOUT_FROM[:7]:
            raise NoVerdict(f"{ticker} 첫 행 {rows[0][0]} — {v2.HOLDOUT_FROM[:7]} 에서 시작하지 않는다(짧음)")
        if start_by is not None and rows[0][0] > start_by:
            raise NoVerdict(f"{ticker} 첫 행 {rows[0][0]} 이 지수 첫 행 {start_by} 보다 늦다(짧음)")
    끝 = (date.fromisoformat(first) - date.fromisoformat(rows[-1][0])).days
    if 끝 > et.STALE_DAYS:
        raise NoVerdict(f"{ticker} 마지막 행 {rows[-1][0]} — 운영 첫 행 {first} 보다 {끝}일 앞선다(짧음)")
    return rows


def market_gate(index_rows: Series, market: str, first: str) -> dict[str, Any]:
    """한 시장의 판 2·판 1(어댑터) 리플레이와 문 (a)(b)(c)"""
    start = v2.common_start(index_rows)
    if start is None:
        raise NoVerdict("13번째 월말이 없다 — 공통 구간을 낼 수 없다")
    r2 = v2.replay(index_rows, start=start)
    r1 = v2.replay(index_rows, v2.v1_regime, start=start)
    if not r2.get("days"):
        raise NoVerdict("리플레이 날이 0")
    return {"v2": r2, "v1": r1, "gate": v2.holdout_gate(r2, r1, market, first), "first": first}


def theme_gate(index_rows: Series, etfs: Mapping[str, Series], first: str, pair_passed: bool) -> dict[str, Any]:
    """미국 테마 리플레이·짝(trading-2 Y, SPY 수정종가·SH)·문 (i)(ii)(iii)"""
    start = v2.common_start(index_rows)
    if start is None:
        raise NoVerdict("13번째 월말이 없다")
    sectors = {t: etfs[t] for t in th.UNIVERSE}
    theme = th.replay(index_rows, sectors, etfs[th.FILL], sh_series=etfs[th.INVERSE], start=start)
    pair = v2.replay(index_rows, start=start, long_series=etfs[th.FILL], inverse_series=etfs[th.INVERSE])
    return {"theme": theme, "pair": pair, "gate": th.holdout_gate(theme, pair, pair_passed)}


def _p(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"{x:+.{digits}f}%"


def _n(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}"


def _r(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _ok(b: bool) -> str:
    return "통과" if b else "실패"


def market_lines(market: str, res: dict[str, Any]) -> list[str]:
    r2, r1, g = res["v2"], res["v1"], res["gate"]
    표 = " (문헌 표본 안)" if g["literature_sample"] else ""
    return [
        f"  구간 {r2['from']}~{r2['to']} · {r2['days']}일 · 운영 첫 행 {res['first']} 전날까지",
        f"  문 (a) TWR 판2>판1 {_ok(g['a'])} · (b) 연 뒤집힘 판2<판1 {_ok(g['b'])}"
        f" · (c) MDD 판2≥0.7×보유 {_ok(g['c'])} → {_ok(g['pass'])}{표}",
        f"  TWR 판2 {_p(r2['twr_pct'])} · 판1 {_p(r1['twr_pct'])} · 보유 {_p(r2['hold_pct'])}"
        f" (판2−보유 {g['excess_vs_hold_pct']:+.1f}%p)",
        f"  MDD 판2 {_p(r2['mdd_pct'])} · 판1 {_p(r1['mdd_pct'])} · 보유 {_p(r2['hold_mdd_pct'])}"
        f" (0.7×보유 {_p(v2.MDD_RATIO * r2['hold_mdd_pct'])})",
        f"  연 뒤집힘 판2 {_n(r2['flips_per_year'])} · 판1 {_n(r1['flips_per_year'])}"
        f" · 국면 전환 판2 {r2['regime_changes']} · 판1 {r1['regime_changes']}",
        f"  상승장 날 현금 비율 판2 {_r(r2['up_cash_share'])} · 판1 {_r(r1['up_cash_share'])}"
        f" (현금+인버스 판2 {_r(r2['up_not_long_share'])} · 판1 {_r(r1['up_not_long_share'])})"
        f" · 7일 넘는 빈 구간 {r2['stale_gaps']}",
    ]  # fmt: skip


def theme_lines(res: dict[str, Any]) -> list[str]:
    t, p, g = res["theme"], res["pair"], res["gate"]
    (ta, tb), (pa, pb) = g["halves"]["theme"], g["halves"]["pair"]
    짝 = "" if g["pair_gate"] else " (짝 trading-2 Y 가 자기 문을 못 넘어 열지 않음)"
    return [
        f"  구간 {t['from']}~{t['to']} · {t['days']}일 · 상승 달 {t['up_months']}",
        f"  문 (i) TWR 테마>짝 {_ok(g['i'])} · (ii) 앞·뒤 반 각각 {_ok(g['ii'])}"
        f" · (iii) MDD 테마≥0.7×SPY보유 {_ok(g['iii'])} → {_ok(g['pass'])}{짝}",
        f"  TWR 테마 {_p(t['twr_pct'])} · 짝 {_p(p['twr_pct'])} · SPY 보유 {_p(p['hold_pct'])}",
        f"  반 테마 {_r(ta)}/{_r(tb)} · 짝 {_r(pa)}/{_r(pb)}",
        f"  MDD 테마 {_p(t['mdd_pct'])} · 짝 {_p(p['mdd_pct'])} · SPY 보유 {_p(p['hold_mdd_pct'])}"
        f" · 연 매매 테마 {_n(t['trades_per_year'])}",
    ]  # fmt: skip


def run(fetch: Fetch, firsts: Mapping[str, str], out: Callable[[str], None] = print) -> int:
    """받기 → 자르기 → 리플레이 → 문 → 요약. `fetch` 는 시험이 합성 계열로 갈아 끼운다"""
    tickers = [INDEXES[m][0] for m in INDEXES] + list(ETFS)
    data = fetch(tickers)
    out(f"[ETF 판 2 홀드아웃 — factors.md 12.48 (나), 1회] 구간 {v2.HOLDOUT_FROM} ~ 운영 첫 행 전날 · 지수 수준 집계만")
    불가 = 0
    gates: dict[str, dict[str, Any]] = {}
    index_rows: dict[str, Series] = {}
    for m, (sym, name) in INDEXES.items():
        out(f"{m} {name}({sym}) — etf_tactical_v2 문")
        try:
            index_rows[m] = window(data.get(sym), sym, firsts[m])
            gates[m] = market_gate(index_rows[m], m, firsts[m])
            for line in market_lines(m, gates[m]):
                out(line)
        except (NoVerdict, ValueError) as exc:
            불가 += 1
            out(f"  판정 불가: {exc}")
    out("Y 테마 — etf_theme_v1 문 ①")
    try:
        if "Y" not in index_rows:
            raise NoVerdict("^GSPC 계열이 판정 불가라 테마도 판정 불가")
        시작 = index_rows["Y"][0][0]
        etfs = {t: window(data.get(t), t, firsts["Y"], late_ok=t in LATE_OK, start_by=시작) for t in ETFS}
        pair_passed = "Y" in gates and gates["Y"]["gate"]["pass"]
        for line in theme_lines(theme_gate(index_rows["Y"], etfs, firsts["Y"], pair_passed)):
            out(line)
    except (NoVerdict, ValueError) as exc:
        불가 += 1
        out(f"  판정 불가: {exc}")
    out(f"끝 — 판정 불가 {불가}건" if 불가 else "끝 — 모든 문 판정함")
    return 1 if 불가 else 0


def main(argv: list[str] | None = None, fetch: Fetch = yahoo_fetch) -> int:
    ap = argparse.ArgumentParser(description="45회차 (나) 홀드아웃 문 1회 실행 (factors.md 12.48)")
    ap.add_argument("--kr-first", default="", help="운영 index_prices ^KS11 첫 행 YYYY-MM-DD (기본 2019-05-01)")
    ap.add_argument("--us-first", default="", help="운영 index_prices ^GSPC 첫 행 YYYY-MM-DD (기본 2019-08-01)")
    args = ap.parse_args(argv)
    try:
        firsts = {"X": operating_first("X", args.kr_first), "Y": operating_first("Y", args.us_first)}
    except ValueError as exc:
        print(f"인자 오류: {exc}")
        return 2
    for m, given in (("X", args.kr_first), ("Y", args.us_first)):
        끝 = (date.fromisoformat(firsts[m]) - timedelta(days=1)).isoformat()
        표 = "" if given else " (기본값 — 문서가 적은 달의 1일, 실제 첫 행 [확인필요])"
        print(f"{m} 운영 첫 행 {firsts[m]}{표} → 구간 끝 {끝}")
    return run(fetch, firsts)


if __name__ == "__main__":
    sys.exit(main())
