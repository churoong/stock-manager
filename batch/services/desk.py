"""운용 상황실 요약 — 순수 함수 (docs/desk.md 2장, docs/infra.md 25.1501).

웹 첫 화면(운용)이 읽는 숫자는 여기서 낸다 — 웹은 계산하지 않는다. 계좌 순위는 **공통 구간**(기록이 있는 계좌들의
첫 평가일 가운데 가장 늦은 날부터) 시간가중 수익률로 — 늦게 연 계좌(G·H)와 일찍 연 계좌를 같은 잣대로 견준다.
R 대비 = 공통 구간 수익률 − 무작위 대조 R1·R2 평균(%p): 무작위로 골라도 이만큼은 났다는 기준선
"""

from __future__ import annotations

from typing import Any

#: 계좌 종류 — 화면 글. 모르는 계좌는 이름만 (docs/paper.md 1장, docs/company.md)
KIND = {"A": "회사", "C": "회사", "D": "회사", "E": "회사", "B": "기준(점수 상위)", "G": "추세 익절",
        "H": "지주사(KIS가 베낌)", "R1": "무작위 대조", "R2": "무작위 대조"}  # fmt: skip
CONTROL_IDS = ("R1", "R2")
#: 확인할 일 하나에 싣는 경고 줄 최대 — 화면을 덮지 않게
MAX_WARNINGS = 8


def leaderboard(series: dict[str, list[tuple[str, float]]], names: dict[str, str] | None = None) -> dict[str, Any]:
    """{window_start, control_pct, rows: [{id, name, kind, open, last, since_open_pct, window_pct, vs_r_pp}]}.

    공통 구간 수익률 큰 순

    `series` = {계좌: [(as_of, twr_index)] 날짜순}. 기록이 없는 계좌는 뺀다. R 이 없으면 vs_r_pp 는 None
    """
    살 = {a: sorted(v) for a, v in series.items() if v}
    if not 살:
        return {"window_start": None, "rows": []}
    시작 = max(v[0][0] for v in 살.values())

    def 구간(v: list[tuple[str, float]]) -> float | None:
        앞 = [t for d, t in v if d <= 시작]
        기준 = 앞[-1] if 앞 else v[0][1]
        return (v[-1][1] / 기준 - 1) * 100 if 기준 else None

    줄 = []
    for a, v in 살.items():
        줄.append({"id": a, "name": (names or {}).get(a) or a, "kind": KIND.get(a, ""), "open": v[0][0],
                  "last": v[-1][0], "since_open_pct": (v[-1][1] - 1) * 100, "window_pct": 구간(v)})  # fmt: skip
    대조 = [r["window_pct"] for r in 줄 if r["id"] in CONTROL_IDS and r["window_pct"] is not None]
    기준선 = sum(대조) / len(대조) if 대조 else None
    for r in 줄:
        빼기 = 기준선 is None or r["window_pct"] is None or r["id"] in CONTROL_IDS
        r["vs_r_pp"] = None if 빼기 else r["window_pct"] - 기준선
    줄.sort(key=lambda r: (r["window_pct"] is None, -(r["window_pct"] or 0.0), r["id"]))
    return {"window_start": 시작, "control_pct": 기준선, "rows": 줄}


def _구간(v: list[tuple[str, float]], start: str, end: str | None) -> float | None:
    """`start` 이하 마지막 값 대비 `end` 이하 마지막 값(없으면 마지막) 수익률(%). 그 전 기록이 없으면 첫 값부터"""
    앞 = [t for d, t in v if d <= start]
    기준 = 앞[-1] if 앞 else (v[0][1] if v else None)
    뒤 = [t for d, t in v if end is None or d <= end]
    return (뒤[-1] / 기준 - 1) * 100 if 기준 and 뒤 else None


def rule_history(
    spans: list[dict[str, Any]], series: dict[str, list[tuple[str, float]]],
) -> dict[str, list[dict[str, Any]]]:
    """판 이력 — {계좌: [{version, from, to, orders, pct, control_pct, vs_r_pp}]} 판 순 (docs/desk.md 3장).

    `spans` = 주문 원장을 (계좌, 판)으로 묶은 것 [{account, version, first, last, orders}].
    한 판의 구간은 그 판 첫 결정일 종가부터 다음 판 첫 결정일 종가까지(지금 판이면 마지막 평가까지) —
    그 판의 주문이 처음 들어간 장부터 다음 판이 바꾸기 전까지의 시간가중 수익률이다.
    같은 구간 무작위 대조 R1·R2 평균과 그 차(%p)를 함께 — 판을 바꾼 뒤 나아졌는지를 운과 가른다
    """
    판들: dict[str, list[dict[str, Any]]] = {}
    for s in spans:
        판들.setdefault(str(s["account"]), []).append(s)
    out: dict[str, list[dict[str, Any]]] = {}
    for a, 줄 in 판들.items():
        줄.sort(key=lambda s: (s["first"], s["version"]))
        out[a] = []
        for i, s in enumerate(줄):
            끝 = 줄[i + 1]["first"] if i + 1 < len(줄) else None
            pct = _구간(sorted(series.get(a) or []), s["first"], 끝)
            대조 = [x for r in CONTROL_IDS if (x := _구간(sorted(series.get(r) or []), s["first"], 끝)) is not None]
            기준선 = sum(대조) / len(대조) if 대조 else None
            out[a].append({"version": s["version"], "from": s["first"], "to": 끝, "orders": s["orders"], "pct": pct,
                           "control_pct": 기준선, "vs_r_pp": None if pct is None or 기준선 is None or a in CONTROL_IDS
                           else pct - 기준선})  # fmt: skip
    return out


def checks(
    *, halted: bool, paper: dict[str, Any] | None, broker: dict[str, Any] | None, recon_bad: list[str],
    warnings: list[str],
) -> list[dict[str, str]]:
    """확인할 일 — [{level: high·normal, text}]. 높음이 먼저."""
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
    if p.get("frozen"):
        out.append({"level": "normal",
                    "text": f"기업행위 경계로 멈춘 종목 {len(p['frozen'])}개 — 그 종목은 사고팔지 않습니다"})
    for w in warnings[:MAX_WARNINGS]:
        out.append({"level": "normal", "text": str(w)[:160]})
    if len(warnings) > MAX_WARNINGS:
        out.append({"level": "normal", "text": f"경고 {len(warnings) - MAX_WARNINGS}줄 더 — 시스템 상태 화면에서"})
    return sorted(out, key=lambda x: x["level"] != "high")


def merge(prev: dict[str, Any] | None, market: str, section: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    """그날 요약에 이 시장 칸을 합친다 — 다른 시장 칸은 남기고, 순위·KIS·확인할 일(`common`)은 이번 것으로 덮는다.

    확인할 일은 시장마다 따로 두고(`markets.X.checks`) 화면이 합쳐 보인다 — 국내 실패가 미국 실행에 덮여 사라지지 않게
    """
    out = dict(prev or {})
    시장들 = dict(out.get("markets") or {})
    시장들[market] = section
    return out | common | {"markets": 시장들}
