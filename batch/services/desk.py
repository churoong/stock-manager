"""운용 상황실 요약 — 순수 함수 (docs/desk.md 2장, docs/infra.md 25.1501).

웹 첫 화면(운용)이 읽는 숫자는 여기서 낸다 — 웹은 계산하지 않는다. 계좌 순위는 **공통 구간**(자리 잡은 계좌들의
첫 평가일 가운데 가장 늦은 날부터) 시간가중 수익률로 — 늦게 연 계좌(G·H)와 일찍 연 계좌를 같은 잣대로 견준다.
R 대비 = 공통 구간 수익률 − 무작위 대조 R1·R2 평균(%p): 무작위로 골라도 이만큼은 났다는 기준선.
갓 연 계좌(평가 `MIN_WINDOW_POINTS` 번 미만)는 공통 구간을 끌어당기지 않는다 — 계좌 하나를 새로 열 때마다 모든 계좌의
순위가 0 에서 다시 시작하지 않게(25.1503, 검토 8). 그 계좌는 제 개설일부터 재고 같은 구간 R 과 견준다
"""

from __future__ import annotations

from typing import Any

from batch.notify import formatter
from batch.services import paper as pp
from batch.services.paper import SEPARATE_IDS as _SEPARATE_IDS
from batch.services.paper import account_name as _account_name
from batch.services.paper import step_error_orders as _step_error_orders

#: 계좌 종류 — 화면 글. 모르는 계좌는 이름만 (docs/paper.md 1장, docs/company.md)
KIND = {"A": "회사", "C": "회사", "D": "회사", "E": "회사", "B": "기준(점수 상위)", "G": "추세 익절",
        "H": "지주사(KIS가 베낌)", "R1": "무작위 대조", "R2": "무작위 대조", "S": "스윙(단기 신호)",
        "T": "당일 단타(단기 신호)"}  # fmt: skip


def _이름(aid: object) -> str:
    """계좌 화면 이름(25.1893) — 사람에게 보이는 글에만. 식별자는 그대로 열쇠로 쓴다"""
    return _account_name(str(aid)) if aid is not None else ""


CONTROL_IDS = ("R1", "R2")
#: 따로 재는 실험 계좌 — 순위표 공통 구간의 시작과 챔피언 교체 후보(본페로니 도전자 수)에 넣지 않는다. 스윙 S 는 단기
#: 신호를 며칠씩 사고파는 다른 실험이라(docs/paper.md 16.3.1, 25.1872), 열리는 날 A~G 의 공통 구간·후보 문턱이 바뀌지
#: 않게. 순위표에는 제 개설일부터의 수익으로 맨 뒤에 따로 보인다. 정의처는 `services/paper.SEPARATE_IDS`
SEPARATE_IDS = _SEPARATE_IDS
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
    본 = {a for a in 살 if a not in SEPARATE_IDS} or set(살)
    자리 = {a for a in 본 if len(살[a]) >= MIN_WINDOW_POINTS} or 본
    시작 = max(살[a][0][0] for a in 자리)

    def 대조(start: str) -> float | None:
        값 = [x for r in CONTROL_IDS if (x := _구간(살.get(r) or [], start, None)) is not None]
        return sum(값) / len(값) if 값 else None

    기준선 = 대조(시작)
    줄 = []
    for a, v in 살.items():
        따로 = a in SEPARATE_IDS and a not in 자리
        어림 = a not in 자리 and not 따로
        pct = _구간(v, v[0][0] if 어림 or 따로 else 시작, None)
        선 = 대조(v[0][0]) if 어림 or 따로 else 기준선
        줄.append({"id": a, "name": (names or {}).get(a) or a, "kind": KIND.get(a, ""), "open": v[0][0],
                  "last": v[-1][0], "since_open_pct": (v[-1][1] - 1) * 100, "window_pct": pct, "young": 어림,
                  "separate": 따로,
                  "vs_r_pp": None if 선 is None or pct is None or a in CONTROL_IDS else pct - 선})  # fmt: skip
    줄.sort(key=lambda r: (r["separate"], r["young"], r["window_pct"] is None, -(r["window_pct"] or 0.0), r["id"]))
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


def _판구간(v: list[tuple[str, float]], since: str, to: str | None, first: str | None = None) -> float | None:
    """판 구간 수익률(%) — 결정일 `since` 의 종가부터 다음 판 결정일 `to` 의 종가까지(없으면 마지막 평가까지).

    `paper_daily.as_of` 는 배치를 돌린 **한국 날짜**이고 그 행은 그때까지 나온 종가로 잰 평가다 — 판정일 D 의 결정을 낸
    실행의 행(= D 보다 늦은 첫 평가)이 곧 "D 종가" 평가다(25.1505, 검토 1). `since` 가 '' 면 계좌의 첫 평가부터
    """
    if not v:
        return None
    if not since and first:
        # 대조(R)를 "처음부터" 판과 견줄 때는 그 계좌의 첫 평가일부터 — R 의 처음부터 재면 늦게 연 계좌(G·H)의
        # R 대비에 열기 전 R 의 움직임이 들어갔다(25.1571, 운용 감사 [권고] 3)
        시작 = next((t for d, t in v if d >= first), None)
    else:
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
    운과 가른다. `orders` = {(계좌, 판): 주문 수} — 없으면 None. `prereg` = 그 판의 사전 등록(25.1520)
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
            내것 = sorted(series.get(a) or [])
            pct = _판구간(내것, since, 끝)
            첫날 = 내것[0][0] if 내것 else None
            대조 = [x for r in CONTROL_IDS if (x := _판구간(sorted(series.get(r) or []), since, 끝, 첫날)) is not None]
            기준선 = sum(대조) / len(대조) if 대조 else None
            out[a].append({"version": ver, "from": since, "to": 끝, "prereg": prereg(a, ver),
                           "orders": None if orders is None else orders.get((a, ver), 0), "pct": pct,
                           "control_pct": 기준선, "vs_r_pp": None if pct is None or 기준선 is None or a in CONTROL_IDS
                           else pct - 기준선})  # fmt: skip
    return out


# ── 판 사전 등록 (docs/paper.md 2.5, 25.1520) ─────────────────────────────────────────────────────

