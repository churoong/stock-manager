"""종목 분석의 해석 묶음 — 역DCF·수급 흐름·점수 변화·닮은 종목·모델 합의
(docs/analysis.md 15~19장, docs/infra.md 25.1041).

모두 DB 에 이미 있는 값을 다르게 읽은 것이다. 새 문턱이 없다 — 부호·크기·순서를 사실로 말한다(docs/analysis.md 2장).
계산만 한다 — DB 도 시각도 모른다.
"""

from __future__ import annotations

import math
from typing import Any

FACTORS = ("value", "quality", "growth", "momentum", "risk")
#: 수급 흐름의 두 창 (거래일 행 수) — 한 달·석 달
FLOW_WINDOWS = (20, 60)
#: 점수 변화를 견주는 거리 (달력일). 4주 ≈ 20거래일
SCORE_CHANGE_DAYS = 28
#: 닮은 종목 수
TWINS = 3


def reverse_dcf(*, ep: float | None, discount: float | None) -> dict | None:
    """역DCF — 지금 가격에 담긴 **영구 이익 성장률** (docs/analysis.md 15장).

    고든 성장 모형 P = E·(1+g)/(r−g) 를 g 로 푼다: g = (r − E/P)/(1 + E/P).
    이익 전부를 주주 몫(현금흐름)으로 보는 단순화다.
    r = CAPM 기대수익(10.1). 적자(E/P ≤ 0)면 풀 수 없다 — None."""
    if not isinstance(ep, (int, float)) or ep <= 0 or not isinstance(discount, (int, float)):
        return None
    return {"implied_growth": (discount - ep) / (1 + ep), "ep": ep, "discount": discount, "per": 1 / ep}


def flow_card(rows: list[dict]) -> dict | None:
    """국내 수급 흐름 (docs/analysis.md 16장). rows 는 한 종목의 `kr_flows` 행, **날짜 내림차순**.

    창마다 외국인·기관·개인 순매수 합(억원), 외국인 연속 순매수(+)/순매도(−) 일수, 공매도 비중 최근 20행 평균과 그 앞
    40행 평균, 신용 잔고율 최신과 20행 전."""
    if not rows:
        return None
    out: dict[str, Any] = {"latest": rows[0].get("date"), "windows": {}}
    # 투자자별 금액이 있는 행만 센다 — 공매도만 있는 행을 "순매수 0" 으로 세지 않는다 (25.1042, 교차검증 감사)
    매매 = [r for r in rows if r.get("frgn_net_amt") is not None]
    for w in FLOW_WINDOWS:
        창 = 매매[:w]
        if len(창) < w // 2:
            continue
        합 = {k: sum(int(r.get(f"{k}_net_amt") or 0) for r in 창) / 100 for k in ("frgn", "orgn", "prsn")}
        out["windows"][str(w)] = {"days": len(창), **{k: round(v, 1) for k, v in 합.items()}}
    연속 = 0
    for r in 매매:
        x = r.get("frgn_net_amt")
        if x is None or x == 0:
            break
        if 연속 == 0 or (연속 > 0) == (x > 0):
            연속 += 1 if x > 0 else -1
        else:
            break
    out["frgn_streak"] = 연속
    공 = [float(r["short_vol_pct"]) for r in rows[:60] if isinstance(r.get("short_vol_pct"), (int, float))]
    if len(공) >= 30:
        out["short"] = {"recent": sum(공[:20]) / 20, "before": sum(공[20:]) / len(공[20:])}
    신 = [r.get("credit_rmnd_pct") for r in rows]
    if len(신) > 20 and isinstance(신[0], (int, float)) and isinstance(신[20], (int, float)):
        out["credit"] = {"now": float(신[0]), "before": float(신[20])}
    return out


