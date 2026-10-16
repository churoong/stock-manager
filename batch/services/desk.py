"""운용 상황실 요약 — 순수 함수 (docs/desk.md 2장, docs/infra.md 25.1501).

웹 첫 화면(운용)이 읽는 숫자는 여기서 낸다 — 웹은 계산하지 않는다. 계좌 순위는 **공통 구간**(자리 잡은 계좌들의
첫 평가일 가운데 가장 늦은 날부터) 시간가중 수익률로 — 늦게 연 계좌(G·H)와 일찍 연 계좌를 같은 잣대로 견준다.
R 대비 = 공통 구간 수익률 − 무작위 대조 R1·R2 평균(%p): 무작위로 골라도 이만큼은 났다는 기준선.
갓 연 계좌(평가 `MIN_WINDOW_POINTS` 번 미만)는 공통 구간을 끌어당기지 않는다 — 계좌 하나를 새로 열 때마다 모든 계좌의
순위가 0 에서 다시 시작하지 않게(25.1503, 검토 8). 그 계좌는 제 개설일부터 재고 같은 구간 R 과 견준다
"""

from __future__ import annotations

from typing import Any

#: 계좌 종류 — 화면 글. 모르는 계좌는 이름만 (docs/paper.md 1장, docs/company.md)
KIND = {"A": "회사", "C": "회사", "D": "회사", "E": "회사", "B": "기준(점수 상위)", "G": "추세 익절",
        "H": "지주사(KIS가 베낌)", "R1": "무작위 대조", "R2": "무작위 대조"}  # fmt: skip
CONTROL_IDS = ("R1", "R2")
#: 이만큼 평가가 쌓여야 공통 구간에 든다 — 약 한 달(거래일). 그 전에는 "갓 연 계좌" 로 따로 잰다 (25.1503)
MIN_WINDOW_POINTS = 20


def leaderboard(series: dict[str, list[tuple[str, float]]], names: dict[str, str] | None = None) -> dict[str, Any]:
    """{window_start, control_pct, rows: [{id, name, kind, open, last, since_open_pct, window_pct, vs_r_pp, young}]}.

    자리 잡은 계좌가 공통 구간 수익률 큰 순으로 먼저, 갓 연 계좌(`young`)는 뒤에 제 개설일부터의 수익률 큰 순

    `series` = {계좌: [(as_of, twr_index)] 날짜순}. 기록이 없는 계좌는 뺀다. R 이 없으면 vs_r_pp 는 None.
    자리 잡은 계좌가 하나도 없으면(처음 한 달) 모두를 자리 잡은 것으로 본다
    """
    살 = {a: sorted(v) for a, v in series.items() if v}
    if not 살:
        return {"window_start": None, "rows": []}
    자리 = {a for a, v in 살.items() if len(v) >= MIN_WINDOW_POINTS} or set(살)
    시작 = max(살[a][0][0] for a in 자리)

    def 대조(start: str) -> float | None:
        값 = [x for r in CONTROL_IDS if (x := _구간(살.get(r) or [], start, None)) is not None]
        return sum(값) / len(값) if 값 else None

    기준선 = 대조(시작)
    줄 = []
    for a, v in 살.items():
        어림 = a not in 자리
        pct = _구간(v, v[0][0] if 어림 else 시작, None)
        선 = 대조(v[0][0]) if 어림 else 기준선
        줄.append({"id": a, "name": (names or {}).get(a) or a, "kind": KIND.get(a, ""), "open": v[0][0],
                  "last": v[-1][0], "since_open_pct": (v[-1][1] - 1) * 100, "window_pct": pct, "young": 어림,
                  "vs_r_pp": None if 선 is None or pct is None or a in CONTROL_IDS else pct - 선})  # fmt: skip
    줄.sort(key=lambda r: (r["young"], r["window_pct"] is None, -(r["window_pct"] or 0.0), r["id"]))
    return {"window_start": 시작, "control_pct": 기준선, "rows": 줄}


