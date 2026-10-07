"""업종 채우기 (docs/data-sources.md 15절, batch/services/sectors.py).

유니버스 편입 종목의 stocks.sector 를 공식 산업분류 중분류로 채운다.
  국내  DART 기업개황 induty_code (회사당 1회, 879사 약 4분)
  미국  SEC submissions sic (회사당 1회, 약 1,800사 약 6분)

업종은 거의 바뀌지 않는다. 월 1회면 충분하다. 유니버스에 새로 들어온 종목만 비어 있게 된다.

실행
  python -m batch.jobs.sectors --market KR
  python -m batch.jobs.sectors --market US --limit 20
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from batch import config
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.jobs.financials import target_corps
from batch.services import sectors as sec_map
from batch.sources import dart, sec_edgar

log = logging.getLogger("sectors")

JOB_NAME = "sectors"
# DART 초당 제한은 공개돼 있지 않다 [확인필요]. 배당 수집과 같은 간격.
DART_INTERVAL = 0.25


def update_statement(stock_id: int, name: str, code: str, source: str, now: str) -> tuple[str, list[Any]]:
    return (
        "UPDATE stocks SET sector = ?, sector_code = ?, sector_source = ?, sector_updated_at = ? WHERE id = ?",
        [name, code, source, now, stock_id],
    )


def us_targets(client: TursoClient) -> dict[str, int]:
    rs = client.execute(
        "SELECT s.yahoo_symbol, s.id FROM stocks s JOIN universe_members u ON u.stock_id = s.id"
        " WHERE s.country = 'US' AND u.included = 1 AND s.yahoo_symbol IS NOT NULL"
        f"   AND u.snapshot_date = {db.latest_snapshot_sql()}",
        ["US"],
    )
    return {str(r[0]): int(r[1]) for r in rs.rows}


def run(market: str, limit: int | None = None) -> int:
    client = TursoClient()
    try:
        db.apply_migrations(client)
        today = datetime.now(UTC).date().isoformat()
        run_id = db.start_batch_run(client, job_name=JOB_NAME, market=market, trade_date=today)
        now = db.now_iso()
        statements: list[tuple[str, list[Any]]] = []
        names: Counter[str] = Counter()
        missing: list[str] = []
        error = ""
        잇단멈춤 = False  # 국내: 잇단 실패·한도로 도중에 멈췄나 (25.609·25.611)

        if market == "KR" and db.usage_blocked_today(client, "dart_opendart"):
            # 부르기 전에 본다 (docs/infra.md 25.388) — 한도가 찬 날 100번 더 부르지 않는다
            db.finish_batch_run(client, run_id, status="skipped", error_text="DART 일일 한도가 이미 찼습니다")
            print("DART 일일 한도가 이미 찼습니다. 국내 업종 수집을 건너뜁니다")
            return 0
        if market == "KR":
            corps = target_corps(client)[: limit or None]
            calls = 0
            안_센것 = 0
            잇단실패 = 0
            failed = 0
            첫원인 = ""
            for index, (corp_code, stock_id) in enumerate(corps):
                if index:
                    time.sleep(DART_INTERVAL)
                result = dart.fetch_company_industry(corp_code)
                calls += 1  # noqa: SIM113 — 한도로 멈추면 enumerate 와 어긋난다
                안_센것 += result.attempts  # 부르지 않은 실패(키 없음·오프라인)는 세지 않는다 (25.607)
                # **중간중간 센다** (2026-09-22, 25.117). 끝나고 한 번만 적으면 도중에 죽은 날
                # 879회가 통째로 사라진다. 그리고 우리 카운터가 한도에 닿으면 **먼저** 멈춘다
                상태 = None
                if 안_센것 >= 100:
                    상태 = db.record_and_guard(
                        client, "dart_opendart", count=안_센것,
                        limit_value=dart.DAILY_LIMIT, warn_at_pct=80, label="DART",
                    )  # fmt: skip
                    안_센것 = 0
                if result.limit_state == "blocked":
                    error = f"{index}/{len(corps)} 에서 DART 한도: {result.error}"
                    잇단멈춤 = True  # 한도로 도중에 멈춘 것도 남은 회사가 비었다 (25.611)
                    break
                # **실패와 "업종코드 없음" 을 가른다** (docs/infra.md 25.607, 감사). 예전에는 키 오류(011)·점검(800)·
                # 5xx 가 원인 없이 `missing` 에 섞였고, 전부 실패해도 끝까지 불렀다
                if not result.ok:
                    failed += 1
                    missing.append(corp_code)
                    첫원인 = 첫원인 or f"{corp_code}: {result.error}"
                    잇단실패 += 1
                    if 잇단실패 >= dart.MAX_CONSECUTIVE_FAILURES:
                        error = (f"{index + 1}/{len(corps)} 에서 DART 실패 {잇단실패}번 잇달아 멈춤"
                                 f" — {corp_code}: {result.error}")  # fmt: skip
                        잇단멈춤 = True
                        break
                    # 실패여도 **우리 카운터가 찼으면 멈춘다** (25.609, 교차검증) — 예전에는 `continue` 가 아래 확인을
                    # 건너뛰어, 100번째 호출이 실패인 날 한도 뒤에도 100회를 더 불렀다
                    if 상태 == "blocked":
                        error = f"{index + 1}/{len(corps)} 에서 DART 한도(우리 카운터)"
                        잇단멈춤 = True  # 이 회사도 실패했으니 마지막이어도 남은 것이 있다 (25.611·25.614)
                        break
                    continue
                잇단실패 = 0
                name, code = sec_map.kr_sector(result.data)
                if name is None or code is None:
                    missing.append(corp_code)
                else:
                    names[name] += 1
                    statements.append(update_statement(stock_id, name, f"KSIC {result.data}", "dart_company", now))
                # 받은 응답은 담고 나서 멈춘다 (docs/infra.md 25.386)
                if 상태 == "blocked":
                    error = f"{index + 1}/{len(corps)} 에서 DART 한도(우리 카운터)"
                    잇단멈춤 = index + 1 < len(corps)  # 마지막 회사까지 받았으면 남은 것이 없다 (25.611)
                    break
            # **이름과 수를 다른 DART 작업과 똑같이 맞춘다** (2026-09-21, docs/infra.md 25.68).
            # 여기만 `"dart"` 였다 — 재무·배당·공시는 `"dart_opendart"` 로 센다. 이름이 갈리면
            # 하루 한도(20,000)를 보는 카운터가 **둘로 쪼개져** 여기 쓴 만큼은 안 보인다.
            # 그리고 `len(corps)` 가 아니라 **실제로 부른 횟수**를 센다 — 다섯 번 만에 한도에
            # 걸려 멈춰도 879 를 적으면 그날 남은 DART 작업이 헛되이 막힌다
            if 안_센것:
                db.record_and_guard(
                    client, "dart_opendart", count=안_센것,
                    limit_value=dart.DAILY_LIMIT, warn_at_pct=80, label="DART",
                )  # fmt: skip
            targets = len(corps)
            if failed and not error:
                error = f"{failed}사 실패 — 예: {첫원인}"
        else:
            ids = us_targets(client)
            sec = sec_edgar.SecClient()
            ticker_map = sec.ticker_map()
            by_cik: dict[str, list[int]] = {}
            for symbol, stock_id in sorted(ids.items()):
                if symbol in ticker_map:
                    by_cik.setdefault(ticker_map[symbol], []).append(stock_id)
                else:
                    missing.append(symbol)
            ciks = sorted(by_cik.items())[: limit or None]
            for index, (cik, stock_ids) in enumerate(ciks):
                try:
                    sic = sec.sic(cik)
                except sec_edgar.SecBlocked as exc:
                    error = f"{index}/{len(ciks)} 에서 SEC 차단: {exc}"
                    break
                except Exception as exc:  # noqa: BLE001 — 한 회사 실패(시간 초과 등)로 전체를 멈추지 않는다
                    log.warning("SEC submissions %s 실패: %s", cik, exc)
                    missing.append(cik)
                    continue
                name, code = sec_map.us_sector(sic)
                if name is None or code is None:
                    missing.append(cik)
                    continue
                for stock_id in stock_ids:
                    names[name] += 1
                    statements.append(update_statement(stock_id, name, f"SIC {sic}", "sec_submissions", now))
            db.record_api_call(client, "sec_edgar", count=sec.calls)
            targets = len(ids)

        for start in range(0, len(statements), 200):
            client.batch(statements[start : start + 200])

        step = {
            "targets": targets,
            "updated": len(statements),
            "missing": len(missing),
            "missing_sample": missing[:20],
            "groups": len(names),
            "groups_30_plus": sum(1 for n in names.values() if n >= 30),
            "top": names.most_common(15),
            "error": error,
        }
        # 잇단 실패·한도로 **도중에 멈췄으면 failed** (25.609·25.611, 교차검증). 따라잡기(`--skip-if-ran-within 30`)는
        # partial 도 "돌았다" 로 세어(25.320 이 업종에 일부러 준 기본값) 남은 수백 사를 다음 달까지 비워 뒀다.
        # failed 면 다음 날 다시 돈다
        status = "failed" if not statements or 잇단멈춤 else ("partial" if error else "success")
        db.finish_batch_run(client, run_id, status=status, step_log=step, error_text=error or None)

        print(f"{market} 대상 {targets} / 업종 채움 {len(statements)} / 못 채움 {len(missing)}")
        print(f"업종 {len(names)}개, 30종목 이상 {step['groups_30_plus']}개 (팩터 비교 집단 문턱)")
        for name, count in names.most_common(15):
            print(f"  {count:4d}  {name}")
        if error:
            print(f"  주의: {error}")
        return 0 if status != "failed" else 1
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="업종 채우기")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--limit", type=int, help="앞에서부터 몇 개만 (시험용)")
    parser.add_argument(
        "--skip-if-ran-within", dest="skip_days", type=float,
        help="최근 이 일수 안에 성공했으면 건너뛴다 (D1 따라잡기, docs/infra.md 25.8)",
    )  # fmt: skip
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    if args.skip_days is not None:
        with TursoClient() as client:
            if db.ran_within(client, JOB_NAME, args.skip_days, args.market):
                print(f"최근 {args.skip_days:g}일 안에 돌았습니다. 건너뜁니다")
                return 0
    return run(args.market, args.limit)


if __name__ == "__main__":
    sys.exit(guard(main))