def flow_lines(card: dict | None) -> list[str]:
    """수급 흐름을 문장으로 — 부호와 크기만 말한다(문턱 없음)."""
    if not card or not card.get("windows"):
        return []
    줄 = []
    # 창 이름이 아니라 **실제로 센 거래일**로 적는다 — 수집 첫 두 달은 60거래일 창이 31~59일뿐이다
    # (25.1042, 교차검증 감사)
    for x in card["windows"].values():
        줄.append(f"{x['days']}거래일 순매수: 외국인 {x['frgn']:+,.1f}억 · 기관 {x['orgn']:+,.1f}억"
                  f" · 개인 {x['prsn']:+,.1f}억")
    x20 = card["windows"].get("20")
    if x20 and x20["prsn"] > 0 and x20["frgn"] < 0 and x20["orgn"] < 0:
        줄.append(f"{x20['days']}거래일 동안 개인만 순매수 — 외국인·기관은 순매도")
    s = card.get("frgn_streak") or 0
    if abs(s) >= 2:
        줄.append(f"외국인 {abs(s)}거래일 연속 순{'매수' if s > 0 else '매도'}")
    if card.get("short"):
        줄.append(f"공매도 비중 최근 20일 평균 {card['short']['recent']:.1f}% (그 앞 {card['short']['before']:.1f}%)")
    if card.get("credit"):
        줄.append(f"신용 잔고율 {card['credit']['now']:.2f}% (20거래일 전 {card['credit']['before']:.2f}%)")
    return 줄


def score_change(now: dict | None, before: dict | None) -> dict | None:
    """점수 변화 (docs/analysis.md 17장). now·before = {"as_of", "total", "factors"}.
    가장 많이 오른·내린 팩터를 함께."""
    if not now or not before or not isinstance(now.get("total"), (int, float)) or not isinstance(
            before.get("total"), (int, float)):  # fmt: skip
        return None
    차 = {}
    for k in FACTORS:
        a, b = (now.get("factors") or {}).get(k), (before.get("factors") or {}).get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            차[k] = a - b
    out: dict[str, Any] = {"since": before.get("as_of"), "delta": now["total"] - before["total"], "factors": 차}
    if 차:
        out["up"] = max(차, key=차.get)  # type: ignore[arg-type]
        out["down"] = min(차, key=차.get)  # type: ignore[arg-type]
    return out


def twins(target: int, profiles: dict[int, dict[str, float]], k: int = TWINS) -> list[tuple[int, float]]:
    """팩터 다섯 점수의 모양이 가장 닮은 종목 (docs/analysis.md 18장). 유클리드 거리 오름차순 (종목, 거리).

    다섯이 모두 있는 종목끼리만 견준다. 같은 시장(나라) 안에서만 —
    점수는 시장별 z-score 라 나라를 건너 견주지 않는다."""
    me = profiles.get(target)
    if not me:
        return []
    거리 = []
    for sid, p in profiles.items():
        if sid != target:
            거리.append((sid, math.sqrt(sum((me[f] - p[f]) ** 2 for f in FACTORS))))
    거리.sort(key=lambda x: (x[1], x[0]))
    return [(sid, round(d, 1)) for sid, d in 거리[:k]]


def profile(factors: dict | None) -> dict[str, float] | None:
    """팩터 다섯이 모두 있으면 그 점수, 아니면 None."""
    f = factors or {}
    if all(isinstance(f.get(k), (int, float)) for k in FACTORS):
        return {k: float(f[k]) for k in FACTORS}
    return None


def agreement(outlook: dict | None) -> dict | None:
    """1년 예상의 네 눈 (docs/analysis.md 19장) — CAPM 기대·비슷한 국면 중앙값·시나리오 기본·증권사 목표가.
    수익률로 나란히.
    둘 이상 있어야 낸다. 오름을 말하는 눈의 수와 가장 높은·낮은 눈의 차."""
    o = outlook or {}
    close = o.get("close")
    if not isinstance(close, (int, float)) or close <= 0:
        return None
    눈: dict[str, float] = {}
    일년 = next((h for h in (o.get("forecast") or {}).get("horizons") or [] if h.get("months") == 12), None)
    if 일년:
        눈["capm"] = 일년["expected"] / close - 1
    a = next((h for h in (o.get("analog") or {}).get("horizons") or [] if h.get("months") == 12), None)
    if a:
        눈["analog"] = a["median"]
    s = o.get("scenario") or {}
    if isinstance(s.get("base"), (int, float)):
        눈["scenario"] = s["base"] / close - 1
    c = o.get("consensus") or {}
    if isinstance(c.get("median"), (int, float)):
        눈["consensus"] = c["median"] / close - 1
    if len(눈) < 2:
        return None
    return {"views": {k: round(v, 4) for k, v in 눈.items()}, "up": sum(1 for v in 눈.values() if v > 0),
            "n": len(눈), "spread": round(max(눈.values()) - min(눈.values()), 4)}  # fmt: skip