def _구간(v: list[tuple[str, float]], start: str, end: str | None) -> float | None:
    """`start` 이하 마지막 값 대비 `end` 이하 마지막 값(없으면 마지막) 수익률(%). 그 전 기록이 없으면 첫 값부터"""
    앞 = [t for d, t in v if d <= start]
    기준 = 앞[-1] if 앞 else (v[0][1] if v else None)
    뒤 = [t for d, t in v if end is None or d <= end]
    return (뒤[-1] / 기준 - 1) * 100 if 기준 and 뒤 else None


def new_versions(
    current: dict[str, int], known: list[tuple[str, int, str]], trade_date: str,
) -> list[tuple[str, int, str]]:
    """판 이력에 더할 (계좌, 판, since) — 처음 보는 (계좌, 판)만 (docs/desk.md 3장, 25.1505).

    그 계좌를 처음 적으면 since = '' (계좌의 처음부터), 이미 다른 판이 있으면 이번 판정일
    """
    있음 = {(a, v) for a, v, _s in known}
    계좌들 = {a for a, _v, _s in known}
    return [(a, v, "" if a not in 계좌들 else trade_date) for a, v in sorted(current.items()) if (a, v) not in 있음]


def _판구간(v: list[tuple[str, float]], since: str, to: str | None) -> float | None:
    """판 구간 수익률(%) — 결정일 `since` 의 종가부터 다음 판 결정일 `to` 의 종가까지(없으면 마지막 평가까지).

    `paper_daily.as_of` 는 배치를 돌린 **한국 날짜**이고 그 행은 그때까지 나온 종가로 잰 평가다 — 판정일 D 의 결정을 낸
    실행의 행(= D 보다 늦은 첫 평가)이 곧 "D 종가" 평가다(25.1505, 검토 1). `since` 가 '' 면 계좌의 첫 평가부터
    """
    if not v:
        return None
    시작 = v[0][1] if not since else next((t for d, t in v if d > since), None)
    끝 = v[-1][1] if to is None else next((t for d, t in v if d > to), v[-1][1])
    return (끝 / 시작 - 1) * 100 if 시작 else None