#: 판마다 **바꾸기 전에** 적는 가설·잴 지표·판정 시점. 판을 올리는 마이그레이션과 같은 커밋에 한 줄을 더한다 —
#: tests/test_selection_evidence_1518.py 가 계좌 표의 지금 판이 모두 여기 있는지 본다(없으면 그 커밋이 깨진다).
#: 결과를 보고 가설을 고쳐 쓰면 운을 실력으로 읽는다 — 한 번 적은 줄은 고치지 않고 새 판에 새 줄. `retro` = 이 장치 전에
#: 연 판을 나중에 적은 것(사전 등록이 아니다). 시도 횟수 = 판 수 — 많이 바꿀수록 같은 성적도 덜 믿는다
PREREG: dict[tuple[str, int], dict[str, Any]] = {
    ("A", 1): {"hypothesis": "판 1 — 매도 규칙 하나만 다른 짝(A−B 황색 매도 등, docs/paper.md 1장)", "retro": True},
    ("C", 1): {"hypothesis": "판 1 — 매도 규칙 하나만 다른 짝(docs/paper.md 1장)", "retro": True},
    ("D", 1): {"hypothesis": "판 1 — 매도 규칙 하나만 다른 짝(기간 매도, docs/paper.md 1장)", "retro": True},
    ("E", 1): {"hypothesis": "판 1 — 매도 규칙 하나만 다른 짝(docs/paper.md 1장)", "retro": True},
    ("A", 2): {"hypothesis": "가치투자사 헌장(docs/company.md)으로 고른 종목이 같은 날 안 산 새 후보보다 더 오른다",
               "retro": True},
    ("C", 2): {"hypothesis": "모멘텀투자사 헌장으로 고른 종목이 같은 날 안 산 새 후보보다 더 오른다", "retro": True},
    ("D", 2): {"hypothesis": "장기보유투자사 헌장 — 덜 팔고 오래 들어 비용을 아끼면 기준(B)보다 낫다",
               "retro": True},
    ("E", 2): {"hypothesis": "안정투자사 헌장 — 낙폭을 줄여 변동성 대비 수익이 기준(B)보다 낫다", "retro": True},
    ("B", 1): {"hypothesis": "기준 — 아침 리포트 2부(점수 상위 배분)를 그대로 사면 무작위 대조(R)와 지수보다 낫다",
               "retro": True},
    ("G", 1): {"hypothesis": "목표가 뒤 추세를 따라 파는 것이 목표에서 전량 파는 것(B)보다 낫다", "retro": True},
    # 판 3·H 판 2 (25.1576, 0088) — 결함 수정 판이다: 공시부가 화~금 "자료 없음" 이던 것(요일마다 가결·부결이
    # 갈림)을 고쳐
    # 판단이 바뀌었고, H 는 회사 가중치 기준 평균을 표본 가중으로. 가설은 앞 판과 같다 — 시도 수에 셈
    ("A", 3): {"hypothesis": "가치투자사 헌장(docs/company.md)으로 고른 종목이 같은 날 안 산 새 후보보다 더 오른다 —"
               " 판 2 와 같은 가설, 공시부 덮개 결함 수정 판(25.1576)"},
    ("C", 3): {"hypothesis": "모멘텀투자사 헌장으로 고른 종목이 같은 날 안 산 새 후보보다 더 오른다 —"
               " 판 2 와 같은 가설, 공시부 덮개 결함 수정 판(25.1576)"},
    ("D", 3): {"hypothesis": "장기보유투자사 헌장 — 덜 팔고 오래 들어 비용을 아끼면 기준(B)보다 낫다 —"
               " 판 2 와 같은 가설, 공시부 덮개 결함 수정 판(25.1576)"},
    ("E", 3): {"hypothesis": "안정투자사 헌장 — 낙폭을 줄여 변동성 대비 수익이 기준(B)보다 낫다 —"
               " 판 2 와 같은 가설, 공시부 덮개 결함 수정 판(25.1576)"},
    ("H", 2): {"hypothesis": "회사들의 성적 가중 합의로 고르면 점수 상위 5(G)보다 낫다 — 실전 후보(KIS 가 베낌)."
               " 판 1 과 같은 가설, 투표 회사 판 3·기준 평균 표본 가중(25.1576)"},
    ("H", 1): {"hypothesis": "회사들의 성적 가중 합의로 고르면 점수 상위 5(G)보다 낫다 — 실전 후보(KIS 가 베낌)",
               "retro": True},
    ("R1", 1): {"hypothesis": "대조 — 가설 없음(같은 유니버스에서 무작위로 고르면 이만큼은 난다는 기준선)",
                "metric": "대조 — 판정 없음", "retro": True},
    ("R2", 1): {"hypothesis": "대조 — 가설 없음(R1 과 다른 씨앗의 무작위)", "metric": "대조 — 판정 없음",
                "retro": True},
    # 스윙 S (docs/paper.md 16.3.1, 25.1872) — 계좌 행(0092, 2026-10-29 개설)보다 **먼저**, 결과가 하나도 없을 때 적는다
    ("S", 1): {"hypothesis": "단기 신호를 다음 장 시가에 사서 종가 기준 +5%·−3%·10거래일(swing_close_v1)로 팔면"
               " 비용 뒤 거래당 평균 수익이 0 보다 크다 — 그림자 장부의 같은 규칙과 거래 단위로 견준다",
               "metric": "끝난 거래 수(≥ 30)·비용 뒤 평균·승률·손익비 · 같은 기간 그림자 swing_close_v1 과의 차이 ·"
                         " R 대비(같은 기간)",
               "judge": "끝난 거래 30건 뒤 처음 본다 — 비용 뒤 평균 > 0 이고 그림자와의 차이가 비용·제약으로 설명되면"
                        " 유지, 아니면 엔진·장부부터 본다. 판정은 사용자"},
    # 당일 단타 T (docs/paper.md 16.3.2, 25.1885) — 계좌 행(0092)보다 먼저, 결과가 하나도 없을 때
    ("T", 1): {"hypothesis": "단기 신호를 다음 장 시가에 사서 그날 종가에 팔면(day_v1) 비용 뒤 거래당 평균 수익이"
               " 0 보다 크다 — 그림자 장부의 같은 규칙과 거래 단위로 견준다",
               "metric": "끝난 거래 수(≥ 30)·비용 뒤 평균·승률·손익비 · 같은 기간 그림자 day_v1 과의 차이(짝마다)",
               "judge": "끝난 거래 30건 뒤 처음 본다 — 비용 뒤 평균 > 0 이고 그림자와의 차이가 비용·제약·하한가"
                        " 밤샘으로 설명되면 유지, 아니면 엔진·장부부터 본다. 판정은 사용자"},
}
#: 사전 등록 판의 공통 지표·판정 — 판마다 다르면 줄에 `metric`·`judge` 를 따로 적는다
PREREG_METRIC = "결정 단위 선정 하한 > 50(28일 묶음 6개 뒤) · 관문 구간 지수 대비 > 0 · R 대비 > 0"


def prereg(account: str, version: int) -> dict[str, Any] | None:
    """그 판의 사전 등록 — 없으면 None(등록 없이 연 판 — 화면이 "사전 등록 없음" 으로 보인다)"""
    x = PREREG.get((account, version))
    return None if x is None else {"metric": PREREG_METRIC, **x}


def prereg_status(
    series: dict[str, list[tuple[str, float]]], versions: dict[str, tuple[int, str]],
    evidence: dict[str, dict[str, Any]], index: dict[str, list[tuple[str, float | None, float | None, float]]],
) -> dict[str, dict[str, Any]]:
    """판마다 사전 등록 지표(`PREREG_METRIC`)의 **오늘 값** — {계좌: {version, from, n_blocks, lower, vs_index, vs_r}}
    (25.1710, 결정 원장 감사 [권고] 7 — 25.1691 에 남긴 것). 관문은 H 만 계산해 A·B·C·D·E·G 판을 판정할 값이 날마다
    남지 않았다 — 나중에 "묶음 6 에 처음 닿은 날" 의 값을 그대로 읽으려면 그날 값을 그날 적어 둬야 한다(날마다
    다시 셈하면 날마다 들여다보는 꼴). 운용 요약 줄(`paper_desk`, 하루 한 줄)에 실린다 — 읽기 0.
    `versions` = {계좌: (지금 판, 판 시작 결정일)}. 대조(R1·R2)는 뺀다. 관문과 같은 식(`gate` 의 칸)이라 잣대가 같다 —
    다만 하한(`lower`)은 사전 등록한 한쪽 95% 하한(칸의 `lower_95`)이다. 칸의 `now` 는 H 관문의 Pocock 하한(25.1966)
    """
    out: dict[str, dict[str, Any]] = {}
    for a, (판, 시작) in sorted(versions.items()):
        if a in CONTROL_IDS or not series.get(a):
            continue
        g = gate(series, a, since=시작, version=판, recon_bad_days=0, batch_failed=0, kis=None,
                 evidence=evidence.get(a), index=index.get(a))  # fmt: skip
        칸 = {i["key"]: i for i in g["items"]}
        out[a] = {"version": 판, "from": g["from"], "points": 칸["days"]["now"], "n_blocks": 칸["selection"]["n"],
                  "lower": 칸["selection"]["lower_95"], "vs_index": 칸["vs_index"]["now"],
                  "vs_r": 칸["vs_r"]["now"]}  # fmt: skip
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
#: 결정 단위 선정 증거(docs/paper.md 10.1, 25.1518) — 관문에 쓰려면 이만큼 묶음(28 달력일)이 쌓여야 한다(약 반년)
GATE_SEL_MIN_BLOCKS = 6
#: 무작위로 골랐을 때의 분위 — 관문은 한쪽 하한이 이것을 넘어야 한다
GATE_SEL_NULL = 50.0
#: 판정 시점 — 지금 판의 평가 횟수가 이것에 닿은 날에만 통과를 판정한다(25.1963, docs/funnel.md 3장 "2 → 3" — 날마다
#: 다시 보는 순차 검정의 거짓 통과 부풀림을 막는다). 첫 시점은 `GATE_MIN_POINTS` 와 같다(시험이 대조)
GATE_LOOKS = (126, 189, 252)
#: 판정 시점마다 결정 단위 하한(선정·가상 대조)에 쓰는 한쪽 유의수준 — Pocock 문턱, 판정 셋에 전체 약 5%
#: (funnel.md 3장 "한 번 약 2.2%" `[확인필요]` — 같은 간격 셋의 Pocock 양쪽 0.05 의 명목 값 0.0221. 판정 간격이 2:3:4 라
#: 같은 간격 가정과 조금 다르다 — 한쪽으로 쓰면 보수적)
GATE_POCOCK_ALPHA = 0.0221
#: 챔피언 교체 — 짝지은 묶음 차이의 최소 공통 묶음 수, 유의수준(여러 도전자를 견주므로 본페로니로 나눈다 — 나눈 값이
#: `GATE_POCOCK_ALPHA` 보다 느슨하면 그것으로, 25.1965 `champion_alpha`)
CHAMPION_EVIDENCE_MIN_BLOCKS = 4
CHAMPION_ALPHA = 0.05


def _수익률들(v: list[tuple[str, float]]) -> list[float]:
    """연이은 평가 사이 수익률(소수). 0 이하 값은 건너뛴다"""
    return [b / a - 1 for (_d1, a), (_d2, b) in zip(v, v[1:], strict=False) if a > 0 and b > 0]


