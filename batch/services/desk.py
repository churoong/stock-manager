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


# ── 실전 전환 관문 · 위험판 · 챔피언 교체 후보 (docs/desk.md 6장, 25.1508·25.1511) ──────────────────────────────
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
#: KIS 마지막 줄이 이보다 오래되면(달력일) 실현율에 넣지 않는다 — 미국 베끼기가 꺼진 뒤의 옛 표본이 섞이지 않게(25.1511)
KIS_RECENT_DAYS = 7
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
    series: dict[str, list[tuple[str, float]]], mirror: str, *, since: str = "", version: int | None = None,
    recon_bad_days: int, batch_failed: int, kis: dict[str, Any] | None,
) -> dict[str, Any]:
    """실전 전환 1단계 관문 진도 — {stage, account, version, from, items: [{key, label, now, target, ok}], passed,
    next}.

    1단계 = 모의 성적·운영 무사고(CLAUDE.md). 잣대는 KIS 가 베끼는 계좌(`mirror`, 지금 H)의 **지금 판** —
    실전에 옮길 전략이다. `since` = 지금 판을 처음 본 결정일(판 이력, '' = 처음부터) — 그 뒤 평가만 센다
    (판을 섞지 않는다, 25.1511 검토 3). 비교 구간은 **관문 자기 구간** = 지금 판의 첫 평가와 R1·R2 첫 평가 가운데
    가장 늦은 날부터 — 순위표의 공통 구간을 빌리면 새 계좌 하나가 20번째 평가에 닿는 날 관문 판정이 뒤집혔다
    (25.1511 검토 1). `kis` = {kept_pct, n}(국내·미국 합)
    """
    전체 = sorted(series.get(mirror) or [])
    v = [p for p in 전체 if not since or p[0] > since]
    첫들 = [x[0][0] for x in [v] + [sorted(series.get(r) or []) for r in CONTROL_IDS] if x]
    시작 = max(첫들) if v and 첫들 else None
    pct = _구간(전체, 시작, None) if 시작 else None
    대조수익 = [x for r in CONTROL_IDS if 시작 and (x := _구간(sorted(series.get(r) or []), 시작, None)) is not None]
    vs_r = pct - sum(대조수익) / len(대조수익) if pct is not None and 대조수익 else None
    대조 = [x for r in CONTROL_IDS if 시작 and (x := risk_ratio(sorted(series.get(r) or []), 시작)) is not None]
    대조비 = sum(대조) / len(대조) if 대조 else None
    내비 = risk_ratio(전체, 시작) if 시작 else None
    kept, kn = (kis or {}).get("kept_pct"), int((kis or {}).get("n") or 0)
    판글 = f"지금 판{'' if version is None else f' {version}'}"
    items = [
        {"key": "days", "label": f"모의 기간({판글}의 평가 횟수)", "now": len(v), "target": GATE_MIN_POINTS,
         "ok": len(v) >= GATE_MIN_POINTS},
        {"key": "vs_r", "label": "관문 구간 R 대비(%p)", "now": vs_r, "target": 0, "ok": vs_r is not None and vs_r > 0},
        {"key": "ratio", "label": "변동성 대비 수익 — 무작위 대조 평균보다", "now": 내비, "target": 대조비,
         "ok": 내비 is not None and 대조비 is not None and 내비 > 대조비},
        {"key": "incidents", "label": f"운영 무사고(최근 {GATE_INCIDENT_DAYS}일 장부 검산 어긋남·일일 배치 실패)",
         "now": recon_bad_days + batch_failed, "target": 0, "ok": recon_bad_days + batch_failed == 0,
         "detail": {"recon_bad_days": recon_bad_days, "batch_failed": batch_failed}},
        {"key": "kis", "label": f"KIS 실현율(%, 최근 28일, 표본 {GATE_KEPT_MIN_N} 이상)", "now": kept,
         "target": GATE_KEPT_PCT, "n": kn,
         "ok": kept is not None and kn >= GATE_KEPT_MIN_N and float(kept) >= GATE_KEPT_PCT},
    ]  # fmt: skip
    다음 = "2단계 손 매매 준수율·슬리피지 → 3단계 소액 승인형 → 4단계 한도 내 자동 — 단계마다 사용자 결정"
    return {"stage": 1, "account": mirror, "version": version, "from": 시작, "items": items,
            "passed": all(i["ok"] for i in items), "next": 다음}  # fmt: skip


