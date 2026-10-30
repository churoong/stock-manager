"""위험 모듈 돌연변이 점검 — 장부·체결 판정·KIS모의투자 주문 경로·위험 관문·손절의 핵심 규칙을 일부러 깨뜨려 시험이
잡는지 본다 (docs/infra.md 25.1743, 2026-10-23 사용자 "그렇게 진행해줘" — 아이디어 ⑥).

"한 건의 끝" 의 되돌리기 확인은 그 건이 바꾼 줄만 본다. 이 목록은 **이미 있는 규칙**이 나중 변경으로 시험 없이
풀리지 않았는지를 정기적으로 본다 — 가상모의투자·KIS모의투자 성적이 실전 전환의 근거라(CLAUDE.md "시스템의 중심")
장부가 조용히 틀리면 그 근거가 무너진다.

로컬 전용 — Actions 분을 쓰지 않는다(CLAUDE.md 비용 규칙). 쓰는 법:

    python scripts/mutation_check.py            # 전부
    python scripts/mutation_check.py --only judge_limit_buy

**커밋된 HEAD** 를 임시 작업 사본(git worktree)으로 떠서 그 안에서 바꾸고 돌린다 — 작업 트리를 건드리지 않는다.
살아남은 돌연변이(시험이 안 깨짐)나 목록이 썩은 항목(원문이 그 파일에 정확히 한 번 있지 않음)이 있으면 종료 코드 1.
목록이 썩지 않게 `tests/test_mutation_catalog_1743.py` 가 보통 시험에서 원문 한 번·시험 파일 있음을 본다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

뿌리 = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Mutation:
    name: str
    path: str  # 저장소 뿌리 기준
    old: str  # 그 파일에 정확히 한 번
    new: str
    tests: tuple[str, ...]  # 이 가운데 하나라도 깨져야 "잡힘"
    why: str


#: 돌연변이 목록 — 하나하나가 "이것이 조용히 바뀌면 돈·장부·주문이 틀린다" 인 규칙이다. 시험은 그 규칙을 이름으로 보는
#: 파일만 — 전체 묶음을 돌리면 한 번에 10분이라 정기 점검으로 못 쓴다
MUTATIONS: tuple[Mutation, ...] = (
    Mutation("judge_limit_buy", "batch/services/paper.py",
             "        if bar.close <= order.limit_price:\n", "        if bar.close < order.limit_price:\n",
             ("tests/test_paper_engine_1444.py", "tests/test_paper_accounts_1458.py"),
             "가상 지정가 매수는 종가 ≤ 회차 가격이면 찬다 — 같을 때를 빠뜨리면 체결이 줄어 성적이 틀린다"),
    Mutation("judge_halted", "batch/services/paper.py",
             "    if bar.volume is not None and bar.volume <= 0:\n        return True\n",
             "    if False:\n        return True\n",
             ("tests/test_paper_engine_1444.py", "tests/test_missing_bar_1713.py"),
             "거래량 0(정지)인 날 전일 종가로 지정가 매수가 거짓으로 찬다"),
    Mutation("limit_locked_off", "batch/services/paper.py",
             '    if order.currency != "KRW" or order.order_type != "market" or bar.change_pct is None:\n'
             "        return False\n",
             "    if True:\n        return False\n",
             ("tests/test_paper_limit_locked_1553.py",),
             "상한가 시장가 매수·하한가 시장가 매도를 찬 것으로 본다(25.1549)"),
    Mutation("fill_limit_buy_slippage", "batch/services/paper.py",
             '    slip = 0.0 if order.side == "buy" and order.order_type != "market" else'
             " gross * costs.slippage_pct / 100\n",
             "    slip = gross * costs.slippage_pct / 100\n",
             ("tests/test_paper_engine_1444.py", "tests/test_paper_accounts_1458.py"),
             "지정가 매수는 그 값보다 비싸게 찰 수 없다 — 슬리피지를 얹으면 원가가 부푼다"),
    Mutation("fill_sell_tax", "batch/services/paper.py",
             "        tax = gross * costs.sell_tax_pct / 100\n", "        tax = 0.0\n",
             ("tests/test_paper_engine_1444.py", "tests/test_paper_accounts_1458.py"),
             "매도 세금을 빼먹으면 실현손익이 부푼다"),
    Mutation("positions_cost_on_sell", "batch/services/paper.py",
             "        p.cost *= 남김\n", "        p.cost *= 1.0\n",
             ("tests/test_paper_engine_1444.py", "tests/test_paper_accounts_1458.py"),
             "일부 매도 때 원가를 덜지 않으면 남은 보유의 평균 단가·손익이 틀린다"),
    Mutation("positions_oversell", "batch/services/paper.py",
             "        if f.quantity > p.quantity:\n", "        if False:\n",
             ("tests/test_paper_engine_1444.py", "tests/test_paper_accounts_1458.py"),
             "보유보다 많은 매도 체결을 장부가 받아들인다"),
    Mutation("reconcile_always_ok", "batch/services/paper.py",
             "    if abs(차) > RECON_TOLERANCE_KRW:\n", "    if False:\n",
             ("tests/test_paper_engine_1444.py",),
             "장부 검산이 늘 '맞음' — 지워지거나 고쳐진 장부 행을 못 본다"),
    Mutation("twr_flow", "batch/services/paper.py",
             "    return prev_index * (equity - flow_since) / prev_equity\n",
             "    return prev_index * equity / prev_equity\n",
             ("tests/test_paper_engine_1444.py",),
             "입금을 수익으로 센다 — 시간가중 수익률이 부푼다"),
    Mutation("kis_vts_tr_guard", "batch/sources/kis_vts.py",
             ' or not tr.startswith("V"):\n', ":\n",
             ("tests/test_kis_vts_1475.py",),
             "모의 tr_id 가 아니어도 보낸다 — 실계좌 주문 경로가 열린다(CLAUDE.md 실주문 금지)"),
    Mutation("kis_vts_domain_guard", "batch/sources/kis_vts.py",
             '    if not url.startswith("https://openapivts.koreainvestment.com:29443/") or',
             '    if not url.startswith("https://") or',
             ("tests/test_kis_vts_1475.py",),
             "모의 도메인이 아니어도 보낸다 — 실전 도메인으로 주문이 나갈 수 있다"),
    Mutation("kis_daily_cap", "batch/services/broker_mirror.py",
             "    if len(낼것) > 남은:\n", "    if False:\n",
             ("tests/test_kis_vts_1475.py", "tests/test_kis_vts_us_1485.py"),
             "KIS모의투자 하루 주문 상한을 넘겨 낸다"),
    Mutation("risk_daily_loss", "batch/services/risk_gate.py",
             "    if 손실 is not None and 손실 < DAILY_LOSS_HALT_PCT:", "    if False:",
             ("tests/test_risk_gate_1451.py", "tests/test_risk_gate_1629.py"),
             "하루 손실 한도를 넘은 날에도 새로 산다"),
    Mutation("risk_cash", "batch/services/risk_gate.py",
             "    if 쓴현금 + c.cash_cost > ctx.cash_available_krw + 1:\n", "    if False:\n",
             ("tests/test_risk_gate_1451.py", "tests/test_risk_gate_1629.py"),
             "현금(미체결 예약을 뺀)을 넘겨 산다 — 장부 현금이 음수가 된다"),
    Mutation("stop_loss_boundary", "batch/services/sell_flags.py",
             '        if ret <= rules["stop_pct"]:\n', '        if ret < rules["stop_pct"]:\n',
             ("tests/test_sell_flags.py", "tests/test_sell_flag_rounding_1324.py"),
             "손절선에 정확히 닿은 날 손절 플래그가 안 걸린다(가상모의투자는 규칙서대로 판다)"),
    # KIS모의투자 장중 손절·익절 감시 (25.1838 — 25.1768 부터 장중에 실제 모의 매도를 내는데 목록에 없었다)
    Mutation("guard_line_boundary", "batch/jobs/intraday_guard.py",
             '    return 비 <= 0 if 쪽 == "아래" else 비 >= 0\n', '    return 비 < 0 if 쪽 == "아래" else 비 >= 0\n',
             ("tests/test_intraday_guard_1768.py",),
             "장중 현재가가 손절선에 정확히 닿아도 KIS모의투자 매도를 내지 않는다"),
    Mutation("guard_qty_cap", "batch/jobs/intraday_guard.py",
             '        수량 = min(int(h.sellable), int(선.get("qty") or h.sellable))\n',
             '        수량 = int(h.sellable)\n',
             ("tests/test_intraday_guard_1768.py",),
             "장중 감시가 H 수량을 넘어 사람이 그 모의계좌에서 산 주식까지 판다"),
    Mutation("guard_skip_rate_only", "batch/jobs/intraday_guard.py",
             "        if any(c in str(exc) for c in kv.RATE_LIMIT_CODES) and not isinstance(exc, kv.NoResponse):\n",
             "        if not isinstance(exc, kv.NoResponse):\n",
             ("tests/test_guard_transient_1835.py",),
             "토큰 무효 같은 지속 장애도 '이 회차만 쉼' 으로 삼켜 실패 알림이 안 간다"),
    # 스윙 S(10-29 개설) — 판정 경계와 권리락 되돌림(25.2004)
    Mutation("swing_stop_boundary", "batch/services/shadow.py",
             "        if 수익 <= 아래:\n",
             "        if 수익 < 아래:\n",
             ("tests/test_shadow_1862.py",),
             "스윙 손절선과 같은 종가(−3.00%)를 넘지 않은 것으로 보고 들고 간다"),
    Mutation("swing_rights_revert", "batch/jobs/paper.py",
             "    되돌린 = [float(r[1]) / _되돌림(str(r[0])[:10]) for r in 줄]\n",
             "    되돌린 = [float(r[1]) for r in 줄]\n",
             ("tests/test_paper_swing_1871.py",),
             "유상증자 권리락 날의 기계적 하락을 원시 종가로 견줘 거짓 스윙손절을 낸다(25.1999)"),
)


def _돌림(사본: Path, m: Mutation, 시간: int) -> tuple[str, str]:
    """(결과, 덧말) — 결과는 caught · SURVIVED · ROT · TIMEOUT"""
    파일 = 사본 / m.path
    원문 = 파일.read_text(encoding="utf-8")
    if 원문.count(m.old) != 1:
        return "ROT", f"원문이 {원문.count(m.old)}번 나온다 — 목록을 고친다"
    try:
        파일.write_text(원문.replace(m.old, m.new, 1), encoding="utf-8")
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *m.tests],
                           cwd=사본, env=env, capture_output=True, text=True, timeout=시간)  # fmt: skip
    except subprocess.TimeoutExpired:
        return "TIMEOUT", f"{시간}초"
    finally:
        파일.write_text(원문, encoding="utf-8")
    if r.returncode == 0:
        return "SURVIVED", "시험이 안 깨짐 — 그 규칙을 이름으로 보는 시험을 더한다"
    실패 = next((x for x in r.stdout.splitlines() if x.startswith("FAILED")), "")
    return "caught", 실패.split(" - ")[0].removeprefix("FAILED ")[:100]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", action="append", help="이 이름만 (여러 번 줄 수 있다)")
    ap.add_argument("--timeout", type=int, default=300, help="돌연변이 하나의 시험 시간 한도(초)")
    a = ap.parse_args(argv)
    고른 = [m for m in MUTATIONS if not a.only or m.name in a.only]
    if not 고른:
        print("고른 돌연변이가 없습니다", file=sys.stderr)
        return 2
    사본 = Path(tempfile.mkdtemp(prefix="mutation_wt_"))
    shutil.rmtree(사본)
    나쁨 = 0
    # 사본 만들기도 try 안에서 — 만드는 중에 끊기면 사본이 `git worktree list` 에 남았다
    # (25.1747, 25.1743 교차검증 [권고] 1)
    try:
        subprocess.run(["git", "-C", str(뿌리), "worktree", "add", "-q", "--detach", str(사본), "HEAD"], check=True)
        # **기준 실행** — 돌연변이 없이 대상 시험이 먼저 통과해야 한다. 이미 깨져 있으면 모든 돌연변이가 "잡힘" 으로
        # 나와 구멍을 가린다(25.1747, 25.1743 교차검증 [권고] 2)
        시험들 = sorted({t for m in 고른 for t in m.tests})
        try:
            기준 = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *시험들], cwd=사본,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, capture_output=True, text=True,
                                timeout=a.timeout * 2)  # fmt: skip
        except subprocess.TimeoutExpired:
            print(f"BASELINE TIMEOUT — 대상 시험이 {a.timeout * 2}초 안에 끝나지 않는다")
            return 1
        if 기준.returncode != 0:
            # 수집 오류(ImportError 등)는 "ERROR" 로 나온다 — 둘 다 없으면 끝 몇 줄(25.1749)
            출력 = 기준.stdout.splitlines()
            줄 = [x for x in 출력 if x.startswith(("FAILED", "ERROR"))] or 출력[-15:]
            print("BASELINE FAIL — 돌연변이 없이도 대상 시험이 깨진다. 먼저 고친다:")
            print("\n".join(줄)[:2000])
            return 1
        for m in 고른:
            결과, 덧 = _돌림(사본, m, a.timeout)
            나쁨 += 결과 != "caught"
            print(f"{결과:9} {m.name:26} {덧}")
            if 결과 != "caught":
                print(f"          └ {m.why}")
    finally:
        subprocess.run(["git", "-C", str(뿌리), "worktree", "remove", "--force", str(사본)], check=False,
                       capture_output=True)  # fmt: skip
        subprocess.run(["git", "-C", str(뿌리), "worktree", "prune"], check=False)
    print(f"\n{len(고른)}개 중 잡힘 {len(고른) - 나쁨} · 문제 {나쁨}")
    return 1 if 나쁨 else 0


if __name__ == "__main__":
    sys.exit(main())