def index_return(
    rows: list[tuple[str, float | None, float | None, float]], start: str,
) -> tuple[float | None, list[str]]:
    """지수 그림자 수익률(%)과 쓴 지수 — `start` 이하 마지막 행부터 마지막 행까지 **행마다 이어 곱한다**
    (docs/desk.md 6장).

    그림자(docs/paper.md 4장)는 입금마다 그 입금일 지수로 단위를 산 값이다. 처음(25.1519)에는
    (끝 − 구간 입금) ÷ 시작 − 1 한 번으로 냈는데, 그러면 구간 중 입금이 입금 뒤에 낸 지수 수익이 시작 값을 분모로
    한 번 더 들어갔다 — 1천만에 한 달 뒤 1천만 더 넣고 지수 +10% 면 +20% 로 나와 지수와 똑같은 전략이 관문에서
    떨어지고, 지수 −10% 면 −20% 로 나와 지수와 같은 전략이 관문을 **잘못 통과**했다(25.1523, 검토 1).
    행마다 (그날 그림자 − 그날 입금 증가) ÷ 전날 그림자 를 곱하면 입금은 그날 지수로 산 몫이라 빠진다
    (입금일과 행 날짜가 하루 어긋나는 오차만 남는다). 취소 날은 원래 단위를 원래 값으로 되돌려 오차가 남는다
    (취소는 모든 계좌에 현금이 남아야 돼서 드물다 `[확인필요]`).
    KOSPI·S&P500 각각 이어 곱해 평균 — 한쪽 그림자가 빈 행이 있으면 그쪽은 뺀다(쓴 지수를 함께 돌려준다, 검토 2).
    둘 다 배당 빠진 가격지수라 기준이 연 1~2% 낮게 나온다(관문에 유리한 쪽 `[확인필요]`)
    """
    v = sorted(rows)
    앞 = [k for k, r in enumerate(v) if r[0] <= start]
    if not 앞:
        return None, []
    구간 = v[앞[-1]:]
    이름 = {1: "KOSPI", 2: "S&P500"}
    수익: dict[int, float] = {}
    for i in (1, 2):
        곱 = 1.0
        for a, b in zip(구간, 구간[1:], strict=False):
            if a[i] is None or b[i] is None or float(a[i]) <= 0:
                break
            곱 *= (float(b[i]) - (float(b[3]) - float(a[3]))) / float(a[i])
        else:
            if 구간[0][i] is not None and float(구간[0][i]) > 0:
                수익[i] = (곱 - 1) * 100
    if not 수익:
        return None, []
    return sum(수익.values()) / len(수익), [이름[i] for i in sorted(수익)]


def cash_equivalent_return(
    twr: list[tuple[str, float]], cash: list[tuple[str, float, float]],
    index: list[tuple[str, float | None, float | None, float]], start: str, used: list[str],
) -> tuple[float | None, float | None]:
    """쉬는 현금이 지수 수익을 냈다고 본 수익률(%)과 평균 현금 비중(%) — 관문 지수 대비 옆 **참고 칸**
    (25.1540, 31회차 P3).

    r_eq(t) = r_twr(t) + c(t−1)·r_idx(t). c = 전날 현금 ÷ 전날 평가(`paper_daily.cash_krw`·`equity_krw`),
    r_idx = `index_return` 과 같은 행마다 입금 보정 그림자 수익. **같은 지수(`used`)마다 따로 이어 곱해 끝에 평균**
    (`index_return` 과 같은 꼴, 25.1541). 구간도 같다(`start` 이하 마지막 행부터).
    한 행이라도 현금·평가·그림자가 비면 None — 모름을 0 으로 채우지 않는다. 관문 통과(`passed`)에는 쓰지 않는다
    """
    현금 = {d: (c, e) for d, c, e in cash}
    지수 = {r[0]: r for r in index}
    v = sorted(twr)
    앞 = [k for k, r in enumerate(v) if r[0] <= start]
    칸 = {"KOSPI": 1, "S&P500": 2}
    if not 앞 or not used or any(u not in 칸 for u in used):
        return None, None
    구간 = v[앞[-1]:]
    # 지수마다 따로 이어 곱하고 끝에 평균 — `index_return` 이 반반 사서 그대로 둔 꼴이라 같은 꼴이어야 현금 100% 계좌가
    # 0 이 된다(날마다 평균을 곱하면 날마다 반반으로 다시 맞춘 꼴이라 KOSPI +27%·S&P 제자리에서 −1.84%p, 25.1541)
    곱 = {u: 1.0 for u in used}
    비중들: list[float] = []
    for (da, ta), (db, tb) in zip(구간, 구간[1:], strict=False):
        ce, ia, ib = 현금.get(da), 지수.get(da), 지수.get(db)
        if ce is None or ia is None or ib is None or not ce[1] or ce[1] <= 0 or ta <= 0:
            return None, None
        c = float(ce[0]) / float(ce[1])
        비중들.append(c)
        for u in used:
            i = 칸[u]
            if ia[i] is None or ib[i] is None or float(ia[i]) <= 0:
                return None, None
            r_idx = (float(ib[i]) - (float(ib[3]) - float(ia[3]))) / float(ia[i]) - 1
            곱[u] *= 1 + (tb / ta - 1) + c * r_idx
    if not 비중들:
        return None, None
    return (sum(곱.values()) / len(곱) - 1) * 100, 100 * sum(비중들) / len(비중들)


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


#: 가상모의투자 단계의 건너뜀 가운데 사고가 아닌 것 — 입금이 없으면 할 일이 없다
PAPER_SKIP_OK = ("입금 없음",)


def paper_step_failed(row: dict[str, Any]) -> bool:
    """그 실행의 가상모의투자 단계가 끝까지 돌지 못했나 — `step_log.paper.error`(예외) 또는 입금 없음 밖의 `skipped`
    (다른 시장 잠금 등). 실행은 `partial` 로 끝나 예전엔 무사고로 셌다(25.1636, 36회차 P3). `paper` 칸이 없는 실행
    (기능 전 옛 행·dry-run)은 모름 — 사고로 세지 않는다"""
    if row.get("paper_error") is not None:  # 빈 글 예외(`StopIteration()` 등)도 — 교차검증 [필수]
        return True
    # 계좌 하나만 깨진 날도 사고다(25.1920 — 계좌별로 가둔 예외, 단계 전체는 끝까지 돌았다)
    if row.get("paper_account_errors") not in (None, "", "{}"):
        return True
    건너뜀 = row.get("paper_skipped")
    return bool(건너뜀) and str(건너뜀) not in PAPER_SKIP_OK


def batch_incident_days(rows: list[dict[str, Any]], expected: dict[str, list[str]]) -> int:
    """일일 배치 사고 날 수 — 일마다(`expected` = {작업: 다뤘어야 할 거래일들}) 성공(`success`·`partial` **이면서
    가상모의투자 단계가 끝까지 돈** — 25.1636) 기록이 없거나 실패(`failed`) 기록이 있던 날 (docs/desk.md 5장 관문
    "운영 무사고", 25.1572). 단계가 잠금으로 건너뛴 뒤 같은 거래일 손 실행이 끝까지 돌았으면 사고가 아니다.
    한도로 건너뜀·끝맺지 못함·실행 기록조차
    못 남긴 날(한도로 첫 읽기에서 깨짐) 모두 "성공 없음" 으로 잡힌다"""
    성공: set[tuple[str, str]] = set()
    실패: set[tuple[str, str]] = set()
    for r in rows:
        k = (str(r.get("job_name")), str(r.get("trade_date") or "")[:10])
        if r.get("status") in ("success", "partial") and not paper_step_failed(r):
            성공.add(k)
        elif r.get("status") == "failed":
            실패.add(k)
    return sum(1 for job, 날들 in expected.items() for d in set(날들) if (job, d) not in 성공 or (job, d) in 실패)


#: 관문 R 대비·지수 대비를 믿기 어려운 H 의 원가·묵은 평가 비중(관문 구간 평균, 평가 총액 대비 %) — 25.1709·1711,
#: H·R 감사 [권고] 3. 5% 는 정한 값(되돌릴 수 있음): 평가가 멈춘 몫이 이보다 크면 그 몫의 실제 손익이 비교에서 빠진다
#: — H 가 넘으면 두 칸을 불통으로(더 엄격하게만). R 쪽은 참고
GATE_VALUATION_GAP_PCT = 5.0
#: 마지막 종가가 그날보다 이 달력일보다 오래되면 묵은 평가(장기 정지·상장폐지 절차) — 긴 연휴(국내 최장 약 10일)
#: 첫날엔 잠깐 잡히지만 구간 평균이라 작다
VALUATION_STALE_DAYS = 7


def valuation_gaps(rows: list[dict[str, Any]]) -> dict[str, list[tuple[str, float]]]:
    """`paper_daily` 줄(account_id·as_of·equity_krw·positions_json) → {계좌: [(as_of, 원가·묵은 평가 비중 %)]}
    (25.1709). 원가 평가 = 보유 줄의 `unknown_why`(기업행위 멈춤·종가 없음·환율 없음 — 평가가 원가로 멈춤), 묵은
    평가 = `price_date` 가 그날보다 `VALUATION_STALE_DAYS` 넘게 앞섬. 평가 총액이 없거나 0 이하인 줄은 뺀다"""
    import json
    from datetime import date

    out: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        eq = r.get("equity_krw")
        if eq is None or float(eq) <= 0:
            continue
        try:
            줄 = json.loads(r.get("positions_json") or "[]")
            날 = date.fromisoformat(str(r["as_of"])[:10])
        except (TypeError, ValueError):
            continue
        멈춤 = 0.0
        for p in 줄 if isinstance(줄, list) else []:
            v = float(p.get("value_krw") or 0) if isinstance(p, dict) else 0.0
            if not isinstance(p, dict) or v <= 0:
                continue
            if p.get("unknown_why"):
                멈춤 += v
            elif p.get("price_date"):
                try:
                    if (날 - date.fromisoformat(str(p["price_date"])[:10])).days > VALUATION_STALE_DAYS:
                        멈춤 += v
                except ValueError:
                    continue
        out.setdefault(str(r["account_id"]), []).append((str(r["as_of"]), 100 * 멈춤 / float(eq)))
    return out


