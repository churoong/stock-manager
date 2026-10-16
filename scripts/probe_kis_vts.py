"""KIS모의투자 시험 주문 — 실제로 주문·체결·잔고가 도는지 한 번 본다 (docs/paper.md 15장, docs/infra.md 25.1491).

2026-10-16 사용자 질문 "KIS모의투자 실제 매매가 되는지 테스트 가능해?" 로 만들었다.
손으로만 돌린다(`probe-kis-vts.yml`).
가상 돈(모의 도메인·모의 키)이다 — `sources/kis_vts` 의 세션만 쓰므로 실전 도메인으로 갈 길이 없다.

- **국내**(정규장 09:00~15:20 KST 안일 때만): 1주 시장가 매수 → 체결 조회로 찰 때까지(최대 `WAIT_S`) →
  같은 1주 시장가 매도 → 다시 확인 → 잔고에서 그 종목 수량이 처음과 같은지. 사고팔아 **남기지 않는다** —
  남으면 일일 배치의 잔고 대조가 "어긋남" 으로 센다
- **미국**: 매수가능금액 조회(원화만 있어도 환전 뒤 살 수 있나 — 15.5 `[확인필요]`) · 예약 접수 시간이면
  **1달러 지정가 1주 예약 매수** — 찰 수 없는 값이라 접수만 본다(예약은 당일, 마감 뒤 자동취소)
- 계좌번호·토큰은 찍지 않는다. 출력은 워크플로가 `ops_tee` 로 비공개 운영 이슈에만 보낸다(잔고 수량·금액이 실리므로)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from batch.core import calendar as cal  # noqa: E402
from batch.core.client import TursoClient  # noqa: E402
from batch.jobs import broker_mirror as job  # noqa: E402
from batch.sources import kis_vts as kv  # noqa: E402
from batch.sources.kis import KisFailed  # noqa: E402

#: 체결을 기다리는 최대 초 — 모의 시장가는 대개 몇 초 안에 찬다 `[확인필요]`
WAIT_S = 90
POLL_S = 5
KR_OPEN, KR_CLOSE = dtime(9, 0), dtime(15, 20)
#: 미국 예약 시험 단가 — 찰 수 없는 값(접수만 본다)
US_PROBE_PRICE = 1.0


def _찾기(sess: kv.Session, day: str, odno: str) -> kv.Execution | None:
    체결, _다 = sess.executions(day, day)
    return next((e for e in 체결 if e.order_no.lstrip("0") == odno.lstrip("0")), None)


def _기다려_찬_것(sess: kv.Session, day: str, odno: str) -> kv.Execution | None:
    끝 = time.monotonic() + WAIT_S
    while True:
        e = _찾기(sess, day, odno)
        if e and (e.filled >= e.quantity or e.rejected or e.cancelled) or time.monotonic() > 끝:
            return e
        time.sleep(POLL_S)


def _보유(sess: kv.Session, code: str) -> int:
    보유, _합 = sess.balance()
    return next((h.quantity for h in 보유 if h.code == code), 0)


def kr(sess: kv.Session, code: str, now: datetime) -> bool:
    현지 = now.astimezone(cal.market_tz("KR"))
    print(f"[국내] {현지:%Y-%m-%d %H:%M} KST · 종목 {code}")
    처음 = _보유(sess, code)
    print(f"  잔고 조회 OK — 시험 종목 보유 {처음}주")
    if not cal.is_session("KR", 현지.date()) or not (KR_OPEN <= 현지.time() < KR_CLOSE):
        print("  정규장 밖 — 주문은 내지 않음(잔고·토큰만 확인)")
        return True
    day = 현지.strftime("%Y%m%d")
    ok = True
    for side in ("buy", "sell"):
        try:
            받음 = sess.order(side, code, 1, None)
        except KisFailed as exc:
            print(f"  {side} 1주 시장가 → 거부/모름: {exc}")
            return False
        e = _기다려_찬_것(sess, day, 받음.order_no)
        if e is None:
            print(f"  {side} 1주 시장가 → 접수 {받음.order_no} · {WAIT_S}초 안에 체결 조회에 안 보임")
            ok = False
        else:
            print(f"  {side} 1주 시장가 → 접수 {받음.order_no} · 체결 {e.filled}/{e.quantity}주 · 평균가 {e.avg_price}"
                  f" · 거부 {e.rejected} · 취소 {e.cancelled}")  # fmt: skip
            ok = ok and e.filled >= 1
        if side == "buy" and not ok:
            print("  매수가 안 차서 매도는 내지 않음(남는 수량 없게)")
            break
    끝 = _보유(sess, code)
    같음 = "와 같음" if 끝 == 처음 else " — 다름! 남은 수량 확인 필요"
    print(f"  끝 잔고 — 시험 종목 보유 {끝}주 (처음 {처음}주{같음})")
    return ok and 끝 == 처음


def us(sess: kv.Session, code: str, exchange: str, now: datetime) -> bool:
    print(f"[미국] 종목 {code}({exchange})")
    보유, _합 = sess.balance_us()
    print(f"  잔고 조회 OK — 보유 종목 {len(보유)}")
    ok = True
    try:
        가능 = sess.buyable_us(code, exchange, 100.0)
        칸 = ("tr_crcy_cd", "ord_psbl_frcr_amt", "ovrs_ord_psbl_amt", "echm_af_ord_psbl_amt", "max_ord_psbl_qty",
             "echm_af_ord_psbl_qty", "ovrs_max_ord_psbl_qty", "exrt")  # fmt: skip
        print("  매수가능(100달러 기준): " + " · ".join(f"{k}={가능.get(k)}" for k in 칸))
    except KisFailed as exc:
        print(f"  매수가능금액 조회 실패: {exc}")
        ok = False
    # 예약은 **다음 정규장**으로 간다 — 한국 낮(뉴욕은 전날 밤)에는 현지 "오늘" 장이 이미 끝났으니 다음 장을 본다.
    # 일일 배치는 개장 전 현지 날짜로 돌아 그날이 곧 다음 장이다
    장 = cal.local_today("US")
    개장 = cal.session_open_utc("US", 장)
    if 개장 is None or now >= 개장 - job.US_RESV_CUTOFF:
        장 = cal.next_session("US", 장)
    막힘 = job._us_window(now, 장)
    if 막힘:
        print(f"  예약 시험 안 함 — {막힘}")
        return ok
    try:
        받음 = sess.order_us("buy", code, exchange, 1, US_PROBE_PRICE)
        print(f"  예약 매수 1주 @ {US_PROBE_PRICE:.2f}달러(찰 수 없는 값) → 접수, 예약번호 {받음.order_no}"
              " — 마감 뒤 자동취소")
    except KisFailed as exc:
        print(f"  예약 매수 → 거부/모름: {exc}")
        ok = False
    return ok


def _진단() -> str:
    """어느 칸이 비었나 — 값은 찍지 않는다. 계좌는 숫자 자릿수와 숫자 아닌 글자 수만(8 또는 10 이어야 한다)."""
    e = kv._vars(None)
    계좌 = str(e.get(kv.ACCOUNT_ENV) or "")
    숫자 = sum(ch.isdigit() for ch in 계좌)
    키, 비밀 = ("있음" if e.get(k) else "없음" for k in (kv.KEY_ENV, kv.SECRET_ENV))
    칸 = "없음" if not 계좌 else f"숫자 {숫자}자리 + 그 밖 글자 {len(계좌) - 숫자}개"
    return f"  진단: 앱키 {키} · 시크릿 {비밀} · 계좌 {칸}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=("KR", "US", "both"), default="both")
    ap.add_argument("--kr-code", default="005930")
    ap.add_argument("--us-code", default="AAPL")
    ap.add_argument("--us-exchange", default="NASD", choices=sorted(set(kv.US_EXCHANGE.values())))
    a = ap.parse_args()
    if not kv.configured():
        print("KIS모의투자 키·계좌 없음 — 시크릿 세 개(KIS_VTS_APP_KEY·KIS_VTS_APP_SECRET·KIS_VTS_ACCOUNT)를 확인")
        print(_진단())
        return 1
    now = datetime.now(UTC)
    with TursoClient() as client:
        try:
            sess = kv.Session(client, now)
        except KisFailed as exc:
            print(f"토큰 실패: {exc}")
            return 1
        print(f"토큰 OK ({'새로 받음' if sess.calls else '저장된 것'}) · 계좌 형식 OK(상품코드 {sess.prdt})")
        ok = True
        if a.market in ("KR", "both"):
            ok = kr(sess, a.kr_code, now) and ok
        if a.market in ("US", "both"):
            ok = us(sess, a.us_code, a.us_exchange, datetime.now(UTC)) and ok
        print(f"호출 {sess.calls}회 · 한도 초과 뒤 다시 {sess.rate_retries}회 · 결과 {'통과' if ok else '확인 필요'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