#: 실전 준비 목록 (docs/paper.md 14장, 25.1513) — 배치 늦음을 세는 창(달력일)과 "늦음" 문턱(초)
READY_WINDOW_DAYS = 30
READY_LATE_SEC = 600

#: 14장 일곱 항목의 지금 상태 — done 갖춤 · partial 일부 · rehearsal 모의로 연습 중 · todo 아직 · user 사용자 결정·확인.
#: **사람이 고친다** — 항목을 갖추는 일을 하면 이 표와 docs/paper.md 14장을 같은 커밋에서 고친다(시험이 대조)
READINESS = (
    ("always_on", "상시 실행 환경", "user",
     "장중 주문·체결 확인·장중 손절이 정해진 시각에 돌아야 한다 — 지금은 예약 실행(지연·누락)."
     " 비용 규칙(0원) 변경은 사용자 결정"),
    ("broker", "증권사 주문 연동", "rehearsal",
     "KIS모의투자로 주문·접수·거부·체결 조회·잔고 대조를 날마다 연습 중(실전 도메인 주문 경로는 없다)"),
    ("risk_gate", "사전 위험 점검", "done",
     "하루 손실 한도·하루 주문 수·비중 상한·묵은 종가·멈춤 스위치가 모의 주문 앞에서 돈다(25.1451)"),
    ("failure", "장애 대처", "partial",
     "묵은 종가면 주문 안 함·실패 알림·멈춤 스위치는 있음. 없는 것: 체결 확인이 안 되면 다음 주문을 멈추는 장치"),
    ("cost", "세금·수수료 실값", "todo",
     "지금은 추정 비용(수수료·거래세·슬리피지 가정). 실제 체결 비용은 2단계(손 매매)부터 잰다"),
    ("terms", "이용 조건 확인", "user",
     "증권사 API 의 자동 주문 허용 범위·호출 한도 — 약관 확인은 사용자 `[확인필요]`"),
    ("audit", "감사 기록", "done", "결정 원장·근거표·판 번호·위험 점검 결과를 남긴다(25.1449)"),
)