#: 노출 맞춘 R 대비(25.1702)에서 R 의 투자 비중이 이보다 작은 날은 뺀다 — 주식 몫 수익(하루 수익 ÷ 투자 비중)이
#: 분모 0 근처에서 터진다. 5% 는 정한 값(되돌릴 수 있음) — 뺀 날 수를 칸에 적는다
EXPOSURE_MIN = 0.05


def exposure_matched_r(
    series: dict[str, list[tuple[str, float]]], cash: dict[str, list[tuple[str, float, float]]], mirror: str,
    start: str,
) -> dict[str, Any] | None:
    """**노출 맞춘 R 수익**(%) — R 의 주식 몫 하루 수익(하루 수익 ÷ 전날 투자 비중)에 **H 의 전날 투자 비중**을 곱해
    이어 곱한 것, R1·R2 평균 (25.1702, H·R 감사 [필수] 1 고칠 방향 2). 관문 "R 대비" 는 H 와 R 의 투자 속도(분할
    지정가·합의 후보 수 vs 시가 일괄)가 달라 하락장엔 현금만으로 통과하고 상승장엔 실력이 있어도 떨어졌다 — 같은 노출로
    맞추면 그 몫이 빠진다. 투자 비중 = 1 − 현금 ÷ 평가(`paper_daily`, 현금은 이자 0).
    구간은 `_구간` 과 같다(`start` 이하 마지막 행부터). H·R 어느 쪽이든 그날 현금·평가가 비면 모름(None) — 0 으로 채우지
    않는다. R 투자 비중이 `EXPOSURE_MIN` 아래인 날은 빼고 수를 센다. **참고 칸** — 관문 통과(`passed`)에 쓰지 않는다
    """
    def 비중(c: float, e: float) -> float:
        return min(max(1 - c / e, 0.0), 1.0)

    h현금 = {d: (c, e) for d, c, e in cash.get(mirror) or []}
    수익들: list[float] = []
    h비중: list[float] = []
    r비중: list[float] = []
    뺀 = 0
    for rid in CONTROL_IDS:
        v = sorted(series.get(rid) or [])
        r현금 = {d: (c, e) for d, c, e in cash.get(rid) or []}
        앞 = [k for k, r in enumerate(v) if r[0] <= start]
        구간 = v[(앞[-1] if 앞 else 0):]
        if len(구간) < 2:
            continue
        곱 = 1.0
        for (da, ta), (_db, tb) in zip(구간, 구간[1:], strict=False):
            h, r = h현금.get(da), r현금.get(da)
            if h is None or r is None or not h[1] or not r[1] or h[1] <= 0 or r[1] <= 0 or ta <= 0:
                return None
            eh, er = 비중(float(h[0]), float(h[1])), 비중(float(r[0]), float(r[1]))
            h비중.append(eh)
            r비중.append(er)
            if er < EXPOSURE_MIN:
                뺀 += 1
                continue
            곱 *= 1 + eh * (tb / ta - 1) / er
        수익들.append((곱 - 1) * 100)
    if not 수익들:
        return None
    return {"r_pct": sum(수익들) / len(수익들), "h_exposure_pct": 100 * sum(h비중) / len(h비중),
            "r_exposure_pct": 100 * sum(r비중) / len(r비중), "skipped_days": 뺀}  # fmt: skip


