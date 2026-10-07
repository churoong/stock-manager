"""국내 수정주가 채우기 (docs/adjust.md).

계산은 `batch/services/adjust.py` 가 한다. 이 파일은 읽어서 넘기고 결과를 쓴다.

**원본을 건드리지 않는다.** `close` 는 한국거래소가 준 미조정 값 그대로 두고
`adj_close` 만 채운다. 잘못 계산됐다면 이 배치를 다시 돌려 덮으면 된다.

언제 도나 (docs/adjust.md 3.1 — 2026-09-28 바로잡음, infra 25.571)
  - **예약 없음. 일일 배치는 부르지 않는다** — 전 종목이 하루 읽기 예산의 3분의 2 라서. 일일 배치는 그날치
    계수만 보고 기업행위가 보이면 이 배치를 돌리라고 알린다(`daily._detect_kr_actions`)
  - 국내 백필 워크플로(`backfill-kr.yml`) 끝에서 돈다. D1 따라잡기(`d1-catchup.yml`) 의 백필 뒤에는 돌지 않는다
  - 손으로: python -m batch.jobs.adjust_kr (Actions → "국내 수정주가")

실행
  python -m batch.jobs.adjust_kr                 # 전 종목
  python -m batch.jobs.adjust_kr --ticker 005930 # 한 종목만 (확인용)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import adjust as adj

log = logging.getLogger("adjust_kr")

JOB_NAME = "adjust_kr"

#: 한 번에 읽는 종목 수. 종목당 5년이면 1,200행이라 20종목이면 24,000행이다
CHUNK_STOCKS = 20

#: 한 문장에 담는 UPDATE 수
UPDATE_CHUNK = 400


def load_stock_ids(client: TursoClient, ticker: str | None) -> list[int]:
    if ticker:
        rs = client.execute(
            "SELECT id FROM stocks WHERE country = 'KR' AND ticker = ?", [ticker]
        )
    else:
        rs = client.execute(
            "SELECT DISTINCT s.id FROM stocks s JOIN prices p ON p.stock_id = s.id"
            # **이은 ETF 는 조정하지 않는다** (docs/infra.md 25.916, 감사). ETF 는 그날치만
            # 받고(`daily._store_kr_etf_day`) 구멍 메우기
            # 길(야후 대신 받기·백필)이 없다. 하루 빠지면 다음 날 "종가 비 ÷ 등락률" 이 그 이틀 수익을 담아 2% 문턱을
            # 넘고, 그 앞 전체가
            # 가짜 기업행위로 틀어졌다(예: +3% 빠진 날 → 과거 수정종가 3% 부풀고 보유 평가·TWR 이 3% 틀림). 국내 ETF
            # 분할은 드물다 —
            # 수정종가가 없으면 평가는 종가를 쓴다(`SPLIT_ONLY_PRICE_SQL`)
            " WHERE s.country = 'KR' AND s.asset_type = 'stock' ORDER BY s.id"
        )
    return [int(r[0]) for r in rs.rows]


def load_days(
    client: TursoClient, stock_ids: list[int]
) -> tuple[dict[int, list[adj.Day]], dict[int, dict[str, float | None]]]:
    """종목별 일봉과 **지금 저장된** 수정종가. 저장된 값과 같으면 다시 쓰지 않으려고 함께 읽는다."""
    placeholders = ", ".join(["?"] * len(stock_ids))
    rs = client.execute(
        "SELECT stock_id, date, close, change_pct, adj_close FROM prices"
        f" WHERE stock_id IN ({placeholders}) AND close IS NOT NULL"
        " ORDER BY stock_id, date",
        list(stock_ids),
    )
    out: dict[int, list[adj.Day]] = {}
    stored: dict[int, dict[str, float | None]] = {}
    for row in rs.dicts():
        stock_id = int(row["stock_id"])
        out.setdefault(stock_id, []).append(
            adj.Day(
                date=str(row["date"]),
                close=float(row["close"]),
                change_pct=None if row["change_pct"] is None else float(row["change_pct"]),
            )
        )
        stored.setdefault(stock_id, {})[str(row["date"])] = (
            None if row["adj_close"] is None else float(row["adj_close"])
        )
    return out, stored


# 수정종가가 종가와 이만큼 가까우면 "같다" 로 본다. 조정 계수의 부동소수 찌꺼기를 흡수한다
SAME_REL = 1e-9


def rows_to_write(
    days: list[adj.Day], adjusted: list[adj.Adjusted], stored: dict[str, float | None]
) -> list[tuple[str, float | None]]:
    """실제로 바뀌는 행만 (날짜, 새 값). 새 값이 None 이면 "종가와 같다" 라서 비운다.

    **왜 이렇게 하나 (docs/infra.md 25.7).** 예전에는 모든 가격 행에 UPDATE 를 한 번씩 썼다.
    분할·감자가 없는 종목은 수정종가가 종가와 같은데도. 2026-09-18 D1 첫날 시세 32일치(8.8만 행)
    뒤에 이 단계가 같은 수만큼 또 써서 하루 한도를 넘겼고, Turso 쓰기 2,787만 행에도 한몫했다.

    종가와 같은 날은 비워 둔다. 읽는 쪽은 모두 `COALESCE(adj_close, close)` 라 결과가 같다.
    등락률이 없어 조정할 수 없는 종목(\"모른다\")도 비어 있으므로, 국내에서 adj_close 가 비었다는 것은
    "종가와 같거나 아직 모른다" 는 뜻이다. 둘을 가르는 코드는 없다.
    """
    closes = {d.date: d.close for d in days}
    out: list[tuple[str, float | None]] = []
    for a in adjusted:
        close = closes.get(a.date)
        if close is None:
            continue
        target: float | None = a.adj_close
        if abs(a.adj_close - close) <= SAME_REL * max(1.0, abs(close)):
            target = None
        current = stored.get(a.date)
        if target is None and current is None:
            continue
        if target is not None and current is not None and abs(target - current) <= SAME_REL * max(1.0, abs(target)):
            continue
        out.append((a.date, target))
    return out


# 종목당 가진 가격 행 어림(5년). 예산 어림에만 쓴다
ROWS_PER_STOCK = 1_250


def run(ticker: str | None = None, force: bool = False) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date().isoformat()
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME, market="KR", trade_date=today
        )

        stock_ids = load_stock_ids(client, ticker)
        if not stock_ids:
            db.finish_batch_run(client, run_id, status="failed", error_text="국내 가격이 없습니다")
            print("국내 가격이 없습니다")
            return 1

        # 전 종목의 가격을 통째로 훑는 작업이다. 시작 전에 읽기 예산을 본다 (docs/infra.md 24절)
        ok, text = db.check_read_budget(client, len(stock_ids) * ROWS_PER_STOCK, "국내 수정주가")
        print(text)
        if not ok and not force:
            db.finish_batch_run(client, run_id, status="skipped", error_text=text)
            print("이번 달 읽기 예산이 모자랍니다. --force 로 넘길 수 있지만 계정이 막힐 수 있습니다")
            return 1

        updated = 0
        with_action = 0
        with_suspect = 0
        without_change_pct = 0
        action_samples: list[dict[str, Any]] = []
        suspect_samples: list[dict[str, Any]] = []

        for start in range(0, len(stock_ids), CHUNK_STOCKS):
            ids = stock_ids[start : start + CHUNK_STOCKS]
            days_by_stock, stored_by_stock = load_days(client, ids)
            for stock_id, days in days_by_stock.items():
                if not days:
                    continue
                if all(d.change_pct is None for d in days):
                    # 등락률을 받기 전에 저장된 구간이다. 조정하지 않고 비워 둔다.
                    # 여기서 close 를 그대로 adj_close 에 넣으면 "조정된 값" 으로 오해된다
                    without_change_pct += 1
                    continue

                adjusted = adj.adjust(days)
                # 바뀌는 행만 쓴다 (rows_to_write 주석). 대부분의 종목은 0행이다
                statements: list[tuple[str, list[Any]]] = [
                    ("UPDATE prices SET adj_close = ? WHERE stock_id = ? AND date = ?", [value, stock_id, day])
                    for day, value in rows_to_write(days, adjusted, stored_by_stock.get(stock_id, {}))
                ]
                db.note_prices_touched((stock_id, args[2]) for _sql, args in statements)  # 시세 사본 (25.888)
                for chunk_start in range(0, len(statements), UPDATE_CHUNK):
                    client.batch(statements[chunk_start : chunk_start + UPDATE_CHUNK])
                updated += len(statements)

                found = [a for a in adjusted if adj.is_action(a.factor)]
                if found:
                    with_action += 1
                    if len(action_samples) < 10:
                        action_samples.append(
                            {"stock_id": stock_id, "dates": [(a.date, round(a.factor, 4)) for a in found[:3]]}
                        )
                # **구멍 위에서 판정된 것은 따로 센다** (docs/infra.md 25.132).
                # 계수 식은 "앞 행 = 바로 전 거래일" 을 전제한다. 수집이 하루 빠지면 그 계수가
                # 빠진 구간의 수익률이 되고, 수정계수는 **그 이전 전체**에 곱해진다
                의심 = adj.suspect_actions(adjusted)
                if 의심:
                    with_suspect += 1
                    if len(suspect_samples) < 10:
                        suspect_samples.append({
                            "stock_id": stock_id,
                            "dates": [(a.date, round(a.factor, 4), a.gap_days) for a in 의심[:3]],
                        })  # fmt: skip

        step = {
            "stocks": len(stock_ids),
            "rows_updated": updated,
            "stocks_with_action": with_action,
            "stocks_with_suspect_action": with_suspect,
            "suspect_samples": suspect_samples,
            "stocks_without_change_pct": without_change_pct,
            "samples": action_samples,
            "calc_version": adj.CALC_VERSION,
        }
        db.finish_batch_run(client, run_id, status="success", step_log=step)
        print(f"종목 {len(stock_ids)}개 중 {with_action}개에서 기업행위를 찾아 {updated:,}행을 조정했습니다")
        if without_change_pct:
            print(f"  등락률이 없어 건너뛴 종목 {without_change_pct}개 (백필을 다시 돌리면 채워진다)")
        for sample in action_samples[:5]:
            print(f"  종목 {sample['stock_id']}: {sample['dates']}")
        if with_suspect:
            print(
                f"  ⚠ 앞뒤 사이에 빠진 거래일이 있는 기업행위가 {with_suspect}개 종목에 있습니다"
                " (docs/infra.md 25.132·25.572)."
                " 거래정지를 낀 진짜 분할일 수도, 수집 구멍일 수도 있습니다 — 그 날짜를 백필 **--refresh** 로 한 번"
                " 되받아(--refresh 가 없으면 그 시장에 다른 종목 행이 있는 날은 건너뛴다, 25.574) 행이 생기면 이 작업을"
                " 다시 돌리고, 생기지 않으면(정지) 그대로 두면 됩니다"
            )
            for sample in suspect_samples[:5]:
                print(f"    종목 {sample['stock_id']}: {sample['dates']}  (날짜, 계수, 앞 행과의 일수)")
        return 0
    finally:
        client.close()


def main() -> int:
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="국내 수정주가 채우기")
    parser.add_argument("--ticker", help="한 종목만 (확인용)")
    parser.add_argument("--force", action="store_true", help="읽기 예산이 모자라도 강행")
    args = parser.parse_args()
    return run(args.ticker, args.force)


if __name__ == "__main__":
    sys.exit(guard(main))
