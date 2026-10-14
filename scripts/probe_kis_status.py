"""KIS 종목 상태 칸으로 국내 관리종목·거래정지 제외를 한 번 대조한다 — 20회차 P4 1단계.

(docs/factors.md 12.22, docs/infra.md 25.1328)

국내 관리종목·거래정지 제외는 한국거래소 소속부(`SECT_TP_NM`) 글자 하나에 매달려 있고, 그 글자가 실제로 오는지는
확인된 적이 없다(`services/universe.marker_warning`, 25.157·25.1111 — 코스피 소속부가 비어 온다는 기억). KIS 주식현재가
(`FHKST01010100`, 시세 조회 API)의 상태 칸으로 최신 유니버스 스냅샷을 견준다.

- 칸 이름·코드의 뜻은 `[확인필요]` 다 — 그래서 **해석하지 않고 값의 분포만** 그룹(편입 / 관리종목 제외 / 거래정지 제외)
  마다 찍는다. 편입 종목 가운데 관리·정지·정리매매로 보이는 값이 있으면 따로 나열한다(2단계를 열지 정하는 근거)
- 쓰는 것: `universe_members` 최신 국내 스냅샷 한 번(수천 행), KIS 호출은 편입 + 관리·정지 제외 종목 수만큼(초당 10건).
  DB 에 쓰는 것은 KIS 토큰 캐시(`api_tokens`)뿐 — 운영 배치와 같은 경로
- 출력은 비공개 운영 저장소 이슈로만(`probe-kis-status.yml` — 공개 로그에는 찍지 않는다)

실행: python scripts/probe_kis_status.py [--limit N]
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from batch.core import db  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402
from batch.services import universe as uv  # noqa: E402
from batch.sources import kis  # noqa: E402

PRICE_TR = "FHKST01010100"
#: 볼 칸 — 이름은 KIS 문서 표기 그대로, 뜻은 `[확인필요]`
FIELDS = ("iscd_stat_cls_code", "mang_issu_cls_code", "sltr_yn", "temp_stop_yn", "mrkt_warn_cls_code",
          "invt_caful_yn", "short_over_yn")  # fmt: skip
#: 편입 종목인데 이 값이면 따로 나열한다 — 관리(51)·정지(58) 로 알려진 코드와 예·아니오 칸 [확인필요]
SUSPECT = {"iscd_stat_cls_code": {"51", "58"}, "sltr_yn": {"Y"}, "temp_stop_yn": {"Y"}}

SNAPSHOT_SQL = (
    "SELECT s.ticker, s.market, s.section_type, u.included, u.exclude_reason FROM universe_members u"
    " JOIN stocks s ON s.id = u.stock_id"
    f" WHERE s.country = 'KR' AND u.snapshot_date = {db.latest_snapshot_sql()}"
)


def group_of(row: dict) -> str | None:
    if row.get("included"):
        return "편입"
    if row.get("exclude_reason") == uv.REASON_SUPERVISED:
        return "관리종목 제외"
    if row.get("exclude_reason") == uv.REASON_HALTED:
        return "거래정지 제외"
    return None


def suspect(out: dict) -> bool:
    관리 = str(out.get("mang_issu_cls_code") or "N") not in ("N", "")
    return 관리 or any(str(out.get(k) or "") in v for k, v in SUSPECT.items())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, default=0, help="편입 종목을 이만큼만 (0 이면 전부)")
    args = ap.parse_args()
    client = TursoClient()
    try:
        rows = client.execute(SNAPSHOT_SQL, ["KR"]).dicts()
        token, _ = kis.access_token(client)
    finally:
        client.close()
    대상 = [(r, g) for r in rows if (g := group_of(r))]
    편입 = [x for x in 대상 if x[1] == "편입"]
    if args.limit:
        대상 = [x for x in 대상 if x[1] != "편입"] + 편입[: args.limit]
    print(f"스냅샷 국내 {len(rows)}행 — 부를 종목 {len(대상)} (그룹: {dict(Counter(g for _r, g in 대상))})")
    q = kis.QuoteClient(token)

    def 부름(item: tuple[dict, str]) -> tuple[dict, str, dict | None, str | None]:
        r, g = item
        try:
            body = q.get(kis.Q + "inquire-price", PRICE_TR,
                         {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": str(r["ticker"])})
        except kis.KisFailed as exc:
            return r, g, None, str(exc)
        out = body.get("output") if isinstance(body, dict) else None
        return (r, g, out, None) if isinstance(out, dict) else (r, g, None, str((body or {}).get("msg_cd")))

    with ThreadPoolExecutor(max_workers=4) as ex:
        결과 = list(ex.map(부름, 대상))
    실패 = Counter(err for _r, _g, out, err in 결과 if out is None)
    print(f"호출 {q.calls}, 실패 {sum(실패.values())} {dict(실패)}")
    for 그룹 in ("편입", "관리종목 제외", "거래정지 제외"):
        묶음 = [(r, out) for r, g, out, _e in 결과 if g == 그룹 and out is not None]
        print(f"\n[{그룹}] {len(묶음)}종목")
        for 칸 in FIELDS:
            print(f"  {칸}: {dict(Counter(str(out.get(칸)) for _r, out in 묶음).most_common(8))}")
        for 시장 in sorted({str(r["market"]) for r, _o in 묶음}):
            소속부 = Counter(str(r.get("section_type")) for r, _o in 묶음 if str(r["market"]) == 시장)
            print(f"  {시장} 소속부: {dict(소속부.most_common(6))}")
    의심 = [(r, out) for r, g, out, _e in 결과 if g == "편입" and out is not None and suspect(out)]
    print(f"\n편입인데 KIS 상태가 관리·정지·정리매매로 보이는 종목 {len(의심)}")
    for r, out in 의심[:40]:
        칸들 = " ".join(f"{k}={out.get(k)}" for k in FIELDS)
        print(f"  {r['ticker']} {r['market']} 소속부={r.get('section_type')!r} {칸들}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