def gate(
    series: dict[str, list[tuple[str, float]]], mirror: str, *, since: str = "", version: int | None = None,
    recon_bad_days: int, batch_failed: int, kis: dict[str, Any] | None, evidence: dict[str, Any] | None = None,
    index: list[tuple[str, float | None, float | None, float]] | None = None,
    cash: list[tuple[str, float, float]] | None = None,
    control_cash: dict[str, list[tuple[str, float, float]]] | None = None,
    valuation: dict[str, list[tuple[str, float]]] | None = None,
    prev: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """실전 전환 1단계 관문 진도 — {stage, account, version, from, items: [{key, label, now, target, ok}], passed,
    next, looks, next_look}.

    **판정 시점 고정**(25.1963): 통과(`passed`)는 지금 판의 평가 횟수가 `GATE_LOOKS` 에 닿은 날의 판정(`looks`)으로만
    정한다 — 그 사이 날의 `items` 는 진도일 뿐이다. 판정은 그날 모든 칸이 맞으면 통과이고, 결정 단위 하한은 Pocock 문턱
    (`GATE_POCOCK_ALPHA`)으로 잰다. 앞선 판정은 `prev`(직전 운용 요약의 관문)에서 같은 계좌·같은 판일 때만 이어받는다 —
    판을 올리면 처음부터(새 사전 등록). 한 번 통과하면 그 판에서는 통과로 남는다(다음 단계는 사용자 결정).

    1단계 = 모의 성적·운영 무사고(CLAUDE.md). 잣대는 KIS 가 베끼는 계좌(`mirror`, 지금 H)의 **지금 판** —
    실전에 옮길 전략이다. `since` = 지금 판을 처음 본 결정일(판 이력, '' = 처음부터) — 그 뒤 평가만 센다
    (판을 섞지 않는다, 25.1511 검토 3). 비교 구간은 **관문 자기 구간** = 지금 판의 첫 평가와 R1·R2 첫 평가 가운데
    가장 늦은 날부터 — 순위표의 공통 구간을 빌리면 새 계좌 하나가 20번째 평가에 닿는 날 관문 판정이 뒤집혔다
    (25.1511 검토 1). `kis` = {kept_pct, n}(국내·미국 합). `evidence` = 그 계좌 지금 판의 결정 단위 선정 증거
    (`paper.selection_evidence`, 25.1518) — "R 대비 > 0" 은 부호 하나라 실력이 없어도 반쯤 통과한다.
    `index` = 그 계좌의 지수 그림자 [(as_of, KOSPI 그림자, S&P500 그림자, 총 입금)] — 실전 돈의 기회비용은 R 이 아니라
    지수 ETF 다(25.1519). `cash` = 그 계좌의 [(as_of, 현금, 평가)] — 지수 대비 항목의 참고 칸 `cash_eq`(25.1540)
    """
    전체 = sorted(series.get(mirror) or [])
    v = [p for p in 전체 if not since or p[0] > since]
    첫들 = [x[0][0] for x in [v] + [sorted(series.get(r) or []) for r in CONTROL_IDS] if x]
    시작 = max(첫들) if v and 첫들 else None
    pct = _구간(전체, 시작, None) if 시작 else None
    대조수익 = [x for r in CONTROL_IDS if 시작 and (x := _구간(sorted(series.get(r) or []), 시작, None)) is not None]
    vs_r = pct - sum(대조수익) / len(대조수익) if pct is not None and 대조수익 else None
    # 참고 — 노출을 H 에 맞춘 R(25.1702). 통과 판정에 넣지 않는다
    맞춤 = (exposure_matched_r(series, {mirror: cash or [], **(control_cash or {})}, mirror, 시작)
          if 시작 and cash and control_cash else None)  # fmt: skip
    if 맞춤 is not None and pct is not None:
        맞춤["vs_r"] = pct - 맞춤["r_pct"]
    # 원가·묵은 평가 비중(25.1709) — 관문 구간(시작 뒤) 평균. H 나 R(R1·R2 평균)이 문턱을 넘으면 R 대비를 믿기 어렵다
    멈춘평가: dict[str, Any] | None = None
    if valuation is not None and 시작:
        def 평균(a: str) -> float | None:
            xs = [x for d, x in valuation.get(a) or [] if d > 시작]
            return sum(xs) / len(xs) if xs else None

        r평균 = [x for r in CONTROL_IDS if (x := 평균(r)) is not None]
        멈춘평가 = {"h_pct": 평균(mirror), "r_pct": sum(r평균) / len(r평균) if r평균 else None,
                 "limit": GATE_VALUATION_GAP_PCT}  # fmt: skip
        # **H 쪽만 판정에 넣는다**(25.1711, 25.1709 교차검증 [권고] 1·2) — 원가로 멈춘 보유는 대개 손실을 숨긴다.
        # H 가 숨기면 H 가 좋아 보여 거짓 통과로 가고, R 이 숨기면 R 이 좋아 보여 R 대비가 **작아진다**(이미 보수 쪽).
        # R 쪽까지 막으면 R1 의 이어 멈춤 종목 하나로 H 관문이 판 끝까지 닫혔다(풀 길이 판 올림·장부 고침뿐). R 은
        # 참고로 싣는다. 같은 H 수익을 쓰는 지수 대비 칸도 같이 막는다
        멈춘평가["over"] = 멈춘평가["h_pct"] is not None and 멈춘평가["h_pct"] > GATE_VALUATION_GAP_PCT
    대조 = [x for r in CONTROL_IDS if 시작 and (x := risk_ratio(sorted(series.get(r) or []), 시작)) is not None]
    대조비 = sum(대조) / len(대조) if 대조 else None
    내비 = risk_ratio(전체, 시작) if 시작 else None
    지수, 쓴지수 = index_return(index or [], 시작) if 시작 else (None, [])
    vs_idx = pct - 지수 if pct is not None and 지수 is not None else None
    # 참고 — 쉬는 현금이 지수를 냈다면(25.1540). 통과 판정에 넣지 않는다(31회차 검증 A 1)
    eq_pct, 현금비중 = (cash_equivalent_return(전체, cash or [], index or [], 시작, 쓴지수)
                    if 시작 and 지수 is not None else (None, None))  # fmt: skip
    cash_eq = {"vs_index": eq_pct - 지수 if eq_pct is not None and 지수 is not None else None,
               "return_pct": eq_pct, "avg_cash_pct": 현금비중}  # fmt: skip
    kept, kn = (kis or {}).get("kept_pct"), int((kis or {}).get("n") or 0)
    ev = evidence or {}
    가상 = ev.get("control") or {}
    # 결정 단위 하한은 Pocock 문턱으로(25.1963) — 증거의 95% 하한(`lower`)은 참고로 싣는다
    하한, 묶음수 = pocock_lower(ev), int(ev.get("n_blocks") or 0)
    대조하한, 대조묶음수 = pocock_lower(가상), int(가상.get("n_blocks") or 0)
    판글 = f"지금 판{'' if version is None else f' {version}'}"
    items = [
        {"key": "days", "label": f"모의 기간({판글}의 평가 횟수)", "now": len(v), "target": GATE_MIN_POINTS,
         "ok": len(v) >= GATE_MIN_POINTS},
        {"key": "vs_r", "label": "관문 구간 R 대비(%p)", "now": vs_r, "target": 0, "exposure_matched": 맞춤,
         "valuation_gap": 멈춘평가,
         "ok": vs_r is not None and vs_r > 0 and not (멈춘평가 or {}).get("over")},
        {"key": "vs_index", "label": "관문 구간 지수 대비(%p) — 같은 돈을 KOSPI·S&P500 반반에 넣었다면",
         "now": vs_idx, "target": 0, "index_pct": 지수, "index_used": 쓴지수, "cash_eq": cash_eq,
         "valuation_gap": 멈춘평가,
         "ok": vs_idx is not None and vs_idx > 0 and not (멈춘평가 or {}).get("over")},
        {"key": "ratio", "label": "변동성 대비 수익 — 무작위 대조 평균보다", "now": 내비, "target": 대조비,
         "ok": 내비 is not None and 대조비 is not None and 내비 > 대조비},
        {"key": "incidents",
         "label": f"운영 무사고(최근 {GATE_INCIDENT_DAYS}일 장부 검산 어긋남·일일 배치 사고 날 — 실패·성공 없음)",
         "now": recon_bad_days + batch_failed, "target": 0, "ok": recon_bad_days + batch_failed == 0,
         "detail": {"recon_bad_days": recon_bad_days, "batch_failed": batch_failed}},
        {"key": "kis", "label": f"KIS 실현율(%, 최근 28일, 표본 {GATE_KEPT_MIN_N} 이상)", "now": kept,
         "target": GATE_KEPT_PCT, "n": kn, "path_pct": (kis or {}).get("path_pct"), "path_n": (kis or {}).get("path_n"),
         "ok": kept is not None and kn >= GATE_KEPT_MIN_N and float(kept) >= GATE_KEPT_PCT},
        {"key": "selection",
         "label": (f"결정 단위 선정 — 같은 날 새 후보 안 분위의 한쪽 하한(Pocock α {GATE_POCOCK_ALPHA:.2%}, 28일 묶음"
                   f" {GATE_SEL_MIN_BLOCKS}개 이상)"),
         "now": 하한, "target": GATE_SEL_NULL, "n": 묶음수, "mean": ev.get("mean"), "lower_95": ev.get("lower"),
         "ok": 하한 is not None and 묶음수 >= GATE_SEL_MIN_BLOCKS and float(하한) > GATE_SEL_NULL},
        # 가상 대조(25.1697, H·R 감사 [필수]) — R 대비가 규칙 차이(진입·매도·투자 속도)를 재는 것을 받친다.
        # AND 로만 더함
        {"key": "control",
         "label": ("가상 대조 — 주문 낸 새 종목이 같은 날 무작위 20종목 안 분위의 한쪽 하한"
                   f"(Pocock α {GATE_POCOCK_ALPHA:.2%}, 28일 묶음 {GATE_SEL_MIN_BLOCKS}개 이상)"),
         "now": 대조하한, "target": GATE_SEL_NULL, "n": 대조묶음수, "mean": 가상.get("mean"),
         "lower_95": 가상.get("lower"),
         "ok": 대조하한 is not None and 대조묶음수 >= GATE_SEL_MIN_BLOCKS and float(대조하한) > GATE_SEL_NULL},
    ]  # fmt: skip
    다음 = "2단계 손 매매 준수율·슬리피지 → 3단계 소액 승인형 → 4단계 한도 내 자동 — 단계마다 사용자 결정"
    looks = gate_looks(prev, mirror, version, len(v), v[-1][0] if v else None, items)
    한 = {x["n"] for x in looks}
    return {"stage": 1, "account": mirror, "version": version, "from": 시작, "items": items,
            "passed": any(x["passed"] for x in looks), "next": 다음, "looks": looks,
            "next_look": next((L for L in GATE_LOOKS if L not in 한), None)}  # fmt: skip


def pocock_lower(ev: dict[str, Any]) -> float | None:
    """결정 단위 증거(`pp.block_bound` 꼴 — mean·se·n_blocks)의 한쪽 하한을 Pocock 문턱(`GATE_POCOCK_ALPHA`)으로
    (25.1963). 묶음이 `pp.EVIDENCE_MIN_BLOCKS` 보다 적거나(95% 하한도 없는 자리) 재료가 없으면 None"""
    m, se, n = ev.get("mean"), ev.get("se"), int(ev.get("n_blocks") or 0)
    if m is None or se is None or n < pp.EVIDENCE_MIN_BLOCKS:
        return None
    return float(m) - pp.t_quantile(1 - GATE_POCOCK_ALPHA, n - 1) * float(se)


def gate_looks(prev: dict[str, Any] | None, mirror: str, version: int | None, n: int, day: str | None,
               items: list[dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """판정 시점 기록(25.1963) — 앞선 관문(`prev`)이 같은 계좌·같은 판이면 그 `looks` 를 이어받고, 평가 횟수 `n` 이 닿은
    아직 판정 안 한 시점마다 오늘 칸으로 판정을 더한다(운용 요약이 그 시점 날을 건너뛰었으면 닿은 첫날 — `at` 에 실제
    횟수). 통과 = 그날 모든 칸이 맞음, `failed` = 안 맞은 칸"""
    같은판 = bool(prev) and prev.get("account") == mirror and prev.get("version") == version
    looks = [dict(x) for x in ((prev or {}).get("looks") or []) if isinstance(x, dict)] if 같은판 else []
    한 = {x.get("n") for x in looks}
    for L in GATE_LOOKS:
        if n >= L and L not in 한:
            looks.append({"n": L, "at": n, "date": day, "passed": all(i["ok"] for i in items),
                          "failed": [i["key"] for i in items if not i["ok"]]})  # fmt: skip
    return looks


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
            # 거부는 **베낀 주문**의 거부만 — 장중 손절·익절(KIS 에서만 낸 매도) 거부는 따로(25.1779, 25.1773 교차검증
            # [참고]: "끝난 베낀 주문 n · 거부 X" 의 X 에 베낀 주문이 아닌 장중 매도 거부가 섞였다)
            장중거부 = sum(int(x.get("guard_rejected") or 0) for x in kis)
            근거 = (f"최근 {창}일 끝난 베낀 주문 {n} · 모름 {sum(int(x.get('unknown') or 0) for x in kis)}"
                  f" · 거부 {sum(int(x.get('rejected') or 0) for x in kis) - 장중거부}"
                  + (f" · 장중 매도 거부 {장중거부}" if 장중거부 else ""))  # fmt: skip
        out.append({"key": key, "label": label, "status": status, "note": note, "evidence": 근거})
    return out


def _경로실패(x: dict[str, Any]) -> int:
    """가상은 찼는데 경로가 실패한(모름·거부) 주문 수(`path_fail`, 25.1627). 그 칸이 없는 옛 요약(하루치)은
    모름·거부 전부 — 낮은 쪽으로"""
    if "path_fail" in x:
        return int(x["path_fail"] or 0)
    return int(x.get("unknown") or 0) + int(x.get("rejected") or 0)


def combine_kept(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """국내·미국 KIS 비교 요약을 합친 실현율 — 둘 다 참 ÷ (둘 다 참 + 가상만 참), 표본 = 그 분모.

    `path_pct` = **경로 포함 실현율**(참고 칸, 관문 판정에 넣지 않는다) — 가상은 찼는데 모름(미국 예약이 개장 때
    전송 안 됨 등)·거부인 주문을 못 찬 것으로 분모에 더한 최악 경계(가상도 안 찬 주문은 넣지 않는다, 25.1627).
    실현율은 둘을 빼 높게 기울 수 있다(15.8) — 경로 실패가 잦으면 두 칸이 벌어진다(25.1624, 35회차 곁가지 1)"""
    둘 = sum(int((x or {}).get("both") or 0) for x in summaries)
    가상만 = sum(int((x or {}).get("internal_only") or 0) for x in summaries)
    경로 = sum(_경로실패(x or {}) for x in summaries)
    return {"kept_pct": 둘 / (둘 + 가상만) * 100 if 둘 + 가상만 else None, "n": 둘 + 가상만,
            "path_pct": 둘 / (둘 + 가상만 + 경로) * 100 if 둘 + 가상만 + 경로 else None,
            "path_n": 둘 + 가상만 + 경로}  # fmt: skip


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
    # 따로 재는 S 는 제 개설일부터 잰 값이라 공통 구간 이웃과 견주지 않는다(25.1873, 25.1872 교차검증 [권고])
    줄 = [r for r in leaderboard.get("rows") or []
         if not r.get("young") and not r.get("separate") and r.get("window_pct") is not None]  # fmt: skip
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


def champion(
    series: dict[str, list[tuple[str, float]]], mirror: str, evidence: dict[str, dict[str, Any]] | None = None,
    since: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """챔피언 교체 후보 — 지금 챔피언(`mirror`, KIS 가 베끼는 계좌)을 **꾸준히** 앞서는 전략.

    계좌마다 챔피언과 둘 다 평가가 있는 날만 맞춰, 가장 최근 끝에서부터 겹치지 않는 `CHAMPION_BLOCK` 평가 블록으로
    나눠 블록마다 수익을 견준다.
    후보 = 블록 `CHAMPION_MIN_BLOCKS` 개 이상 · 이긴 블록 비율 `CHAMPION_WIN_SHARE` 이상 · 맞춘 기간 전체 수익도 앞섬.
    한 번 크게 이긴 것이 아니라 여러 구간에서 이겼는지를 본다. 무작위 대조는 뺀다. 교체는 사용자 결정.

    **결정 단위 근거도 있어야 후보다**(25.1518) — 블록 규칙만으로는 실력이 없는 도전자 여럿 가운데 석 달 안에 거의 늘
    "후보" 가 나왔다. `evidence` = {계좌: 선정 증거}의 28일 묶음별 산 종목 20 거래일 지수 대비 초과(`excess_blocks`)를
    챔피언과 **같은 묶음끼리 짝지어** 차이의 한쪽 하한을 낸다. 여러 도전자를 한꺼번에 보므로 유의수준을 도전자 수로
    나눈다(본페로니). `blocks_ok` = 예전 블록 규칙, `candidate` = 블록 규칙 그리고 결정 단위 하한 > 0
    """
    h = dict(sorted(series.get(mirror) or []))
    판 = since or {}
    out = []
    # 본페로니 도전자 수는 **견줄 자격이 있는 계좌 수**다 — 아래에서 평가가 2개 미만이라 빠지는 계좌(새 계좌·판을 막
    # 바꾼 계좌)까지 센다. 줄 수로 나누면 그런 계좌가 들고 날 때마다 유의수준이 흔들려 같은 자료로 후보가 섰다
    # 사라졌다(25.1915, 25.1905 L2 후보 ①)
    도전 = [a for a in series if a != mirror and a not in CONTROL_IDS and a not in SEPARATE_IDS]
    for a, v in sorted(series.items()):
        if a not in 도전:  # 따로 재는 실험은 도전자 수에 넣지 않는다(25.1872)
            continue
        # **두 계좌 모두 지금 판인 구간만**(`since` = 계좌마다 지금 판의 시작 결정일, 판 이력) — 관문은 지금 판만 보는데
        # 챔피언 블록·같은 기간 수익은 판을 가리지 않아 옛 판 성적으로 후보가 섰다(25.1575, 운용 감사 [참고])
        시작 = max(판.get(mirror) or "", 판.get(a) or "")
        날 = [d for d, _t in sorted(v) if d in h and (not 시작 or d > 시작)]
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
                    "blocks_ok": 후보, "candidate": 후보})  # fmt: skip
    _결정근거(out, mirror, evidence or {}, len(도전))
    return sorted(out, key=lambda r: (not r["candidate"], -(r["wins"] / r["blocks"] if r["blocks"] else 0),
                                      -((r["pct"] or 0) - (r["champion_pct"] or 0))))  # fmt: skip


def champion_alpha(challengers: int) -> float:
    """챔피언 결정 단위 하한의 한쪽 유의수준 — 본페로니(`CHAMPION_ALPHA` ÷ 도전자 수)와 관문 Pocock 문턱
    (`GATE_POCOCK_ALPHA`) 가운데 더 보수적인(작은) 값(25.1965, docs/desk.md 6장 "더 보수적으로만"). 후보도 날마다 다시
    보므로 도전자가 하나·둘이라 본페로니가 느슨할 때 관문 한 번 문턱보다 느슨하지 않게 막는다"""
    return min(CHAMPION_ALPHA / max(challengers, 1), GATE_POCOCK_ALPHA)


def _결정근거(rows: list[dict[str, Any]], mirror: str, evidence: dict[str, dict[str, Any]], challengers: int) -> None:
    """챔피언 후보 줄마다 결정 단위 짝 차이(`evidence_diff`)를 붙이고 `candidate` 를 그것까지로 좁힌다.
    `challengers` = 본페로니로 나눌 도전자 수(줄 수가 아니라 자격 있는 계좌 수, 25.1915), 유의수준은 `champion_alpha`"""
    from batch.services.paper import block_bound

    챔 = (evidence.get(mirror) or {}).get("excess_blocks") or {}
    n = max(challengers, len(rows), 1)
    alpha = champion_alpha(n)
    for r in rows:
        내 = (evidence.get(r["id"]) or {}).get("excess_blocks") or {}
        차 = {int(k): float(내[k]) - float(챔[k]) for k in 내 if k in 챔}
        b = block_bound(차, alpha) if len(차) >= CHAMPION_EVIDENCE_MIN_BLOCKS else {"n_blocks": len(차)}
        r["evidence_diff"] = {"n_blocks": b.get("n_blocks"), "mean": b.get("mean"), "lower": b.get("lower"),
                              "alpha": alpha}  # fmt: skip
        r["candidate"] = bool(r["blocks_ok"] and b.get("lower") is not None and float(b["lower"]) > 0)


#: KIS 단계가 주문을 안 보낸 까닭 가운데 정상인 것 — 확인할 일에 넣지 않는다
SEND_OK = ("오늘은 장이 없다",)


#: 확인할 일에 싣는 괴리 글의 상한 — `broker_mirror.diverge` 의 네 칸 글(약 400자)이 다 들어가는 값.
#: 꼴이 깨진 값만 막는다
DIVERGE_TEXT_MAX = 800


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
        # KIS 가 베끼는 계좌의 계좌 단계 실패면 그 계좌 줄을 아래에서 겹쳐 내지 않는다 — 대신 주문을 어디까지 적었나를
        # 여기 붙인다(가상 장부엔 있는데 KIS 엔 안 베낀 주문 — 다음 날 괴리의 까닭, 25.1926)
        적은곳 = ""
        for aid, a in (p.get("accounts") or {}).items():
            if isinstance(a, dict) and str(p["error"]).startswith(f"가상 {aid} 계좌 단계 실패"):
                적은곳 = _step_error_orders(a)
        out.append({"level": "high",
                    "text": f"가상모의투자 단계 실패: {str(p['error'])[:120]}" + (f" — {적은곳}" if 적은곳 else "")
                    + " — 그날 KIS 베끼기도 하지 않았습니다"})
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
        # 괴리 글은 배치가 만든 정해진 꼴(칸 넷이 다 켜져도 약 400자)이라 다 싣는다 — 예전 300자 자르기는 네 칸이
        # 켜진 날 매도 가격 칸을 말 중간에서 잘랐다(25.1641 교차검증 [권고], 25.1649).
        # 꼴이 깨진 긴 값만 막고 자른 표시를 붙인다
        글 = str(b["diverge"])
        out.append({"level": "high", "text": 글 if len(글) <= DIVERGE_TEXT_MAX else 글[: DIVERGE_TEXT_MAX - 1] + "…"})
    if b.get("diverge_error"):
        out.append({"level": "normal", "text": f"KIS 괴리 판정 실패({b['diverge_error']}) — 주문은 그대로 나갔습니다"})
    # 비교 표본을 가르는 곁다리 실패(25.1815, 25.1812 교차검증 [권고]) — 실행 기록에만 있어, 그날 괴리 알림이 시세·환율
    # 구멍으로 미룬 매도 탓인지·체결 칸이 왜 안 바뀌었는지 알 길이 없었다
    if b.get("defer_mark_error"):
        out.append({"level": "normal", "text": f"KIS 비교 표본 표시 실패({b['defer_mark_error']}) — 오늘 일치율·괴리에 "
                                               "시세·환율이 없어 미룬 매도가 섞였을 수 있습니다"})
    if b.get("cells_error"):
        out.append({"level": "normal",
                    "text": f"KIS 체결 칸 계산 실패({b['cells_error']}) — 오늘 체결 칸이 비거나 갱신되지 않았습니다"})
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
        틀림 = ", ".join(_이름(a) for a in sorted(recon_bad))
        out.append({"level": "high", "text": f"장부 검산 어긋남: 계좌 {틀림}"})
    # 계좌 하나만 그날 못 산 것 — 위원회·지주사 실패, 헌장 오류 (25.1503, 검토 6)
    for aid, a in sorted((p.get("accounts") or {}).items()):
        if not isinstance(a, dict):
            continue
        nm = _이름(aid)
        for 칸, 이름 in (("committee", "투자위원회를 열지 못함"), ("holding", "고르지 못함")):
            if isinstance(a.get(칸), dict) and a[칸].get("error"):
                글 = f"가상 {nm} {이름} — 오늘 사지 않았습니다: {str(a[칸]['error'])[:100]}"
                out.append({"level": "high", "text": 글})
        if a.get("charter_errors"):
            out.append({"level": "high", "text": f"가상 {nm} 헌장 오류: {'; '.join(a['charter_errors'])[:120]}"})
        if a.get("error"):  # 모르는 매도 규칙으로 그 계좌를 건너뛴 날(25.1871 막이) — 텔레그램 줄에만 있었다(25.1873)
            out.append({"level": "high", "text": f"가상 {nm} {str(a['error'])[:100]} — 오늘 평가·매매 안 함"})
        if a.get("substitute_overlap_hit"):
            # 무작위 대조의 옛 자리 신호가 다시 나와 뽑기가 운영·문서 규칙과 갈렸을 수 있는 날(25.1929 찾은 것,
            # 25.1931) — R 판 올림을 앞당길지 볼 자리. 장부 상태(`substitute_overlap`)만으로는 내지 않는다
            # (해 없는 날도 켜진다)
            # 내부 번호를 싣지 않는다 — 사람이 못 읽는다(25.1932). 자리·종목은 실행 기록에 있다
            n = sum(len(v) for v in (a["substitute_overlap_hit"] or {}).values())
            # 25.1933 — "무작위" 가 세 번 겹치던 글을 줄임
            글 = f"가상 {nm} 대체 종목의 옛 자리 신호가 다시 나옴({n}건) — 규칙 판 올림 앞당김 검토"
            out.append({"level": "normal", "text": 글})
        if a.get("step_error") and not str(p.get("error") or "").startswith(f"가상 {aid} 계좌 단계 실패"):
            # 계좌 단계에서 가둔 예외(25.1920·25.1922) — 다른 계좌는 돌았다. 주문을 적은 뒤에 났을 수 있다.
            # KIS 가 베끼는 계좌면 위 단계 실패 줄이 같은 사건을 이미 말한다(25.1924)
            적음 = _step_error_orders(a)
            # "… 이미 냄 멈춤" 처럼 깨지지 않게 글을 그대로(25.1926)
            뒤 = 적음 or "오늘 이 계좌의 평가·매매가 끝나지 않았을 수 있음"
            out.append({"level": "high", "text": f"가상 {nm} 계좌 단계 실패({str(a['step_error'])[:100]}) — {뒤}"})
    if p.get("frozen"):
        # `frozen` 은 경계 멈춤(모든 계좌) + 이어 멈춤(경계 전부터 든 계좌만, `action_sticky`)의 합이다 —
        # 합친 수를 "그 종목은 사고팔지 않습니다" 로 쓰면 이어 멈춤 종목을 다른 계좌가 사고팔 수 있다는 것을
        # 가렸다(25.1631 감사 [권고], 25.1653)
        이어 = set(p.get("action_sticky") or [])
        경계 = [s for s in p["frozen"] if s not in 이어]
        조각 = ([f"기업행위 경계로 멈춘 종목 {len(경계)}개 — 모든 계좌가 사고팔지 않습니다"] if 경계 else []) + (
            [f"경계 전부터 든 계좌만 이어 멈춘 종목 {len(이어)}개"] if 이어 else [])  # fmt: skip
        out.append({"level": "normal", "text": " · ".join(조각)})
    if warnings:
        # `kind` — 텔레그램 리포트는 경고를 본문에 그대로 싣으니 운용 보고에선 이 줄을 뺀다(25.1738)
        out.append({"level": "normal", "text": f"이 실행의 경고 {warnings}줄 — 시스템 상태 화면에서",
                    "kind": "warnings"})
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
    # 항목마다 줄을 나누고 들여 쓴다(25.1721, 2026-10-22 사용자 지시 "텔레그램 리포트가 너무 가독성이 떨어진다").
    # 예전엔 순위·체결·주문이 한 줄에 " · " 로 이어져 폰 화면에서 한 문단이 됐고, 미국 정식 이름("…, Inc. Common
    # Stock")과 회차마다 따로 적힌 같은 종목 주문이 줄을 채웠다. 종목은 짧은 이름, 같은 종목·방향 주문은 한 줄로 묶는다
    out = ["📊 운용 보고 — 가상모의투자·KIS모의투자 (자세히: 웹 운용 화면)"]
    # 경고 개수 줄은 뺀다 — 이 리포트가 경고를 본문에 그대로 싣는다(25.1738, 웹 운용 화면에는 남는다)
    확인 = [c for c in section.get("checks") or [] if c.get("kind") != "warnings"]
    if 확인:
        out.append("▸ 확인할 일")
        out += ["  " + ("❗" if c["level"] == "high" else "") + c["text"] for c in 확인[:REPORT_MAX_CHECKS]]
        if len(확인) > REPORT_MAX_CHECKS:
            out.append(f"  … 외 {len(확인) - REPORT_MAX_CHECKS}건")
    else:
        out.append("▸ 확인할 일: 없음")
    모든 = list(board.get("rows") or [])
    줄 = [r for r in 모든 if not r.get("young") and not r.get("separate")]  # 공통 구간 순위 — S 는 따로(25.1873)

    def 칸(r: dict[str, Any]) -> str:
        v = r.get("vs_r_pp")
        return (f"{r['window_pct']:+.2f}%" + (f" (R 대비 {v:+.2f}%p)" if v is not None else "")
                + (" — 갓 엶, 개설 후" if r.get("young") else ""))  # fmt: skip

    위 = [r for r in 줄 if r.get("window_pct") is not None][:REPORT_TOP_ACCOUNTS]
    # 챔피언은 갓 연 계좌여도 빠뜨리지 않는다 — KIS 가 베끼는 계좌가 순위 줄에서 사라지면 안 된다(25.1512 검토 5)
    챔 = next((r for r in 모든 if r["id"] == mirror and r.get("window_pct") is not None), None)
    if 위 or 챔:
        out.append(f"▸ 순위 (공통 구간 {board.get('window_start')}부터)")
        out += [f"  {k + 1}. {_이름(r['id'])} {칸(r)}" for k, r in enumerate(위)]
        if 챔 and 챔 not in 위:
            # 위 계좌가 없으면 "…" 를 붙이지 않는다(25.1723 — 빠진 계좌가 있는 것처럼 읽혔다)
            out.append(f"  {'… ' if 위 else ''}{_이름(mirror)}(KIS) {칸(챔)}")
    체결 = section.get("fills_h") or []
    out.append(f"▸ {_이름(mirror)} 체결 (판정 {section.get('trade_date')})" + ("" if 체결 else ": 없음"))
    out += [f"  {_SIDE.get(f['side'], f['side'])} {formatter.short_name(f['name'], f.get('ticker'), f['currency'])}"
            f" {int(f['quantity']):,}주 @ {_돈(f['price'], f['currency'])}" for f in 체결]  # fmt: skip
    주문 = section.get("orders_h") or []
    # 체결 장은 주문의 것 — 장 끝난 뒤 손 실행이면 다음다음 장이다(25.1512 검토 4)
    장들 = sorted({str(o.get("fill_session")) for o in 주문 if o.get("fill_session")})
    장 = 장들[0] if len(장들) == 1 else section.get("session_date")
    묶음 = order_groups(주문)
    out.append(f"▸ {_이름(mirror)} 다음 장 주문 ({장})" + ("" if 묶음 else ": 없음"))
    out += ["  " + g for g in 묶음[:REPORT_MAX_ORDERS]]
    if len(묶음) > REPORT_MAX_ORDERS:
        out.append(f"  … 외 {len(묶음) - REPORT_MAX_ORDERS}종목")  # 종목·방향 묶음 수

    # 다른 계좌의 체결·주문 건수 줄은 뺐다(25.1738) — 아래 가상모의투자 절이 계좌마다 "체결 n건" 을 싣고, 다음 장 주문
    # 건수는 판단에 쓰이지 않았다(실물로 찍어 본 리포트 검토). 웹 운용 화면에는 그대로 있다
    if kis_state is not None:
        out.append(f"▸ KIS모의투자: {kis_state}")
        if prev_kis is not None:
            셈: dict[str, int] = {}
            for o in prev_kis:
                셈[str(o.get("status"))] = 셈.get(str(o.get("status")), 0) + 1
            말 = {"accepted": "접수", "rejected": "거부", "sending": "응답 모름", "unknown": "모름"}
            지난 = " · ".join(f"{말.get(k, k)} {n}" for k, n in sorted(셈.items())) if 셈 else "낸 주문 없음"
            out.append(f"  지난 실행 보낸 결과: {지난} (체결은 다음 실행이 맞춤)")
    return out


def order_groups(orders: list[dict[str, Any]]) -> list[str]:
    """주문을 종목·방향마다 한 줄로 — 분할 지정가 회차가 셋이면 "3회차 412주 $88.47~$93.12" (25.1721).

    순서는 처음 나온 차례 그대로. 시장가가 섞이면 "시장가" 를 붙인다
    """
    묶: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for o in orders:
        묶.setdefault((str(o.get("side")), str(o.get("ticker") or o.get("name"))), []).append(o)
    out: list[str] = []
    for (side, _k), os_ in 묶.items():
        o0 = os_[0]
        수량 = sum(int(o.get("quantity") or 0) for o in os_)
        값 = sorted(float(o["limit_price"]) for o in os_
                   if o.get("limit_price") is not None and o.get("order_type") != "market")  # fmt: skip
        시장가 = [o for o in os_ if o.get("order_type") == "market" or o.get("limit_price") is None]
        가격 = []
        if 값:
            가격.append(_돈(값[0], o0["currency"]) if 값[0] == 값[-1]
                       else f"{_돈(값[0], o0['currency'])}~{_돈(값[-1], o0['currency'])}")  # fmt: skip
        if 시장가:
            # 지정가와 섞이면 시장가 몫 수량을 밝힌다(25.1723)
            가격.append("시장가" if len(시장가) == len(os_)
                       else f"시장가 {sum(int(o.get('quantity') or 0) for o in 시장가):,}주")  # fmt: skip
        회차 = f"{len(os_)}회차 " if len(os_) > 1 else ""
        out.append(f"{_SIDE.get(side, side)} {formatter.short_name(o0.get('name'), o0.get('ticker'), o0['currency'])}"
                   f" {회차}{수량:,}주 {' + '.join(가격)}".rstrip())  # fmt: skip
    return out



# ── 실전 준비도 한 장 (주간 요약, 25.1735 — 2026-10-23 사용자 "그렇게 진행해줘", 아이디어 ④) ──────────

#: 관문 항목의 짧은 이름 — 전체 이름(`gate` 의 label)은 웹 운용 화면에 있다
_준비_이름 = {
    "days": "모의 기간", "vs_r": "R(무작위) 대비", "vs_index": "지수 대비", "ratio": "변동성 대비 수익",
    "incidents": "운영 무사고", "kis": "KIS 실현율", "selection": "선정 실력(분위 하한)",
    "control": "가상 대조(분위 하한)",
}
_준비_상태 = {"done": "갖춤", "partial": "일부", "rehearsal": "연습 중", "todo": "아직", "user": "사용자 결정"}


def _준비_값(i: dict[str, Any]) -> str:
    """관문 항목 하나의 "지금 / 목표 (남은 거리)" 글 — 모르면 "아직 모름" """
    k, now, t = i.get("key"), i.get("now"), i.get("target")
    if now is None:
        return "아직 모름"
    if k == "days":
        남음 = max(0, int(t) - int(now))
        return f"{int(now)} / {int(t)}회" + (f" ({남음}회 남음)" if 남음 else "")
    if k in ("vs_r", "vs_index"):
        글 = f"{float(now):+.2f}%p (목표 > 0)"
        if (i.get("valuation_gap") or {}).get("over"):
            글 += " — 원가로 멈춘 평가가 많아 판정 보류"
        return 글
    if k == "ratio":
        return f"{float(now):.2f} (대조 {float(t):.2f} 보다 커야)" if t is not None else f"{float(now):.2f}"
    if k == "incidents":
        return f"사고 {int(now)}일 (최근 {GATE_INCIDENT_DAYS}일, 목표 0)"
    if k == "kis":
        return f"{float(now):.0f}% / {float(t):.0f}% (표본 {int(i.get('n') or 0)}/{GATE_KEPT_MIN_N})"
    if k in ("selection", "control"):
        return f"{float(now):.0f} (50 넘어야, 28일 묶음 {int(i.get('n') or 0)}/{GATE_SEL_MIN_BLOCKS})"
    return str(now)


def gate_looks_line(gate: dict[str, Any] | None) -> str | None:
    """관문 판정 시점 한 줄(25.1963) — "…: 126 못 넘음 · 다음 189평가". 판정 시점 기록이 없는 옛 관문은 None.
    웹 `gateLooksText` 와 같은 글"""
    if not gate or "looks" not in gate:
        return None
    looks = gate.get("looks") or []
    칸 = [f"{x.get('n')} {'통과' if x.get('passed') else '못 넘음'}" for x in looks]
    다음 = gate.get("next_look")
    끝 = "판정 시점 끝" + ("" if gate.get("passed") else " — 새 판 등록 전엔 통과 없음")
    꼬리 = f"다음 {다음}평가" if 다음 else 끝
    return "판정 시점(126·189·252평가에만 판정): " + (" · ".join(칸) + " · " if 칸 else "") + 꼬리


#: 관문을 비운 갈래(판오류·앞오류)에 관문 계산 실패가 겹친 날 `gate_error` 에 잇는 구분 — 뒤에 계산 까닭 전문
#: (`jobs/desk._까닭_잇기`, 25.1977). 웹 `lib/desk.ts` 의 같은 이름과 같아야 한다(시험이 대조, 25.1978)
GATE_CALC_ERROR_SEP = " · 관문 계산 실패 — "


def readiness_lines(gate: dict[str, Any] | None, ready: list[dict[str, Any]] | None, gate_error: str | None = None
                    ) -> list[str]:  # fmt: skip
    """주간 요약의 **실전 준비도** 한 장 — 1단계 관문 항목마다 지금 값·목표·남은 거리, 실전 준비 목록 상태 수,
    다음 단계.

    값은 운용 요약(`paper_desk`)이 날마다 적은 그대로 — 새로 셈하지 않는다. 관문·준비 목록이 웹 운용 화면에 흩어져 "언제
    실전으로 갈 수 있나" 를 한눈에 보기 어려웠다(아이디어 ④)
    """
    if not gate:
        # 갈래 글을 80자로 자른다 — 관문 계산 실패가 겹친 날은 잘릴 계산 까닭 대신 한 마디를 붙인다(25.1978)
        글 = (gate_error or "").split(GATE_CALC_ERROR_SEP)[0][:80]
        겹침 = " · 관문 계산도 실패" if gate_error and GATE_CALC_ERROR_SEP in gate_error else ""
        return ["🧭 실전 준비도 — 관문을 세지 못했습니다" + (f" ({글}{겹침})" if gate_error else "")]
    items = list(gate.get("items") or [])
    충족 = sum(1 for i in items if i.get("ok"))
    판 = gate.get("version")
    # 통과는 판정 시점의 판정(`passed`)으로만 — 칸 수가 다 차도 판정 시점이 아니면 통과가 아니다(25.1968, 25.1963
    # 교차검증 [권고] — 예전 "통과 9/9" 는 칸 수였다). 웹 상황실 관문 카드와 같은 말
    상태 = "통과(다음 단계는 사용자 결정)" if gate.get("passed") else f"{충족}/{len(items)} 충족"
    머리 = (f"🧭 실전 준비도 — 1단계 관문(모의 성적·무사고) {상태}"
          f" · {_이름(gate.get('account'))}{'' if 판 is None else f' 판 {판}'}"
          + (f", {gate.get('from')}부터" if gate.get("from") else ""))  # fmt: skip
    out = [머리]
    for i in items:
        이름 = _준비_이름.get(str(i.get("key")), str(i.get("label")))
        out.append(f"  {'✅' if i.get('ok') else '⬜'} {이름}: {_준비_값(i)}")
    판정줄 = gate_looks_line(gate)
    if 판정줄:
        out.append(f"  {판정줄}")
    if ready:
        셈: dict[str, int] = {}
        for r in ready:
            셈[str(r.get("status"))] = 셈.get(str(r.get("status")), 0) + 1
        out.append("  준비 목록 " + " · ".join(f"{말} {셈[k]}" for k, 말 in _준비_상태.items() if 셈.get(k)))
    if gate.get("next"):
        out.append(f"  그다음: {gate['next']}")
    return out