def rule_history(
    versions: list[tuple[str, int, str]], series: dict[str, list[tuple[str, float]]],
    orders: dict[tuple[str, int], int] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """판 이력 — {계좌: [{version, from, to, orders, pct, control_pct, vs_r_pp}]} 판 순 (docs/desk.md 3장).

    `versions` = 판 이력 표의 (계좌, 판, since). 한 판의 구간은 since 결정일 종가부터 다음 판 since 결정일 종가까지
    (지금 판이면 마지막 평가까지) — 그 판의 첫 결정이 처음 들어간 장부터 다음 판이 바꾸기 전까지의 시간가중 수익률이다.
    `from` 이 '' 면 계좌의 처음부터. 같은 구간 무작위 대조 R1·R2 평균과 그 차(%p)를 함께 — 판을 바꾼 뒤 나아졌는지를
    운과 가른다. `orders` = {(계좌, 판): 주문 수} — 없으면 None
    """
    판들: dict[str, list[tuple[str, int]]] = {}
    for a, ver, since in versions:
        판들.setdefault(a, []).append((since, ver))
    out: dict[str, list[dict[str, Any]]] = {}
    for a, 줄 in 판들.items():
        줄.sort()
        out[a] = []
        for i, (since, ver) in enumerate(줄):
            끝 = 줄[i + 1][0] if i + 1 < len(줄) else None
            pct = _판구간(sorted(series.get(a) or []), since, 끝)
            대조 = [x for r in CONTROL_IDS if (x := _판구간(sorted(series.get(r) or []), since, 끝)) is not None]
            기준선 = sum(대조) / len(대조) if 대조 else None
            out[a].append({"version": ver, "from": since, "to": 끝,
                           "orders": None if orders is None else orders.get((a, ver), 0), "pct": pct,
                           "control_pct": 기준선, "vs_r_pp": None if pct is None or 기준선 is None or a in CONTROL_IDS
                           else pct - 기준선})  # fmt: skip
    return out


# ── 실전 전환 관문 · 위험판 · 챔피언 교체 후보 (docs/desk.md 6장, 25.1507) ──────────────────────────────
# 읽기 전용 계산이다 — 관문을 넘었다고 아무것도 열리지 않는다. 다음 단계는 사용자가 정한다(CLAUDE.md 단계 관문).
# 문턱은 모두 **첫 값**이다 `[확인필요]` — 근거 없음, 쌓인 기록을 보고 사용자와 다시 정한다.

#: 1단계 관문 — 모의 기간(평가 횟수). 약 6개월 거래일(handoff "모의 6~12개월")
GATE_MIN_POINTS = 126
#: 운영 무사고를 보는 창(달력일)
GATE_INCIDENT_DAYS = 60
#: KIS모의투자 실현율(가상 체결이 KIS 에서도 찬 비율, docs/paper.md 15.8) 하한(%)과 그 판단에 쓸 최소 표본
GATE_KEPT_PCT = 90.0
GATE_KEPT_MIN_N = 100
#: 위험조정 비교에 쓸 최소 일간 수익 수
GATE_RATIO_MIN_N = 20
#: 위험판 — 손절선까지 이만큼(%p) 안이면 "손절 가까움"
RISK_NEAR_STOP_PP = 2.0
#: 챔피언 교체 후보 — 겹치지 않는 블록 길이(평가 횟수), 최소 블록 수, 이긴 블록 비율
CHAMPION_BLOCK = 20
CHAMPION_MIN_BLOCKS = 3
CHAMPION_WIN_SHARE = 0.67


def _수익률들(v: list[tuple[str, float]]) -> list[float]:
    """연이은 평가 사이 수익률(소수). 0 이하 값은 건너뛴다"""
    return [b / a - 1 for (_d1, a), (_d2, b) in zip(v, v[1:], strict=False) if a > 0 and b > 0]


def risk_ratio(v: list[tuple[str, float]], start: str | None = None) -> float | None:
    """변동성 대비 수익 — `start` 이후 평가 사이 수익률의 평균 ÷ 표본표준편차. 같은 기간의 계좌끼리 견주는 값이라
    무위험 수익률은 빼지 않는다(빼도 둘 다 같은 만큼 빠진다 — 변동성이 다르면 정확히 같지는 않다 `[확인필요]`).
    수익률이 `GATE_RATIO_MIN_N` 개보다 적으면 None"""
    xs = _수익률들([p for p in v if start is None or p[0] >= start])
    if len(xs) < GATE_RATIO_MIN_N:
        return None
    m = sum(xs) / len(xs)
    sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
    return m / sd if sd > 0 else None


def gate(
    series: dict[str, list[tuple[str, float]]], board: dict[str, Any], mirror: str, *, recon_bad_days: int,
    batch_failed: int, kis: dict[str, Any] | None,
) -> dict[str, Any]:
    """실전 전환 1단계 관문 진도 — {stage, items: [{key, label, now, target, ok}], passed, next}.

    1단계 = 모의 성적·운영 무사고(CLAUDE.md). 잣대는 KIS 가 베끼는 계좌(`mirror`, 지금 H) — 실전에 옮길 전략이다.
    `board` = `leaderboard()` 결과, `kis` = {kept_pct, n}(국내·미국 합)
    """
    v = sorted(series.get(mirror) or [])
    줄 = {r["id"]: r for r in board.get("rows") or []}
    시작 = board.get("window_start")
    대조 = [x for r in CONTROL_IDS if (x := risk_ratio(sorted(series.get(r) or []), 시작)) is not None]
    대조비 = sum(대조) / len(대조) if 대조 else None
    내비 = risk_ratio(v, 시작)
    vs_r = (줄.get(mirror) or {}).get("vs_r_pp")
    kept, kn = (kis or {}).get("kept_pct"), int((kis or {}).get("n") or 0)
    items = [
        {"key": "days", "label": "모의 기간(평가 횟수)", "now": len(v), "target": GATE_MIN_POINTS,
         "ok": len(v) >= GATE_MIN_POINTS},
        {"key": "vs_r", "label": "공통 구간 R 대비(%p)", "now": vs_r, "target": 0, "ok": vs_r is not None and vs_r > 0},
        {"key": "ratio", "label": "변동성 대비 수익 — 무작위 대조 평균보다", "now": 내비, "target": 대조비,
         "ok": 내비 is not None and 대조비 is not None and 내비 > 대조비},
        {"key": "incidents", "label": f"운영 무사고(최근 {GATE_INCIDENT_DAYS}일 장부 검산 어긋남·일일 배치 실패)",
         "now": recon_bad_days + batch_failed, "target": 0, "ok": recon_bad_days + batch_failed == 0,
         "detail": {"recon_bad_days": recon_bad_days, "batch_failed": batch_failed}},
        {"key": "kis", "label": f"KIS 실현율(%, 표본 {GATE_KEPT_MIN_N} 이상)", "now": kept, "target": GATE_KEPT_PCT,
         "n": kn,
         "ok": kept is not None and kn >= GATE_KEPT_MIN_N and float(kept) >= GATE_KEPT_PCT},
    ]  # fmt: skip
    다음 = "2단계 손 매매 준수율·슬리피지 → 3단계 소액 승인형 → 4단계 한도 내 자동 — 단계마다 사용자 결정"
    return {"stage": 1, "account": mirror, "items": items, "passed": all(i["ok"] for i in items), "next": 다음}


def combine_kept(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """국내·미국 KIS 비교 요약을 합친 실현율 — 둘 다 참 ÷ (둘 다 참 + 가상만 참), 표본 = 그 분모"""
    둘 = sum(int((x or {}).get("both") or 0) for x in summaries)
    가상만 = sum(int((x or {}).get("internal_only") or 0) for x in summaries)
    return {"kept_pct": 둘 / (둘 + 가상만) * 100 if 둘 + 가상만 else None, "n": 둘 + 가상만}


def risk_board(
    latest: dict[str, dict[str, Any]], series: dict[str, list[tuple[str, float]]], stops: dict[str, float],
) -> list[dict[str, Any]]:
    """위험판 — 계좌마다 노출·집중·손절 가까움·낙폭.

    `latest` = {계좌: {equity, holdings, positions: [{name, ticker, horizon, unrealized_pct, weight_pct}]}} (그날 평가),
    `stops` = {기간: 손절 %(음수)}. 낙폭 = 시간가중 지수의 지금 ÷ 지난 고점 − 1(%)
    """
    out = []
    for a in sorted(latest):
        x = latest[a]
        자산, 보유 = float(x.get("equity") or 0), float(x.get("holdings") or 0)
        종목 = [q for q in x.get("positions") or [] if isinstance(q, dict)]
        큰 = max(종목, key=lambda q: float(q.get("weight_pct") or 0), default=None)
        가까움 = []
        for q in 종목:
            선 = stops.get(str(q.get("horizon") or ""))
            u = q.get("unrealized_pct")
            if 선 is not None and u is not None and float(u) <= 선 + RISK_NEAR_STOP_PP:
                가까움.append({"name": q.get("name") or q.get("ticker"), "unrealized_pct": float(u), "stop_pct": 선})
        v = sorted(series.get(a) or [])
        고점 = max((t for _d, t in v), default=None)
        out.append({"id": a, "exposure_pct": 보유 / 자산 * 100 if 자산 > 0 else None, "positions": len(종목),
                    "top_weight_pct": float(큰.get("weight_pct") or 0) if 큰 else None,
                    "top_name": (큰.get("name") or 큰.get("ticker")) if 큰 else None, "near_stop": 가까움,
                    "dd_pct": (v[-1][1] / 고점 - 1) * 100 if v and 고점 else None})  # fmt: skip
    return out


def champion(series: dict[str, list[tuple[str, float]]], mirror: str) -> list[dict[str, Any]]:
    """챔피언 교체 후보 — 지금 챔피언(`mirror`, KIS 가 베끼는 계좌)을 **꾸준히** 앞서는 전략.

    계좌마다 챔피언과 둘 다 평가가 있는 날만 맞춰, 겹치지 않는 `CHAMPION_BLOCK` 평가 블록으로 나눠
    블록마다 수익을 견준다.
    후보 = 블록 `CHAMPION_MIN_BLOCKS` 개 이상 · 이긴 블록 비율 `CHAMPION_WIN_SHARE` 이상 · 맞춘 기간 전체 수익도 앞섬.
    한 번 크게 이긴 것이 아니라 여러 구간에서 이겼는지를 본다. 무작위 대조는 뺀다. 교체는 사용자 결정
    """
    h = dict(sorted(series.get(mirror) or []))
    out = []
    for a, v in sorted(series.items()):
        if a == mirror or a in CONTROL_IDS:
            continue
        날 = [d for d, _t in sorted(v) if d in h]
        내 = dict(v)
        if len(날) < 2:
            continue
        블록 = [날[i: i + CHAMPION_BLOCK + 1] for i in range(0, len(날) - CHAMPION_BLOCK, CHAMPION_BLOCK)]
        이김 = sum(내[b[-1]] / 내[b[0]] > h[b[-1]] / h[b[0]] for b in 블록 if 내[b[0]] and h[b[0]])
        전체 = (내[날[-1]] / 내[날[0]] - 1) * 100 if 내[날[0]] else None
        챔 = (h[날[-1]] / h[날[0]] - 1) * 100 if h[날[0]] else None
        후보 = (len(블록) >= CHAMPION_MIN_BLOCKS and 이김 / len(블록) >= CHAMPION_WIN_SHARE
              and 전체 is not None and 챔 is not None and 전체 > 챔)  # fmt: skip
        out.append({"id": a, "from": 날[0], "blocks": len(블록), "wins": 이김, "pct": 전체, "champion_pct": 챔,
                    "candidate": 후보})  # fmt: skip
    return sorted(out, key=lambda r: (not r["candidate"], -(r["wins"] / r["blocks"] if r["blocks"] else 0),
                                      -((r["pct"] or 0) - (r["champion_pct"] or 0))))  # fmt: skip


#: KIS 단계가 주문을 안 보낸 까닭 가운데 정상인 것 — 확인할 일에 넣지 않는다
SEND_OK = ("오늘은 장이 없다",)


def checks(
    *, halted: bool, paper: dict[str, Any] | None, broker: dict[str, Any] | None, recon_bad: list[str],
    warnings: int, h_orders: int = 0,
) -> list[dict[str, str]]:
    """확인할 일 — [{level: high·normal, text}]. 높음이 먼저.

    `warnings` 는 그 실행의 경고 **줄 수**만 받는다 — 줄 자체는 가상·KIS 실패와 겹치고(같은 일이 두 번) 실제 포트폴리오
    경고가 섞여 운용 화면의 잡음이 된다(25.1503, 검토 6). `h_orders` = KIS 가 베낄 계좌의 이 실행 주문 수
    """
    out: list[dict[str, str]] = []
    if halted:
        out.append({"level": "high", "text": "멈춤 스위치가 켜져 있어 가상·KIS모의투자 모두 새 주문을 내지 않습니다"})
    p = paper or {}
    if p.get("error"):
        out.append({"level": "high",
                    "text": f"가상모의투자 단계 실패: {str(p['error'])[:120]} — 그날 KIS 베끼기도 하지 않았습니다"})
    b = broker or {}
    if b.get("missing"):
        out.append({"level": "high",
                    "text": f"KIS모의투자 시크릿 {', '.join(b['missing'])} 이(가) 비어 주문을 내지 않았습니다"})
    if b.get("error"):
        out.append({"level": "high", "text": f"KIS모의투자 단계 실패: {str(b['error'])[:120]}"})
    # 안 보낸 날 (25.1503, 검토 1) — 접수 시작 전·장 끝난 뒤·예약 마감 뒤에 돈 실행은 그날 KIS 에 아무것도 내지 않는다
    if b.get("send") and b["send"] not in SEND_OK:
        out.append({"level": "high" if h_orders else "normal",
                    "text": f"KIS모의투자에 보내지 않음: {b['send']}" + (f" — 베낄 주문 {h_orders}건이 남았습니다"
                                                                    if h_orders else "")})  # fmt: skip
    if b.get("diverge"):
        out.append({"level": "high", "text": str(b["diverge"])[:300]})
    if b.get("diverge_error"):
        out.append({"level": "normal", "text": f"KIS 괴리 판정 실패({b['diverge_error']}) — 주문은 그대로 나갔습니다"})
    if b.get("not_sent"):
        까닭 = ", ".join(f"{k} {v}건" for k, v in sorted(b["not_sent"].items()))
        out.append({"level": "normal", "text": f"KIS모의투자에 안 낸 주문: {까닭}"})
    보냄 = b.get("sent") or {}
    if 보냄.get("rejected"):
        out.append({"level": "normal", "text": f"KIS모의투자 거부 {보냄['rejected']}건 — 거래내역의 까닭을 봅니다"})
    if 보냄.get("no_response"):
        out.append({"level": "normal",
                    "text": f"KIS모의투자 응답 모름 {보냄['no_response']}건 — 다음 배치가 체결 조회로 맞춥니다"})
    맞춤 = b.get("reconciled") or {}
    if 맞춤.get("unknown"):
        out.append({"level": "normal", "text": f"KIS모의투자 체결 조회에 없는 주문 {맞춤['unknown']}건(모름으로 닫음)"})
    if b.get("perf_error"):
        out.append({"level": "normal",
                    "text": f"KIS 계좌 성과 계산 실패({b['perf_error']}) — 주문은 그대로 나갔습니다"})
    if b.get("diff"):
        out.append({"level": "normal", "text": f"KIS 잔고와 가상 장부가 어긋난 종목 {b['diff']}개"})
    if recon_bad:
        out.append({"level": "high", "text": f"장부 검산 어긋남: 계좌 {', '.join(sorted(recon_bad))}"})
    # 계좌 하나만 그날 못 산 것 — 위원회·지주사 실패, 헌장 오류 (25.1503, 검토 6)
    for aid, a in sorted((p.get("accounts") or {}).items()):
        if not isinstance(a, dict):
            continue
        for 칸, 이름 in (("committee", "투자위원회를 열지 못함"), ("holding", "지주사가 고르지 못함")):
            if isinstance(a.get(칸), dict) and a[칸].get("error"):
                글 = f"가상 {aid} {이름} — 오늘 사지 않았습니다: {str(a[칸]['error'])[:100]}"
                out.append({"level": "high", "text": 글})
        if a.get("charter_errors"):
            out.append({"level": "high", "text": f"가상 {aid} 헌장 오류: {'; '.join(a['charter_errors'])[:120]}"})
    if p.get("frozen"):
        out.append({"level": "normal",
                    "text": f"기업행위 경계로 멈춘 종목 {len(p['frozen'])}개 — 그 종목은 사고팔지 않습니다"})
    if warnings:
        out.append({"level": "normal", "text": f"이 실행의 경고 {warnings}줄 — 시스템 상태 화면에서"})
    return sorted(out, key=lambda x: x["level"] != "high")


def merge(prev: dict[str, Any] | None, market: str, section: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """그날 요약에 이 시장 칸을 합친다 — 다른 시장 칸은 남기고, 순위·KIS·확인할 일(`common`)은 이번 것으로 덮는다.

    확인할 일은 시장마다 따로 두고(`markets.X.checks`) 화면이 합쳐 보인다 — 국내 실패가 미국 실행에 덮여 사라지지 않게
    """
    out = dict(prev or {})
    시장들 = dict(out.get("markets") or {})
    시장들[market] = section
    return out | common | {"markets": 시장들}
