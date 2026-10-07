"""포트폴리오 재계산 (docs/portfolio.md, Step 12).

원본(trades · dividend_receipts)에서 파생 표를 **통째로 다시 만든다**.
  trade_lots · positions · portfolio_values · portfolio_summary
  trade_reviews · review_stats (Step 15 매매 복기, docs/review.md)

복기를 따로 배치로 두지 않은 이유: 입력이 trade_lots 와 같다. 같은 실행 안에서
만들어야 lot 과 복기가 어긋난 채로 화면에 보이는 순간이 없다.

언제 도나
  - 웹에서 매매·배당을 저장하면 repository_dispatch(portfolio) 로 곧바로 (1~2분 뒤 반영)
  - 일일 배치가 시세를 받은 뒤 (평가액을 그날 종가로)

실행
  python -m batch.jobs.portfolio
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any

from batch import config
from batch.core import calendar as cal
from batch.core import db, visibility
from batch.core import settings_range as sr
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import backtest as bt
from batch.services import metrics as m
from batch.services import portfolio as pf
from batch.services import review as rv
from batch.sources import dart

log = logging.getLogger("portfolio")

JOB_NAME = "portfolio"
#: 다시 쓰는 동안 요약의 지문 자리에 적는 표시 (docs/infra.md 25.644). 400문장씩 나눠 쓰다 가운데 조각이 실패하면
#: 보유·체결 묶음이 반쪽인데 요약 지문은 예전 그대로라 화면이 "계산 중" 도 띄우지 않고 반쪽을 보였고, 매도 플래그가 그
#: 반쪽으로
#: 판정했다. 첫 조각 맨 앞에서 이 값을 적고 마지막 문장(요약)이 진짜 지문으로 덮는다 — 끝까지 못 가면 "계산 중" 이
#: 남는다
REBUILDING = "rebuilding"


def 쓰는_중(client: TursoClient) -> bool:
    """재계산이 끝나지 않아 보유 표가 반쪽일 수 있나 (25.644·25.648). 보유를 읽는 곳이 모두 이것을 본다 —
    매도 플래그·장중 감시 목록·리포트 2부. 표가 없는 DB 는 아니다."""
    try:
        return client.execute("SELECT trades_version FROM portfolio_summary WHERE id = 1").scalar() == REBUILDING
    except Exception as e:  # noqa: BLE001 — 표가 없는 DB 는 정상
        if not db.표가_없나(e):
            raise
        return False

# 열 목록은 문자열 하나로 두고 개수는 세어 쓴다. 손으로 센 숫자는 열을 더할 때 어긋난다 (db.column_count 주석)
REVIEW_COLUMNS = (
    "buy_trade_id, stock_id, horizon, buy_date, last_sell_date, currency, quantity_sold, quantity_bought, partial,"
    " cost, proceeds, return_pct, holding_days, realized_pnl_krw, price_pnl_krw, fx_pnl_krw, has_snapshot,"
    " snapshot_as_of, score_at_trade, signal_type_at_trade, sentiment_at_trade, factor_scores_at_trade,"
    " target_pct, stop_pct, outcome, verdict_text, calc_version, created_at"
)
STATS_COLUMNS = (
    "group_key, group_kind, label, n, sample_ok, win_rate, avg_return_pct, median_return_pct, avg_holding_days,"
    " target_rate, stop_rate, total_pnl_krw, calc_version, created_at"
)


def _placeholders(columns: str) -> str:
    return ", ".join(["?"] * db.column_count(columns))


def _f(value: Any) -> float | None:
    return None if value is None else float(value)


#: 수수료·세율 잎 이름. `web/lib/settings.ts` 의 `fees`·`taxes` 와 같다
_수수료_잎 = ("kr_buy_pct", "kr_sell_pct", "us_buy_pct", "us_sell_pct")
_세율_잎 = ("kr_transaction_pct", "kr_dividend_pct", "us_dividend_pct", "us_capital_gains_pct")


def cost_rates(
    settings_fees: dict | None, settings_taxes: dict | None, trades: list[pf.Trade] | None = None
) -> tuple[dict[str, pf.CostRates], list[str]]:
    """실현손익에 쓰는 비용률. (나라별 비용률, 경고).

    **범위 밖이면 모른다로 둔다** (docs/infra.md 25.171). 0 으로 되돌리면
    "수수료 없음"·"세금 없음" 이 되어 **실현손익이 좋은 쪽으로 틀린다.**

    **안 넣은 값도 여기서 알린다** (2026-09-26, docs/infra.md 25.192).
    예전에는 이 자리에 "모르는 값은 부르는 쪽이 이미 경고로 알린다" 고 적어 두었는데,
    부르는 쪽은 **수수료만** 보고 있었다 — 증권거래세를 안 넣으면 국내 매도의 세금이
    조용히 0 이 되고 실현손익이 그만큼 좋게 나왔다. `잎마다` 는 범위 **밖**일 때만
    말하고 "안 넣은 값은 정상" 으로 넘기므로(`범위_안`), 안 넣은 것을 말할 사람은
    **무엇을 모르는지 아는 이 함수**다.
    """
    fees, 경고1 = sr.잎마다(settings_fees, _수수료_잎)
    taxes, 경고2 = sr.잎마다(settings_taxes, _세율_잎)
    나온것 = {
        # 지난 구간 매도는 법정 세율 표로 추정한다 — 백테스트와 같은 표 (docs/infra.md 25.791)
        "KRW": pf.CostRates(
            fees["kr_buy_pct"], fees["kr_sell_pct"], taxes["kr_transaction_pct"],
            sell_tax_schedule=bt.KR_SELL_TAX_SCHEDULE,
        ),
        # 미국은 매매마다 붙는 세금이 없다. `None` 이 "모른다" 가 아니라 "없다" 다
        "USD": pf.CostRates(fees["us_buy_pct"], fees["us_sell_pct"], None, has_sell_tax=False),
    }
    return 나온것, 경고1 + 경고2 + _안_넣은_비용(나온것, trades)


def _양도세율(settings_taxes: dict | None) -> float | None:
    """설정의 해외 양도소득세율(%). 없거나 범위 밖이면 None — 추정 세액을 비운다 (25.617)."""
    taxes, _ = sr.잎마다(settings_taxes, ("us_capital_gains_pct",))
    값 = taxes["us_capital_gains_pct"]
    return None if 값 is None else float(값)


#: 비용률을 안 넣었을 때의 말. **무엇이 빠졌고 실현손익이 어느 쪽으로 틀리는지**를 적는다
_안_넣은_수수료 = (
    "설정에 수수료율이 비어 있어, 수수료를 입력하지 않은 매매는 수수료 0 으로 계산했습니다"
    " — 실현손익이 그만큼 좋게 나옵니다"
)
def _안_넣은_거래세(시행일: str, 세율: float) -> str:
    # 날짜·세율은 법정 표의 마지막 구간에서 만든다 — 박아 두면 표에 새 구간이 들어갈 때 틀린다 (25.795, 교차검증)
    return (
        f"설정에 국내 증권거래세율이 비어 있어, 세금을 입력하지 않은 {시행일} 이후 국내 매도는 세금 0 으로"
        " 계산했습니다 — 실현손익이 그만큼 좋게 나옵니다"
        f" (그 구간 법정 {세율:.2f}% [확인필요: 법령 원문]. 그 전 매도는 법정 세율 표로 추정)"
    )


def _안_넣은_비용(rates: dict[str, pf.CostRates], trades: list[pf.Trade] | None = None) -> list[str]:
    """안 넣은 비용률을 알린다. **세금도 본다** (docs/infra.md 25.192).

    미국은 `sell_tax_pct` 가 일부러 `None` 이라(매매세가 없다) 세금은 국내만 본다.
    전부 보면 늘 경고가 떠서, 거짓 경보가 쌓이면 사람이 경고를 안 읽는다(25.11).
    **세금 경고는 설정 세율을 실제로 쓰는 매도가 있을 때만** (25.795, 교차검증) — 25.791 뒤로 지난 구간
    매도는 법정 표로 계산되므로, 지금 구간에 세금을 비운 국내 매도가 없으면 "세금 0 으로 계산했습니다" 는
    거짓 경보다. `trades` 를 안 주면 예전처럼 알린다.
    """
    말들: list[str] = []
    if any(r.buy_fee_pct is None or r.sell_fee_pct is None for r in rates.values()):
        말들.append(_안_넣은_수수료)
    krw = rates["KRW"]
    if krw.sell_tax_pct is None:
        시행일, 세율 = (krw.sell_tax_schedule or (("", 0.0),))[-1]
        쓰는_매도 = trades is None or any(
            t.side == "sell" and t.currency == "KRW" and t.tax is None and t.trade_date >= 시행일 for t in trades
        )
        if 쓰는_매도:
            말들.append(_안_넣은_거래세(시행일 or "모든", 세율))
    return 말들


def trades_version(client: TursoClient) -> str:
    """원본의 지문. 화면이 이것과 portfolio_summary.trades_version 을 비교해 '계산 중' 을 띄운다."""
    t = client.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM trades").rows[0]
    d = client.execute("SELECT COUNT(*), COALESCE(MAX(updated_at), '') FROM dividend_receipts").rows[0]
    return f"t{t[0]}:{t[1]}|d{d[0]}:{d[1]}"


def load_trades(client: TursoClient) -> list[pf.Trade]:
    rs = client.execute(
        "SELECT t.id, t.stock_id, t.side, t.trade_date, t.price, t.quantity, t.currency, t.fx_rate, t.fee, t.tax,"
        " t.horizon, s.asset_type FROM trades t LEFT JOIN stocks s ON s.id = t.stock_id ORDER BY t.trade_date, t.id"
    )
    return [
        pf.Trade(
            int(r["id"]), int(r["stock_id"]), str(r["side"]), str(r["trade_date"]), float(r["price"]),
            float(r["quantity"]), str(r["currency"]), float(r["fx_rate"]), _f(r["fee"]), _tax(r), r["horizon"],
        )
        for r in rs.dicts()
    ]  # fmt: skip


def _tax(r: dict[str, Any]) -> float | None:
    """그 매매의 세금. 비었으면 None(설정 비율로 추정) — **국내 ETF 매도는 0** (docs/infra.md 25.896).

    ETF 는 증권거래세가 없다. 비운 국내 매도에 설정의 거래세(주식 기준)를 붙이면 ETF 의 실현손익이 그만큼 나빠진다.
    저장된 값은 바꾸지 않는다(CLAUDE.md: trades 는 사용자 입력값만) — 계산할 때만 0 으로 읽는다.
    국내 상장 해외·채권 ETF 매매차익의 배당소득세(15.4%)는 이 칸이 아니다 — `ETF_GAIN_TAX_NOTE` 로 알린다
    """
    tax = _f(r["tax"])
    if tax is None and r["side"] == "sell" and r.get("asset_type") == "etf" and r["currency"] == "KRW":
        return 0.0
    return tax


#: 국내 ETF 를 판 기록이 있을 때 붙이는 말 (25.896)
ETF_GAIN_TAX_NOTE = (
    "국내 ETF 매도는 증권거래세가 없어 0 으로 계산했습니다. 해외지수·채권 등 ETF 의 매매차익 배당소득세(15.4%)는"
    " 계산에 넣지 않았습니다 — 낸 세금이 있으면 그 매도의 세금 칸에 직접 넣으세요"
)


def load_buy_snapshots(client: TursoClient) -> dict[int, rv.BuySnapshot]:
    """매수 행과 거기 얼려 둔 근거(스냅샷 열). 복기의 입력.

    load_trades 와 따로 읽는 이유: pf.Trade 는 FIFO 에 필요한 열만 갖고, 스냅샷 열을
    거기 얹으면 포트폴리오 계산이 복기 사정을 알게 된다. 두 관심사를 섞지 않는다.
    """
    rs = client.execute(
        "SELECT id, stock_id, trade_date, quantity, currency, horizon, snapshot_as_of, score_at_trade,"
        " signal_type_at_trade, sentiment_at_trade, factor_scores_at_trade FROM trades WHERE side = 'buy'"
    )
    return {
        int(r["id"]): rv.BuySnapshot(
            trade_id=int(r["id"]), stock_id=int(r["stock_id"]), trade_date=str(r["trade_date"]),
            quantity=float(r["quantity"]), currency=str(r["currency"]), horizon=r["horizon"],
            snapshot_as_of=r["snapshot_as_of"], score_at_trade=_f(r["score_at_trade"]),
            signal_type_at_trade=r["signal_type_at_trade"], sentiment_at_trade=_f(r["sentiment_at_trade"]),
            factor_scores_at_trade=r["factor_scores_at_trade"],
        )
        for r in rs.dicts()
    }  # fmt: skip


def load_receipts(client: TursoClient) -> list[pf.DividendReceipt]:
    rs = client.execute("SELECT stock_id, pay_date, net_amount, fx_rate FROM dividend_receipts ORDER BY pay_date")
    return [
        pf.DividendReceipt(int(r["stock_id"]), str(r["pay_date"]), float(r["net_amount"]), float(r["fx_rate"]))
        for r in rs.dicts()
    ]


def load_closes(client: TursoClient, stock_ids: list[int], since: str) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    size = db.in_chunk(reserve=1)  # 기준일 하나 몫. D1 은 질의당 파라미터 100개 (infra 25.5)
    for start in range(0, len(stock_ids), size):
        ids = stock_ids[start : start + size]
        rs = client.execute(
            # 수정주가를 쓴다. 보유 수량은 사용자가 적은 값 하나뿐이라, 분할이 있으면
            # 원자료 종가와 수량이 서로 다른 잣대가 된다. 오늘 값은 둘이 같다
            # (마지막 날의 계수는 1). docs/adjust.md 7장
            #
            # **미국은 `close` 를 쓴다** (docs/infra.md 25.212). 야후 `Adj Close` 는 분할에 더해 **배당까지** 반영한다.
            # 그 계열로 평가하면 배당 수익이 이미 들어 있는데 TWR 이 받은 배당(`dividend_receipts`)을 또 더해 **두 번**
            # 세고, 매수한 날은 체결가(원래 값)와 수정 종가(뒤 배당만큼 낮음)가 어긋나 가짜 손실이 난다.
            # 야후 `Close`(`auto_adjust=False`)는 **분할만** 반영한 값이라 이 자리의 잣대(분할 맞춘 수량)와 맞는다.
            # 국내 수정주가는 거래소 등락률로 되살려 분할·감자만 담는다(docs/adjust.md) — 그대로 둔다
            # 0 이하 종가는 값이 없는 날이다 (docs/infra.md 25.203). 그대로 쓰면 평가액 0원 → 손절 플래그
            f"SELECT p.stock_id, p.date, {db.SPLIT_ONLY_PRICE_SQL} AS close"
            " FROM prices p JOIN stocks s ON s.id = p.stock_id"
            f" WHERE p.stock_id IN ({', '.join(['?'] * len(ids))})"
            " AND p.date >= ? AND p.close IS NOT NULL AND p.close > 0 AND COALESCE(p.adj_close, p.close) > 0",
            [*ids, since],
        )
        for r in rs.dicts():
            out.setdefault(int(r["stock_id"]), {})[str(r["date"])] = float(r["close"])
    return out


def load_fx(client: TursoClient, since: str) -> dict[str, dict[str, float]]:
    # **0 이하 환율은 없는 날이다** (docs/infra.md 25.690, 교차검증 재현). 그날 미국 보유 평가액이 0, TWR 이 0 에 붙어
    # −100% 가
    # 되고 경고도 없었다. `fx.usable`(25.687)을 지나지 않는 경로라 여기서 거른다. 비면 앞 날 환율로 이어진다
    rs = client.execute(
        "SELECT date, rate FROM fx_rates WHERE pair = 'USDKRW' AND date >= ? AND rate > 0", [since]
    )
    return {"USD": {str(r["date"]): float(r["rate"]) for r in rs.dicts()}}


def next_yahoo_earnings(client: TursoClient, stock_ids: list[int], today: date) -> dict[int, tuple[str, bool]]:
    """종목별 오늘 이후 가장 가까운 **야후** 실적 예정일 (날짜, 확정 여부). 없으면 그 종목은 빠진다 (25.910).

    `earnings_calendar` 는 한 종목에 야후·추정을 같이 넣지 않는다(jobs/earnings_calendar) — 야후 행이 있으면 그것이
    일정이다.
    """
    from batch.jobs.earnings_calendar import SOURCE_YAHOO, YAHOO_EVENT

    out: dict[int, tuple[str, bool]] = {}
    for sid in stock_ids:
        rs = client.execute(
            "SELECT scheduled_date, is_confirmed FROM earnings_calendar"
            " WHERE stock_id = ? AND source = ? AND event_type = ? AND scheduled_date >= ?"
            " ORDER BY scheduled_date LIMIT 1",
            [sid, SOURCE_YAHOO, YAHOO_EVENT, today.isoformat()],
        )
        if rs.rows:
            out[sid] = (str(rs.rows[0][0])[:10], bool(rs.rows[0][1]))
    return out


def fiscal_year_ends(client: TursoClient, stock_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """종목별 (최근 결산일, 작년 연간 보고서 접수일). 미국은 스냅샷의 period_end, 국내는 12월 결산으로 본다."""
    out: dict[int, tuple[str, str | None]] = {}
    for sid in stock_ids:
        rs = client.execute(
            "SELECT f.fiscal_year, f.report_date, s.country,"
            " (SELECT fs.payload FROM financial_snapshots fs WHERE fs.stock_id = f.stock_id"
            "   AND fs.fiscal_year = f.fiscal_year ORDER BY fs.as_of_date DESC LIMIT 1) AS payload"
            " FROM financials f JOIN stocks s ON s.id = f.stock_id"
            " WHERE f.stock_id = ? AND f.report_code = ? AND f.consolidated ="
            " (SELECT fb.consolidated FROM financials fb"
            " WHERE fb.stock_id = f.stock_id AND fb.report_code = f.report_code"
            " ORDER BY fb.fiscal_year DESC, fb.consolidated DESC LIMIT 1)"
            " ORDER BY f.fiscal_year DESC LIMIT 1",
            [sid, dart.ANNUAL_REPORT_CODE],
        ).dicts()
        if not rs:
            continue
        row = rs[0]
        end = f"{row['fiscal_year']}-12-31"
        if row["country"] == "US" and row["payload"]:
            with contextlib.suppress(ValueError):
                end = json.loads(row["payload"]).get("period_end") or end
        out[sid] = (str(end), row["report_date"])
    return out


def run() -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # **사용자의 오늘이다. UTC 가 아니다** (docs/infra.md 25.125).
        # 국내 배치는 08:27 KST 에 도는데 그때 UTC 는 아직 전날 23:27 이다
        today = cal.user_today()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=None, trade_date=today.isoformat(),
                                    )  # fmt: skip
        now = db.now_iso()
        version = trades_version(client)
        trades = load_trades(client)
        receipts = load_receipts(client)
        warnings: list[str] = []
        if client.execute(
            "SELECT COUNT(*) FROM trades t JOIN stocks s ON s.id = t.stock_id"
            " WHERE t.side = 'sell' AND t.currency = 'KRW' AND t.tax IS NULL AND s.asset_type = 'etf'"
        ).scalar():
            warnings.append(ETF_GAIN_TAX_NOTE)  # 25.896
        rates, 설정경고 = cost_rates(
            db.get_setting(client, "fees", {}, 못읽음=warnings), db.get_setting(client, "taxes", {}, 못읽음=warnings),
            trades,
        )
        # 안 넣은 비용률 경고는 `cost_rates` 가 함께 돌려준다 (docs/infra.md 25.192)
        warnings += 설정경고

        stocks = {
            int(r["id"]): r
            for r in client.execute("SELECT id, ticker, COALESCE(name_ko, name_en, ticker) AS name, currency, sector"
                                    " FROM stocks WHERE id IN (SELECT DISTINCT stock_id FROM trades"
                                    " UNION SELECT DISTINCT stock_id FROM dividend_receipts)").dicts()
        }  # fmt: skip

        by_stock: dict[int, list[pf.Trade]] = defaultdict(list)
        for t in trades:
            by_stock[t.stock_id].append(t)

        lots: list[pf.Lot] = []
        positions: list[pf.Position] = []
        for sid, stock_trades in by_stock.items():
            currency = stock_trades[0].currency
            stock_lots, open_lots, w = pf.match_fifo(stock_trades, rates.get(currency, pf.CostRates()))
            lots.extend(stock_lots)
            warnings.extend(f"{stocks.get(sid, {}).get('ticker', sid)} {x}" for x in w)
            pos = pf.position_from(sid, currency, open_lots)
            if pos is not None:
                positions.append(pos)

        since = min((t.trade_date for t in trades), default=today.isoformat())
        ids = sorted(by_stock)
        # **첫 매매일 앞 종가도 조금 불러온다** (docs/infra.md 25.883). 첫 매매일부터만 불러오면 **오늘 처음 산**
        # 포트폴리오는
        # 그 종목의 종가가 하나도 없어(오늘 종가는 내일 나온다) `fresher_price` 가 "종가 없음" 으로 비웠다 — 평가액 0원,
        # 비중 0. 그 앞 종가가 하나라도 있으면 "종가가 체결보다 묵었다" 로 보고 체결가로 평가한다(25.398 규칙 그대로).
        # 거래일 사이 최장 간격(`metrics.MAX_SESSION_GAP_DAYS`) 만큼 거슬러 올라가면 연휴를 끼어도 하나는 걸린다.
        # 날짜별 평가액(TWR)에는 첫 매매일부터만 넘긴다 — 앞 종가를 들고 가면 첫날을 전일 종가로 평가한다(25.398 의 −5%)
        앞_종가일 = (date.fromisoformat(since) - timedelta(days=m.MAX_SESSION_GAP_DAYS)).isoformat()
        closes_wide = load_closes(client, ids, 앞_종가일)
        closes = {sid: {d: v for d, v in series.items() if d >= since} for sid, series in closes_wide.items()}
        fx_series = load_fx(client, since)
        latest_fx = max(fx_series["USD"].items(), default=(None, None))
        for pos in positions:
            # 종가가 마지막 체결보다 묵었으면 체결가로 평가한다 (docs/infra.md 25.398)
            price, price_day = pf.fresher_price(closes_wide.get(pos.stock_id, {}), by_stock[pos.stock_id])
            fx_day, fx_now = ("-", 1.0) if pos.currency == "KRW" else latest_fx
            pf.valuate(pos, price, price_day, fx_now, fx_day)
            if pos.market_value_krw is None:
                ticker = stocks.get(pos.stock_id, {}).get("ticker")
                warnings.append(f"{ticker} 종가 또는 환율이 없어 평가하지 못했습니다")
        # **얼마나 묵은 값으로 평가했나** (docs/infra.md 25.136). 날짜는 처음부터 적고
        # 있었는데 그 날짜를 보고 판단하는 곳이 없었다. 값은 지우지 않고 말한다
        warnings.extend(pf.stale_notes(positions, today.isoformat()))
        pf.set_weights(positions)

        values, w = pf.value_series(trades, receipts, closes, fx_series, today.isoformat(), rates)
        warnings.extend(w)

        sector_of = {sid: row.get("sector") for sid, row in stocks.items()}
        섹터상한, 섹터상한경고 = db.get_setting_in_range(client, "max_weight_per_sector", 30.0)
        if 섹터상한경고:
            warnings.append(섹터상한경고)
        # 상한의 분모 = 총 투자가능금액 (docs/infra.md 25.238). 리포트 2부와 같은 잣대
        투자가능, 투자가능경고 = db.get_setting_in_range(client, "total_investable_amount", 0.0)
        if 투자가능경고:
            warnings.append(투자가능경고)
        alloc = pf.allocation(
            positions, sector_of, 30.0 if 섹터상한 is None else float(섹터상한),
            total_investable_krw=float(투자가능 or 0.0),
        )

        무위험, 무위험경고 = sr.잎마다(db.get_setting(client, "risk_free_manual", {}, 못읽음=warnings), ("kr_pct",))
        warnings += 무위험경고
        rf = 무위험["kr_pct"]
        metrics: dict[str, Any] = {"data_points": len(values)}
        if values:
            # **주말 행은 뺀다** (docs/infra.md 25.332). 평가 계열에는 매매·배당이 있던 날이 섞이는데,
            # 미국 체결을 한국 날짜로 적으면 토요일이 생긴다. 그날은 어느 시장도 열리지 않아 수익률 0 인 행이 되어
            # 표본 수(200 "거래일" 하한)를 부풀리고 변동성을 낮춘다. 지수는 누적이라 빼도 수익이 사라지지 않는다
            # — 다음 거래일 수익률에 그대로 들어간다
            # **아무것도 들고 있지 않은 날도 뺀다** (docs/infra.md 25.736, 감사) — 현금 상태의 수익률 0 행이 같은
            # 방식으로 부풀렸다
            points = [
                m.PricePoint(date=d, close=v.twr_index)
                for v in pf.invested_rows(values)
                if (d := date.fromisoformat(v.date)).weekday() < 5
            ]
            # **`if rf` 로 가르지 않는다** (docs/infra.md 25.298). 0 은 "무위험수익률 0%" 라는 값이다 —
            # 종목 성과(`jobs/metrics.risk_free_for`)는 0 을 그대로 쓰는데 여기만 "모름" 으로 읽어
            # 샤프가 비었다(25.179 와 같은 모양)
            computed = m.compute(
                bt._window_for(len(points)), points, risk_free_annual=None if rf is None else float(rf) / 100
            )
            metrics.update({k: v for k, v in asdict(computed).items() if not isinstance(v, date)})
            metrics["twr_total_return"] = values[-1].twr_index - 1
            # 지표를 낸 구간을 적는다 — 다 판 뒤 현금 일수를 뺐으면 끝 날이 마지막 보유일이다 (25.736)
            metrics["start_date"] = points[0].date.isoformat() if points else values[0].date
            metrics["end_date"] = points[-1].date.isoformat() if points else values[-1].date

        upcoming = []
        보유_번호 = [p.stock_id for p in positions]
        # **야후 예정일이 있으면 그것** (docs/portfolio.md, docs/infra.md 25.910, 감사). 예전에는 법정 기한만 써서
        # 야후가 10-28(D-25)을
        # 주는 종목도 11-14(D-42)로 보였다 — 실적이 보유자 예상보다 먼저 나왔다
        야후 = next_yahoo_earnings(client, 보유_번호, today)
        for sid, (fy_end, last_filed) in fiscal_year_ends(client, 보유_번호).items():
            try:
                kind, deadline, d_day = pf.next_filing_deadline(fy_end, today)
            except ValueError:
                continue
            basis = "legal"
            if sid in 야후:
                deadline, confirmed = 야후[sid]
                d_day = (date.fromisoformat(deadline) - today).days
                kind, basis = "실적발표", ("yahoo" if confirmed else "yahoo_range")
            upcoming.append({"stock_id": sid, "ticker": stocks[sid]["ticker"], "name": stocks[sid]["name"],
                             "kind": kind, "deadline": deadline, "d_day": d_day, "basis": basis,
                             "fiscal_year_end": fy_end, "last_annual_filed": last_filed})  # fmt: skip
        upcoming.sort(key=lambda u: u["d_day"])

        totals = {
            "positions": len(positions),
            "cost_krw": sum(p.cost_krw for p in positions),
            # **평가액과 같은 종목 집합의 원가** (docs/infra.md 25.330). 평가 못 한 종목(종가·환율 없음)은 평가액에서
            # 빠지는데 원가는 전부 더해, 화면이 "평가액 500만 · 원가 900만 · 평가손익 +100만" 처럼
            # 서로 안 맞는 셋을 보였다
            "valued_cost_krw": sum(p.cost_krw for p in positions if p.market_value_krw is not None),
            "unvalued_count": sum(1 for p in positions if p.market_value_krw is None),
            "unvalued_cost_krw": sum(p.cost_krw for p in positions if p.market_value_krw is None),
            "market_value_krw": sum(p.market_value_krw or 0 for p in positions),
            "unrealized_pnl_krw": sum(p.unrealized_pnl_krw or 0 for p in positions),
            "unrealized_price_pnl_krw": sum(p.unrealized_price_pnl_krw or 0 for p in positions),
            "unrealized_fx_pnl_krw": sum(p.unrealized_fx_pnl_krw or 0 for p in positions),
            "realized_pnl_krw": sum(lot.realized_pnl_krw for lot in lots),
            "realized_price_pnl_krw": sum(lot.price_pnl_krw for lot in lots),
            "realized_fx_pnl_krw": sum(lot.fx_pnl_krw for lot in lots),
            "fees_taxes_krw": sum(lot.fees_taxes_krw for lot in lots),
            "dividends_net_krw": sum(r.net_amount * r.fx_rate for r in receipts),
            "cost_estimated": any(p.cost_estimated for p in positions) or any(lot.cost_estimated for lot in lots),
            # **평가에 쓴 종가의 날짜** (docs/infra.md 25.331). 요약의 "기준" 은 재계산한 날이라, 월요일 14시에 저장하면
            # "기준 월요일" 인데 실제 평가는 금요일 종가였다. 종목마다 다를 수 있어 가장 이른 날과 늦은 날을 둔다
            "price_date_min": min((p.price_date for p in positions if p.price_date), default=None),
            "price_date_max": max((p.price_date for p in positions if p.price_date), default=None),
            "fx_date": latest_fx[0],
            "fx_usdkrw": latest_fx[1],
            # 해외 양도세 **연간 추정** — 실현손익에 섞지 않는다 (docs/infra.md 25.617)
            "us_cgt_estimates": pf.us_capital_gains_estimates(
                lots, {t.id: t.trade_date for t in trades if t.side == "sell"},
                _양도세율(db.get_setting(client, "taxes", {})),
            ),
        }

        # ---- 매매 복기 (docs/review.md) ----
        # 목표·손절은 복기 시점의 설정값이다. 매수 시점 값을 얼려 두지 않았다 [확인필요: 얼릴지]
        # 범위 밖·반쪽 짝은 버리고 기본값을 쓴다 (docs/infra.md 25.180).
        # 복기는 "목표에 닿았나" 를 판정한다 — 문턱이 틀리면 성적표가 통째로 틀린다
        horizon_targets, 목표경고 = sr.기간별_목표(db.get_setting(client, "horizon_targets", None))
        for 줄 in 목표경고:
            log.warning("%s", 줄)
        reviews = rv.build_reviews(
            lots, load_buy_snapshots(client), {t.id: t.trade_date for t in trades if t.side == "sell"},
            horizon_targets,
        )  # fmt: skip
        review_stats = rv.build_stats(reviews)

        statements: list[tuple[str, list[Any]]] = [
            ("UPDATE portfolio_summary SET trades_version = ? WHERE id = 1", [REBUILDING]),
            ("DELETE FROM trade_lots", []),
            ("DELETE FROM positions", []),
            ("DELETE FROM portfolio_values", []),
            ("DELETE FROM trade_reviews", []),
            ("DELETE FROM review_stats", []),
        ]
        for lot in lots:
            statements.append((
                "INSERT INTO trade_lots (buy_trade_id, sell_trade_id, stock_id, quantity, buy_price, sell_price,"
                " currency, buy_fx, sell_fx, cost, proceeds, realized_pnl, realized_pnl_krw, price_pnl_krw,"
                " fx_pnl_krw, fee_total, tax_total, cost_estimated, holding_days, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [lot.buy_trade_id, lot.sell_trade_id, lot.stock_id, lot.quantity, lot.buy_price, lot.sell_price,
                 lot.currency, lot.buy_fx, lot.sell_fx, lot.cost, lot.proceeds, lot.realized_pnl,
                 lot.realized_pnl_krw, lot.price_pnl_krw, lot.fx_pnl_krw, lot.fee_total, lot.tax_total,
                 1 if lot.cost_estimated else 0, lot.holding_days, now],
            ))  # fmt: skip
        for p in positions:
            statements.append((
                "INSERT INTO positions (stock_id, quantity, currency, avg_price, avg_fx, cost, cost_krw,"
                " first_buy_date, horizon, price_date, close, fx_date, fx_now, market_value, market_value_krw,"
                " unrealized_pnl, unrealized_pnl_krw, unrealized_price_pnl_krw, unrealized_fx_pnl_krw, weight_pct,"
                " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [p.stock_id, p.quantity, p.currency, p.avg_price, p.avg_fx, p.cost, p.cost_krw, p.first_buy_date,
                 p.horizon, p.price_date, p.close, p.fx_date, p.fx_now, p.market_value, p.market_value_krw,
                 p.unrealized_pnl, p.unrealized_pnl_krw, p.unrealized_price_pnl_krw, p.unrealized_fx_pnl_krw,
                 p.weight_pct, now],
            ))  # fmt: skip
        for v in values:
            statements.append((
                "INSERT INTO portfolio_values (date, value_krw, net_flow_krw, dividends_krw, twr_index, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [v.date, v.value_krw, v.net_flow_krw, v.dividends_krw, v.twr_index, now],
            ))  # fmt: skip
        for r in reviews:
            statements.append((
                f"INSERT INTO trade_reviews ({REVIEW_COLUMNS}) VALUES ({_placeholders(REVIEW_COLUMNS)})",
                [r.buy_trade_id, r.stock_id, r.horizon, r.buy_date, r.last_sell_date, r.currency, r.quantity_sold,
                 r.quantity_bought, 1 if r.partial else 0, r.cost, r.proceeds, r.return_pct, r.holding_days,
                 r.realized_pnl_krw, r.price_pnl_krw, r.fx_pnl_krw, 1 if r.has_snapshot else 0, r.snapshot_as_of,
                 r.score_at_trade, r.signal_type_at_trade, r.sentiment_at_trade, r.factor_scores_at_trade,
                 r.target_pct, r.stop_pct, r.outcome, r.verdict_text, rv.CALC_VERSION, now],
            ))  # fmt: skip
        for g in review_stats:
            statements.append((
                f"INSERT INTO review_stats ({STATS_COLUMNS}) VALUES ({_placeholders(STATS_COLUMNS)})",
                [g.group_key, g.group_kind, g.label, g.n, 1 if g.sample_ok else 0, g.win_rate, g.avg_return_pct,
                 g.median_return_pct, g.avg_holding_days, g.target_rate, g.stop_rate, g.total_pnl_krw,
                 rv.CALC_VERSION, now],
            ))  # fmt: skip
        statements.append((
            "INSERT INTO portfolio_summary (id, as_of_date, totals_json, allocation_json, metrics_json, upcoming_json,"
            " warnings_json, trades_version, calc_version, created_at) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (id) DO UPDATE SET as_of_date = excluded.as_of_date, totals_json = excluded.totals_json,"
            " allocation_json = excluded.allocation_json, metrics_json = excluded.metrics_json,"
            " upcoming_json = excluded.upcoming_json, warnings_json = excluded.warnings_json,"
            " trades_version = excluded.trades_version, calc_version = excluded.calc_version,"
            " created_at = excluded.created_at",
            [today.isoformat(), json.dumps(totals), json.dumps(alloc, ensure_ascii=False),
             json.dumps(metrics, default=str), json.dumps(upcoming, ensure_ascii=False),
             json.dumps(warnings[:30], ensure_ascii=False), version, pf.CALC_VERSION, now],
        ))  # fmt: skip
        # 삭제와 다시 넣기를 나눠 보낸다. 중간에 끊기면 요약 지문이 REBUILDING 으로 남아 화면은 "계산 중", 매도 플래그는
        # 판정을 미룬다(25.644). 다음 실행이 통째로 다시 만든다
        for start in range(0, len(statements), 400):
            client.batch(statements[start : start + 400])

        step = {"trades": len(trades), "receipts": len(receipts), "lots": len(lots), "positions": len(positions),
                "values": len(values), "reviews": len(reviews), "review_groups": len(review_stats),
                "warnings": warnings[:10], "warning_count": len(warnings), "version": version}  # fmt: skip
        db.finish_batch_run(client, run_id, status="partial" if warnings else "success", step_log=step)
        print(f"매매 {len(trades)}건, 배당 {len(receipts)}건 → 체결 묶음 {len(lots)},"
              f" 보유 {len(positions)}, 평가 {len(values)}일")  # fmt: skip
        print(f"복기 {len(reviews)}건, 집단 {len(review_stats)}개")
        # **공개 저장소면 금액·주의 줄을 로그에 찍지 않는다** (25.979) — 평가액·손익·종목이 남이 보는 로그에 남는다
        if visibility.is_public():
            print(f"  (공개 저장소 — 평가액·손익과 주의 {len(warnings)}줄은 로그에 찍지 않음)")
            return 0
        print(f"평가액 {totals['market_value_krw']:,.0f}원, 평가손익 {totals['unrealized_pnl_krw']:,.0f}원,"
              f" 실현손익 {totals['realized_pnl_krw']:,.0f}원")  # fmt: skip
        for warning in warnings[:10]:
            print(f"  주의: {warning}")
        return 0
    finally:
        client.close()


def main() -> int:
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run()


if __name__ == "__main__":
    sys.exit(guard(main))