def readiness(*, runs: dict[str, Any] | None, kis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """실전 준비 목록 진도 — [{key, label, status, note, evidence}] (docs/paper.md 14장, 25.1513).

    상태는 `READINESS` 표(사람이 고침), 근거 수치는 기록에서: 상시 실행 = 최근 `READY_WINDOW_DAYS` 일 일일 배치 중
    예정보다 `READY_LATE_SEC` 초 넘게 늦은 횟수·가장 늦은 분, 증권사 연동 = KIS 마지막 줄들의 끝난 주문·모름·거부
    """
    out = []
    for key, label, status, note in READINESS:
        근거 = None
        if key == "always_on" and runs and runs.get("n"):
            늦음 = f"{READY_LATE_SEC // 60}분 넘게 늦음 {runs.get('late') or 0}회"
            근거 = (f"최근 {READY_WINDOW_DAYS}일 일일 배치 {runs['n']}회 중 {늦음}"
                  f" · 가장 늦음 {round((runs.get('max_delay') or 0) / 60)}분")  # fmt: skip
        if key == "broker" and kis:
            n = sum(int(x.get("n") or 0) for x in kis)
            # 창 길이는 요약이 적은 값(`COMPARE_DAYS`) — 글에 박지 않는다(25.1516, 검토 6)
            from batch.services.broker_mirror import COMPARE_DAYS

            창 = max((int(x.get("days") or 0) for x in kis), default=0) or COMPARE_DAYS
            근거 = (f"최근 {창}일 끝난 베낀 주문 {n} · 모름 {sum(int(x.get('unknown') or 0) for x in kis)}"
                  f" · 거부 {sum(int(x.get('rejected') or 0) for x in kis)}")  # fmt: skip
        out.append({"key": key, "label": label, "status": status, "note": note, "evidence": 근거})
    return out


def combine_kept(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """국내·미국 KIS 비교 요약을 합친 실현율 — 둘 다 참 ÷ (둘 다 참 + 가상만 참), 표본 = 그 분모"""
    둘 = sum(int((x or {}).get("both") or 0) for x in summaries)
    가상만 = sum(int((x or {}).get("internal_only") or 0) for x in summaries)
    return {"kept_pct": 둘 / (둘 + 가상만) * 100 if 둘 + 가상만 else None, "n": 둘 + 가상만}


#: 보정에 쓰지 않는 칸 — 지정가 매수는 지정가보다 비싸게 찰 수 없어 가격 차이가 구조상 0 이하(가격 개선)다.
#: 그 몫은 시가로 따로 잰다(`pi_pp`) — KIS 칸으로 한 번 더 빼면 두 번 센다 (docs/desk.md 8장, 25.1514)
ADJ_SKIP_CELLS = ("buy_limit",)
#: 그날 종가가 지정가의 이만큼 아래면 시가 개선에 넣지 않고 `pi_missing` 으로 — 미국 분할을 감지하면 시세를 처음부터
#: 다시 받아 지난 시가·종가도 분할 반영 값으로 덮이는데 주문의 지정가는 그대로라 개선이 부풀었다(25.1516 검토 2).
#: 처음엔 시가 50% 문턱이었는데 2:1·3:2 분할을 못 잡았다(25.1517 검토 1) — 찬 지정가 매수는 원래 "종가 ≤ 지정가" 이고
#: 대개 지정가 바로 아래에서 끝나므로, 종가가 지정가의 80% 아래면 k ≥ 1.25 분할로 본다.
#: 하루 20% 넘게 빠지며 찬 날은 놓친다
PI_CLOSE_FLOOR = 0.8


def _칸키(f: dict[str, Any]) -> str:
    """체결 칸. 지정가 매수는 판정 잣대와 상관없이 `buy_limit` — 늦은 실행이면 지정가 매수에도 `fill_basis='close'` 가
    붙지만 판정(`judge`)은 지정가 매수에서 그 값을 보지 않는다(25.1516, 검토 3). 그 밖의 종가 판정은 `close`"""
    if f["side"] == "buy" and f.get("order_type") != "market":
        return "buy_limit"
    if f.get("fill_basis") == "close":
        return "close"
    return f"{f['side']}_{'market' if f.get('order_type') == 'market' else 'limit'}"


def measured(c: dict[str, Any] | None, min_days: int) -> bool:
    """칸을 "잰 것" 으로 쓰나 — 거래일 `min_days` 이상이고 excess·se 가 있다.
    배치가 칸마다 적어 웹이 그대로 쓴다(25.1516, 검토 4)"""
    c = c or {}
    return int(c.get("days") or 0) >= min_days and c.get("excess") is not None and c.get("se") is not None


def _회전(
    fills: list[dict[str, Any]], equity: list[tuple[str, float]], start: str, end: str | None = None,
) -> list[tuple[dict[str, Any], float]]:
    """구간(`start ≤ fill_date`, `end` 가 있으면 `< end`) 체결마다 (체결, 원화 금액 ÷ 그 전 평가 총액).
    평가 총액 = 체결일 이하 마지막 평가(그날 체결은 다음 실행이 적으므로 체결 전 값), 없으면 첫 평가"""
    v = sorted(equity)
    out = []
    for f in fills:
        d = str(f.get("fill_date") or "")[:10]
        if d < start or (end is not None and d >= end):
            continue
        앞 = [e for a, e in v if a <= d]
        e = 앞[-1] if 앞 else (v[0][1] if v else None)
        if not e or e <= 0:
            continue
        out.append((f, float(f["price"]) * int(f["quantity"]) * float(f.get("fx_rate") or 1.0) / float(e)))
    return out


def fill_adjust(
    *, fills: dict[str, list[dict[str, Any]]], equity: dict[str, list[tuple[str, float]]], starts: dict[str, str],
    cells: dict[str, dict[str, Any]], min_days: int, ends: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """KIS모의 체결 기준 보정(근사·참고, docs/desk.md 8장) — 계좌마다 순위 구간의 회전율 × KIS 가 잰 칸의
    "장부 가정 넘는 비용".

    `fills` = {계좌: [{market, side, order_type, fill_basis, fill_date, price, quantity, fx_rate, limit_price, open}]},
    `equity` = {계좌: [(as_of, 원화 평가 총액)]}, `starts` = {계좌: 구간 시작}, `ends` = {계좌: 끝(그 날 체결은 뺌)},
    `cells` = {시장: KIS 체결 칸}.

    - `turnover_pct` = Σ 체결 원화 금액 ÷ 그 전 평가 총액(%)
    - `adj_pp` = Σ 회전 × excess(칸) — 잰 칸(`measured`, 지정가 매수 뺌)만. 양수면 KIS 기준으로 그만큼 덜 벌었다
    - `band_pp` = Σ 회전 × 2·se — 칸 값의 불확실성(거래일 묶음 표준오차 둘). `measured_pct` = 잰 칸의 회전(%)
    - `unmeasured_pct` = 잰 칸이 없는 회전(%) — 클수록 보정이 말해 주는 게 적다(지정가 매수는 따로라 뺌)
    - `pi_pp` = Σ 지정가 매수 회전 × (지정가 − min(시가, 지정가)) ÷ 지정가 — 가상 장부는 지정가로 사지만 시가가
      더 낮게 열린 날은 실제로는 시가 언저리에 찬다(가격 개선, 가상이 비관). 장 열린 뒤 낸 주문(`fill_basis='close'`)은
      시가 뒤라 개선 0. `pi_missing` = 시가·종가가 없거나 종가가 지정가의 `PI_CLOSE_FLOOR` 아래(분할 의심)라 못 잰 건수
    - `limit_buy_pct` = 지정가 매수 금액 ÷ 매수 금액(%) — 칸의 miss·extra(체결 여부가 갈리는 몫)를 볼 무게
    보정 수익률(순위 수익률 − adj + pi)은 `adjust_rows` 가 붙인다. **순위는 바꾸지 않는다**(검증 에이전트 반박, 25.1514)
    """
    out: dict[str, dict[str, Any]] = {}
    for a, start in starts.items():
        회 = _회전(fills.get(a) or [], equity.get(a) or [], start, (ends or {}).get(a))
        adj = band = 잼 = 안잼 = pi = 지정 = 매수 = 0.0
        pi_없음 = 0
        for f, t in 회:
            키 = _칸키(f)
            if f["side"] == "buy":
                매수 += t
            if 키 in ADJ_SKIP_CELLS:
                지정 += t
                lim, op, cl = f.get("limit_price"), f.get("open"), f.get("close")
                if f.get("fill_basis") == "close":
                    continue
                if lim and op and cl and float(lim) > 0 and float(cl) >= float(lim) * PI_CLOSE_FLOOR:
                    pi += t * (float(lim) - min(float(op), float(lim))) / float(lim) * 100
                else:
                    pi_없음 += 1
                continue
            c = (cells.get(str(f.get("market") or "")) or {}).get(키) or {}
            if measured(c, min_days):
                adj += t * float(c["excess"])
                band += t * 2 * float(c["se"])
                잼 += t
            else:
                안잼 += t
        out[a] = {"from": start, "fills": len(회), "turnover_pct": sum(t for _f, t in 회) * 100, "adj_pp": adj,
                  "band_pp": band, "measured_pct": 잼 * 100, "unmeasured_pct": 안잼 * 100, "pi_pp": pi,
                  "pi_missing": pi_없음, "limit_buy_pct": 지정 / 매수 * 100 if 매수 > 0 else None}  # fmt: skip
    return out


def adjust_rows(leaderboard: dict[str, Any], adj: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """순위표 줄마다 보정 수익률과 "보정 폭 안에서 이웃과 뒤집힐 수 있음" 표시를 붙인다(순서는 그대로).

    자리 잡은 계좌만 이웃(바로 아래 줄)과 견준다: 내 보정 하한 ≤ 이웃 보정 상한이면 둘 다 `close_call`.
    보정 폭 = band_pp. 안 잰 회전은 폭에 넣지 않는다 — 대신 화면이 `unmeasured_pct` 를 같이 보인다
    """
    out = {a: dict(v) for a, v in adj.items()}
    줄 = [r for r in leaderboard.get("rows") or [] if not r.get("young") and r.get("window_pct") is not None]
    for r in leaderboard.get("rows") or []:
        x = out.get(r["id"])
        if x is not None:
            pct = r.get("window_pct")
            x["adjusted_pct"] = None if pct is None else float(pct) - x["adj_pp"] + x["pi_pp"]
            x.setdefault("close_call", False)
    for 위, 아래 in zip(줄, 줄[1:], strict=False):
        p, q = out.get(위["id"]), out.get(아래["id"])
        if not p or not q or p.get("adjusted_pct") is None or q.get("adjusted_pct") is None:
            continue
        if p["adjusted_pct"] - p["band_pp"] <= q["adjusted_pct"] + q["band_pp"]:
            p["close_call"] = q["close_call"] = True
    return out


def h_window(
    kis_totals: list[tuple[str, float | None]], today: str, us_ok: set[str] | None = None,
) -> tuple[list[str], str] | None:
    """대보기 (시작 후보들, 끝) — 끝 = **오늘보다 앞선**, 그날 미국 일일 배치가 끝난 KIS 총액 마지막 날,
    시작 후보 = 끝보다 앞선 KIS 총액 날(오름차순 — 부르는 쪽이 연결 전 보유 없는 첫날을 고른다, 25.1517 검토 2).

    KIS 총액은 국내 아침 배치가 적는다(국내·미국 모두 전 장 종가). 가상 평가 행은 그날 미국 저녁 배치가 덮어써야
    국내·미국이 같은 장 종가가 된다 — 오늘 행은 아침에는 아직 미국이 하루 늦다(25.1516, 검토 1). 미국 휴장·미국 배치
    실패로 덮이지 않은 날도 끝이 될 수 없다(`us_ok` = 미국 일일 배치가 끝난 한국 날짜, 25.1517 검토 4)
    """
    날 = sorted(d for d, v in kis_totals if v is not None and float(v) > 0)
    끝 = [d for d in 날 if d < today and (us_ok is None or d in us_ok)]
    if not 끝:
        return None
    시작 = [d for d in 날 if d < 끝[-1]]
    return (시작, 끝[-1]) if 시작 else None


def h_check(
    *, start: str, end: str, kis0: float, kis1: float, h_pnl0: float, h_pnl1: float, h_equity0: float,
    h_adj: dict[str, Any], pre_link: int,
) -> dict[str, Any] | None:
    """보정이 맞는지 H 로 대본다 — KIS 실측 손익 차이 vs 칸으로 설명한 몫 (docs/desk.md 8장, 25.1514·25.1516).

    실측 = ((KIS 끝 − 시작 총액) − (H 끝 − 시작 평가손익)) ÷ 시작 H 평가 총액(%p).
    KIS 는 H 주문을 같은 수량으로 베끼므로 손익 금액끼리 견준다(15.7).
    설명 = −adj + pi — `h_adj` 는 같은 기간(시작 ≤ 체결일 < 끝)으로 다시 잰 H 보정.
    `pre_link` = 시작일에 H 가 들고 KIS 에 없던 종목 수 — 그 종목의 시세 변동은 H 에만 있어 실측을 흔든다
    (판정에서 뺀다).
    그 밖에 체결 여부가 갈린 주문(miss·extra)·환율·정리 주문·모름이 섞인다 — 두 달 넘게 설명이 실측을 못 따라가면
    보정 열을 내린다(handoff)
    """
    if h_equity0 <= 0:
        return None
    실측 = ((kis1 - kis0) - (h_pnl1 - h_pnl0)) / h_equity0 * 100
    return {"from": start, "to": end, "actual_pp": 실측, "explained_pp": -h_adj["adj_pp"] + h_adj["pi_pp"],
            "band_pp": h_adj["band_pp"], "unmeasured_pct": h_adj["unmeasured_pct"], "pre_link": pre_link}  # fmt: skip


def risk_board(
    latest: dict[str, dict[str, Any]], series: dict[str, list[tuple[str, float]]], stops: dict[str, float],
) -> list[dict[str, Any]]:
    """위험판 — 계좌마다 노출·집중·손절 가까움·낙폭.

    `latest` = {계좌: {equity, holdings, positions: [{name, horizon, avg_price, close, weight_pct}]}} (그날 평가),
    `stops` = {기간: 손절 %(음수)}. 낙폭 = 시간가중 지수의 지금 ÷ 지난 고점 − 1(%).
    손절 가까움의 수익률은 **매도 플래그와 같은 잣대** — 종목 통화로 종가 ÷ 평균단가(수수료 포함 원가) − 1.
    원화 평가손익률은 환율이 섞여 미국 종목이 달러로는 손절선 1%p 앞인데 "없음" 으로 보였다(25.1511 검토 2).
    기간이 없거나 모르는 값이면 장기로 본다(매도 플래그와 같이). 유상증자 권리락 계수는 반영하지 않는다
    (드묾 `[확인필요]`)
    """
    out = []
    for a in sorted(latest):
        x = latest[a]
        자산, 보유 = float(x.get("equity") or 0), float(x.get("holdings") or 0)
        종목 = [q for q in x.get("positions") or [] if isinstance(q, dict)]
        큰 = max(종목, key=lambda q: float(q.get("weight_pct") or 0), default=None)
        가까움 = []
        for q in 종목:
            기간 = str(q.get("horizon") or "")
            선 = stops.get(기간 if 기간 in stops else "long")
            avg, close = q.get("avg_price"), q.get("close")
            if 선 is None or not avg or close is None or float(avg) <= 0:
                continue
            u = (float(close) / float(avg) - 1) * 100
            if u <= 선 + RISK_NEAR_STOP_PP:
                가까움.append({"name": q.get("name") or q.get("ticker"), "unrealized_pct": u, "stop_pct": 선})
        v = sorted(series.get(a) or [])
        고점 = max((t for _d, t in v), default=None)
        out.append({"id": a, "exposure_pct": 보유 / 자산 * 100 if 자산 > 0 else None, "positions": len(종목),
                    "top_weight_pct": float(큰.get("weight_pct") or 0) if 큰 else None,
                    "top_name": (큰.get("name") or 큰.get("ticker")) if 큰 else None, "near_stop": 가까움,
                    "dd_pct": (v[-1][1] / 고점 - 1) * 100 if v and 고점 else None})  # fmt: skip
    return out


def champion(series: dict[str, list[tuple[str, float]]], mirror: str) -> list[dict[str, Any]]:
    """챔피언 교체 후보 — 지금 챔피언(`mirror`, KIS 가 베끼는 계좌)을 **꾸준히** 앞서는 전략.

    계좌마다 챔피언과 둘 다 평가가 있는 날만 맞춰, 가장 최근 끝에서부터 겹치지 않는 `CHAMPION_BLOCK` 평가 블록으로
    나눠 블록마다 수익을 견준다.
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
        # 가장 최근 끝에서 거꾸로 자른다 — 앞에서 자르면 마지막 미완성 블록(최근 19 평가까지)이 승패에서 빠져
        # 최근에 크게 져도 후보로 남았다(25.1511 검토 6). 블록은 끝점을 나눠 갖는다(수익 구간은 겹치지 않는다)
        블록 = [날[i - CHAMPION_BLOCK: i + 1] for i in range(len(날) - 1, CHAMPION_BLOCK - 1, -CHAMPION_BLOCK)]
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


# ── 아침 운용 보고 (텔레그램 리포트 머리, docs/desk.md 7장, 25.1510) ─────────────────────────────────────

#: 운용 보고에 싣는 줄 수 상한 — 리포트 머리가 종목 추천을 밀어내지 않게
REPORT_MAX_CHECKS = 3
REPORT_MAX_ORDERS = 5
REPORT_TOP_ACCOUNTS = 3

_SIDE = {"buy": "매수", "sell": "매도"}


def _돈(v: Any, currency: str) -> str:
    if v is None:
        return "-"
    x = float(v)
    return f"{round(x):,}원" if currency != "USD" else f"{'-' if x < 0 else ''}${abs(x):,.2f}"


def report_lines(
    section: dict[str, Any], board: dict[str, Any], mirror: str, *, prev_kis: list[dict[str, Any]] | None = None,
    kis_state: str | None = None,
) -> list[str]:
    """아침(미국은 밤) 리포트 머리의 **운용 보고** 몇 줄 — 종목 추천은 그 아래 재료로(CLAUDE.md "시스템의 중심").

    `section` = 운용 요약의 이 시장 칸(체결·주문·확인할 일 — KIS 칸은 아직 비었다, KIS 는 리포트 뒤에 낸다),
    `board` = 계좌 순위, `prev_kis` = 지난 실행이 KIS 에 **보낸 결과**(접수·거부 — 체결은 다음 실행의 맞추기 뒤라
    아직 없다), None 이면 모름(그 부분을 뺀다). `kis_state` = 이번에 KIS 에 내는지의 글 —
    None 이면 KIS 줄을 뺀다(25.1512)
    """
    out = ["📊 운용 보고 — 가상모의투자·KIS모의투자 (자세히: 웹 운용 화면)"]
    확인 = section.get("checks") or []
    if 확인:
        out.append("확인할 일: " + " / ".join(("❗" if c["level"] == "high" else "") + c["text"]
                                          for c in 확인[:REPORT_MAX_CHECKS])
                   + (f" 외 {len(확인) - REPORT_MAX_CHECKS}건" if len(확인) > REPORT_MAX_CHECKS else ""))  # fmt: skip
    else:
        out.append("확인할 일: 없음")
    모든 = list(board.get("rows") or [])
    줄 = [r for r in 모든 if not r.get("young")]

    def 칸(r: dict[str, Any]) -> str:
        v = r.get("vs_r_pp")
        return (f"{r['window_pct']:+.2f}%" + (f"(R 대비 {v:+.2f}%p)" if v is not None else "")
                + (" — 갓 엶, 개설 후" if r.get("young") else ""))  # fmt: skip

    위 = [r for r in 줄 if r.get("window_pct") is not None][:REPORT_TOP_ACCOUNTS]
    # 챔피언은 갓 연 계좌여도 빠뜨리지 않는다 — KIS 가 베끼는 계좌가 순위 줄에서 사라지면 안 된다(25.1512 검토 5)
    챔 = next((r for r in 모든 if r["id"] == mirror and r.get("window_pct") is not None), None)
    if 위 or 챔:
        글 = " · ".join(f"{r['id']} {칸(r)}" for r in 위)
        if 챔 and 챔 not in 위:
            글 += (" … " if 글 else "") + f"{mirror}(KIS) {칸(챔)}"
        out.append(f"순위(공통 구간 {board.get('window_start')}부터): {글}")
    체결 = section.get("fills_h") or []
    체결글 = [f"{_SIDE.get(f['side'], f['side'])} {f['name']} {f['quantity']}주 @ {_돈(f['price'], f['currency'])}"
             for f in 체결]  # fmt: skip
    out.append(f"{mirror} 체결(판정 {section.get('trade_date')}): " + (" · ".join(체결글) if 체결글 else "없음"))
    주문 = section.get("orders_h") or []
    # 체결 장은 주문의 것 — 장 끝난 뒤 손 실행이면 다음다음 장이다(25.1512 검토 4)
    장들 = sorted({str(o.get("fill_session")) for o in 주문 if o.get("fill_session")})
    장 = 장들[0] if len(장들) == 1 else section.get("session_date")
    if 주문:
        글 = " · ".join(f"{_SIDE.get(o['side'], o['side'])} {o['name']} {o['quantity']}주"
                       + (" 시장가" if o.get("order_type") == "market" or o.get("limit_price") is None
                          else f" {_돈(o['limit_price'], o['currency'])}")
                       for o in 주문[:REPORT_MAX_ORDERS])  # fmt: skip
        out.append(f"{mirror} 다음 장({장}) 주문: {글}"
                   + (f" 외 {len(주문) - REPORT_MAX_ORDERS}건" if len(주문) > REPORT_MAX_ORDERS else ""))  # fmt: skip
    else:
        out.append(f"{mirror} 다음 장({장}) 주문: 없음")

    def 건수(c: dict[str, int] | None) -> str:
        xs = sorted((a, n) for a, n in (c or {}).items() if n and a != mirror)
        return " · ".join(f"{a} {n}" for a, n in xs) or "없음"

    체결수, 주문수 = 건수(section.get("fills_by_account")), 건수(section.get("orders_by_account"))
    out.append(f"다른 계좌 — 체결 {체결수} / 주문 {주문수}")
    if kis_state is not None:
        글 = f"KIS모의투자: {kis_state}"
        if prev_kis is not None:
            셈: dict[str, int] = {}
            for o in prev_kis:
                셈[str(o.get("status"))] = 셈.get(str(o.get("status")), 0) + 1
            말 = {"accepted": "접수", "rejected": "거부", "sending": "응답 모름", "unknown": "모름"}
            지난 = " · ".join(f"{말.get(k, k)} {n}" for k, n in sorted(셈.items())) if 셈 else "낸 주문 없음"
            글 += f" · 지난 실행 보낸 결과 {지난}(체결은 다음 실행이 맞춤)"
        out.append(글)
    return out