#: 레이더 목록마다의 길이 (docs/analysis.md 20장)
RADAR_N = 10


def radar(entries: list[dict]) -> dict:
    """종목 분석 탭 첫 화면의 레이더 (docs/analysis.md 20장, 25.1043) — 시장 전체에서 세 목록.

    entries: {stock_id, ticker, name, verdict, ladder, score_change, agreement}
      near    아직 충족하지 않은 **판정표 가격 기준**까지 가장 가까운 종목(거리 절댓값 오름차순)
              — "어느 가격이면 신호 기준이 바뀌나"
      rising  4주 전보다 종합 점수가 가장 많이 오른 종목
      eyes    1년 예상의 눈이 셋 이상이고 **모두** 오름을 말하는 종목(가장 낮은 눈이 높은 순)
    새 문턱이 없다 — 순서만 정한다."""
    near, rising, eyes = [], [], []
    for e in entries:
        머리 = {"stock_id": e["stock_id"], "ticker": e["ticker"], "name": e["name"], "verdict": e.get("verdict")}
        기준 = [it for it in (e.get("ladder") or {}).get("items") or []
                if it.get("kind") == "criterion" and not it.get("met")]  # fmt: skip
        if 기준:
            가까운 = min(기준, key=lambda it: abs(it["dist"]))
            near.append({**머리, "label": 가까운["label"], "price": 가까운["price"], "dist": round(가까운["dist"], 4),
                         "need": 가까운.get("need")})  # fmt: skip
        sc = e.get("score_change") or {}
        if isinstance(sc.get("delta"), (int, float)) and sc["delta"] > 0:
            rising.append({**머리, "delta": round(sc["delta"], 1), "up": sc.get("up"), "since": sc.get("since")})
        a = e.get("agreement") or {}
        if a.get("n", 0) >= 3 and a.get("up") == a.get("n"):
            eyes.append({**머리, "low": min(a["views"].values()), "high": max(a["views"].values()), "n": a["n"]})
    near.sort(key=lambda x: (abs(x["dist"]), x["stock_id"]))
    rising.sort(key=lambda x: (-x["delta"], x["stock_id"]))
    eyes.sort(key=lambda x: (-x["low"], x["stock_id"]))
    return {"near": near[:RADAR_N], "rising": rising[:RADAR_N], "eyes": eyes[:RADAR_N]}


#: 업종 비교에서 이름을 보이는 상위 종목 수 (docs/analysis.md 21장)
PEERS_TOP = 3


def _median(v: list[float]) -> float | None:
    if not v:
        return None
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def peers(sid: int, members: list[dict]) -> dict | None:
    """같은 업종 비교 (docs/analysis.md 21장, 25.1045). members = 같은 시장·같은 업종 종목들
    [{stock_id, ticker, name, total, pbr, roe, r3}] (자기 포함). 둘 이상일 때만.

    점수 순위(종합 점수 높은 순), PBR·ROE·3개월 수익률의 업종 중앙값과 이 종목 값, 점수 상위 `PEERS_TOP` 종목.
    문턱 없음."""
    me = next((m for m in members if m["stock_id"] == sid), None)
    if not me or len(members) < 2:
        return None
    점수순 = sorted((m for m in members if isinstance(m.get("total"), (int, float))),
                 key=lambda m: (-m["total"], m["stock_id"]))  # fmt: skip
    out: dict[str, Any] = {"n": len(members), "ranked": len(점수순)}
    out["rank"] = next((i + 1 for i, m in enumerate(점수순) if m["stock_id"] == sid), None)
    for k in ("pbr", "roe", "r3"):
        out[k] = me.get(k) if isinstance(me.get(k), (int, float)) else None
        out[f"{k}_median"] = _median([m[k] for m in members if isinstance(m.get(k), (int, float))])
    out["top"] = [{"stock_id": m["stock_id"], "ticker": m["ticker"], "name": m["name"], "total": m["total"]}
                  for m in 점수순 if m["stock_id"] != sid][:PEERS_TOP]  # fmt: skip
    return out
