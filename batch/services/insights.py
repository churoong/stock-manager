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


#: 목표가 흐름의 중앙값을 찍는 날 — 지금에서 며칠 전 (docs/analysis.md 36장). 일일 의견이 읽는 90일 창 안
TREND_POINTS = (60, 30, 0)


def target_trend(opinions: list[dict], today: Any, window_days: int) -> dict | None:
    """목표가 흐름 (docs/analysis.md 36장, 25.1059) — 최근 `window_days` 일 증권사 목표가.

    - 변경: 같은 증권사의 바로 앞(창 안) 목표가보다 올렸나 내렸나, 평균 변경률(올림·내림만)
    - 중앙값의 길: `TREND_POINTS` 일 전마다, 그날까지 낸 증권사별 마지막 목표가(창 안)의 중앙값
    창 안 의견이 없으면 None."""
    from datetime import date, timedelta

    from batch.services import divergence

    t = today if isinstance(today, date) else date.fromisoformat(str(today)[:10])
    since = (t - timedelta(days=window_days)).isoformat()
    rows = sorted((str(o.get("date") or ""), str(o.get("broker")), float(o["target_price"])) for o in opinions
                  if str(o.get("date") or "") >= since and isinstance(o.get("target_price"), (int, float))
                  and o["target_price"] > 0)  # fmt: skip
    if not rows:
        return None
    앞: dict[str, float] = {}
    변경: list[float] = []
    for _, b, x in rows:
        if b in 앞 and abs(x / 앞[b] - 1) > divergence.TARGET_TOLERANCE:  # 반대 목소리와 같은 허용 폭
            변경.append(x / 앞[b] - 1)
        앞[b] = x
    points = []
    for 전 in TREND_POINTS:
        끝 = (t - timedelta(days=전)).isoformat()
        마지막: dict[str, float] = {}
        for d, b, x in rows:
            if d <= 끝:
                마지막[b] = x
        if 마지막:
            v = sorted(마지막.values())
            n = len(v)
            points.append({"days_ago": 전, "date": 끝, "brokers": n,
                           "median": v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2})  # fmt: skip
    return {"window": window_days, "raises": sum(1 for x in 변경 if x > 0), "cuts": sum(1 for x in 변경 if x < 0),
            "avg_change": round(sum(변경) / len(변경), 4) if 변경 else None, "points": points}  # fmt: skip


#: 이 종목 신호 성적 (docs/analysis.md 37장) — 같은 기간 신호가 이 달력일 넘게 끊겼다가 다시 나야 새 신호로 센다.
#: 신호는 조건이 이어지는 동안 날마다 다시 적혀, 그대로 세면 한 번의 신호가 수십 번으로 부푼다. 한 주(주말 포함 7일)
SIGNAL_GAP_DAYS = 7
#: 화면에 싣는 최근 신호 수
SIGNAL_SHOW = 10


def signal_history(rows: list[dict]) -> dict | None:
    """이 종목 신호 성적 (docs/analysis.md 37장, 25.1060) — `signal_outcomes` 행(날짜 오름차순)을 신호 한 번씩으로 묶어
    그 첫날의 결과만 센다. 요약: 20거래일 뒤 결과가 있는 신호 수·평균·오른 비율·시장 대비 평균, 목표·손절 도달 수."""
    from datetime import date

    앞날: dict[str, str] = {}
    신호: list[dict] = []
    for r in sorted(rows, key=lambda r: (str(r["as_of_date"]), str(r["horizon"]))):
        d, h = str(r["as_of_date"]), str(r["horizon"])
        새 = h not in 앞날 or (date.fromisoformat(d) - date.fromisoformat(앞날[h])).days > SIGNAL_GAP_DAYS
        앞날[h] = d
        if 새:
            신호.append({k: r.get(k) for k in ("as_of_date", "horizon", "ret_5d", "ret_20d", "ret_60d", "hit_target",
                                                "hit_stop", "bench_ret_20d")})  # fmt: skip
    if not 신호:
        return None
    스물 = [x for x in 신호 if isinstance(x.get("ret_20d"), (int, float))]
    초과 = [x["ret_20d"] - x["bench_ret_20d"] for x in 스물 if isinstance(x.get("bench_ret_20d"), (int, float))]
    return {
        "signals": len(신호), "rows": len(rows), "recent": 신호[-SIGNAL_SHOW:][::-1],
        "n20": len(스물), "avg20": round(sum(x["ret_20d"] for x in 스물) / len(스물), 4) if 스물 else None,
        "up20": sum(1 for x in 스물 if x["ret_20d"] > 0),
        "excess20": round(sum(초과) / len(초과), 4) if 초과 else None,
        "targets": sum(1 for x in 신호 if x.get("hit_target")), "stops": sum(1 for x in 신호 if x.get("hit_stop")),
    }  # fmt: skip


#: 재무 건전성 (docs/analysis.md 38장) — Altman Z″ (1995, 비제조·신흥시장용, 시가총액이 필요 없는 판)의 계수와 구간.
#: Z″ = 6.56·운전자본/자산 + 3.26·이익잉여금/자산 + 6.72·영업이익/자산 + 1.05·자본/부채.
#: 구간 경계(안전 > 2.60, 회색 1.10~2.60, 위험 < 1.10)는 Altman(2000, "Predicting Financial Distress of Companies:
#: Revisiting the Z-Score and ZETA Models")이 적은 값을 그대로 옮긴 것 — 우리가 맞춘 문턱이 아니다
Z2_COEF = (6.56, 3.26, 6.72, 1.05)
Z2_SAFE = 2.60
Z2_DISTRESS = 1.10


def health(rows: list[dict], sector_code: str | None) -> dict | None:
    """재무 건전성 (docs/analysis.md 38장, 25.1061) — 가장 최근 사업보고서(11011) 한 행으로 Altman Z″ 와 부채비율.

    기준 하나: 가장 최근 회계연도의 연결(있으면)·별도, 같은 해 여러 보고서면 가장 늦게 접수된 것.
    금융업(은행·보험·증권·부동산 — `sectors.is_financial`)은 부채가 영업 재료라 Z″ 가 뜻이 없어 내지 않고 그렇게 적는다.
    재료가 비면 비었다고 적는다(지어내지 않는다). 사업보고서가 없으면 None."""
    from batch.services import sectors
    from batch.sources import dart

    연간 = [r for r in rows if str(r.get("report_code")) == dart.ANNUAL_REPORT_CODE]
    if not 연간:
        return None
    해 = max(int(r["fiscal_year"]) for r in 연간)
    같은해 = [r for r in 연간 if int(r["fiscal_year"]) == 해]
    연결 = [r for r in 같은해 if int(r.get("consolidated") or 0) == 1]
    r = max(연결 or 같은해, key=lambda x: str(x.get("report_date") or ""))
    out: dict[str, Any] = {"fiscal_year": 해, "consolidated": bool(연결), "report_date": r.get("report_date"),
                           "z": None}  # fmt: skip
    if sectors.is_financial(sector_code):
        out["note"] = "금융업은 부채가 영업 재료라 Altman Z″ 를 내지 않습니다"
        return out
    ca, cl, ta, tl, re_, eq, ebit = (r.get(k) for k in ("current_assets", "current_liabilities", "total_assets",
                                                         "total_liabilities", "retained_earnings", "total_equity",
                                                         "operating_income"))  # fmt: skip
    빈것 = [이름 for 이름, v in (("유동자산", ca), ("유동부채", cl), ("자산총계", ta), ("부채총계", tl),
                              ("이익잉여금", re_), ("자본총계", eq), ("영업이익", ebit)) if v is None]  # fmt: skip
    if isinstance(tl, (int, float)) and isinstance(eq, (int, float)) and eq > 0:
        out["debt_ratio"] = round(tl / eq, 4)
    if 빈것 or not ta or ta <= 0 or not tl or tl <= 0:
        out["note"] = "재료가 비어 Z″ 를 내지 못했습니다: " + (", ".join(빈것) if 빈것 else "자산·부채 0")
        return out
    x = ((ca - cl) / ta, re_ / ta, ebit / ta, eq / tl)
    z = sum(c * v for c, v in zip(Z2_COEF, x, strict=True))
    out.update({"z": round(z, 3), "parts": [round(v, 4) for v in x],
                "zone": "safe" if z > Z2_SAFE else "distress" if z < Z2_DISTRESS else "grey"})  # fmt: skip
    return out


def health_line(h: dict) -> str:
    구간 = {"safe": "안전 구간", "grey": "회색 구간", "distress": "위험 구간"}[h["zone"]]
    부채 = f" · 부채비율 {h['debt_ratio'] * 100:.0f}%" if h.get("debt_ratio") is not None else ""
    기준 = "·연결" if h["consolidated"] else "·별도"
    return (f"재무 건전성({h['fiscal_year']} 사업보고서{기준}): Altman Z″ {h['z']:.2f} "
            f"— {구간}(>{Z2_SAFE:.2f} 안전, <{Z2_DISTRESS:.2f} 위험, Altman 2000){부채}")


#: 성적 가중 합의가 보는 기간 — 네 눈이 모두 1년 예상이라 1년 성적으로만 견준다 (docs/analysis.md 30장)
WEIGHT_MONTHS = 12


def weighted_agreement(agreement: dict | None, market_track: dict | None, since: str | None) -> dict | None:
    """성적 가중 합의 (docs/analysis.md 30장, 25.1056) — 네 눈을 **시장 전체 1년 성적표**의
    평균 로그 오차의 역수로 가중.

    눈마다 1년 성적 칸의 표본이 성적표 최소 표본(`forecast_track.MIN_SAMPLE`) 이상이고
    오차가 0 보다 클 때만 가중에 든다.
    든 눈이 둘 이상이면 가중 평균, 아니면 언제부터 낼 수 있는지(첫 기록일 + 1년)를 말한다. 눈이 없으면 None."""
    from batch.services import forecast_track as ft

    views = (agreement or {}).get("views") or {}
    if len(views) < 2:
        return None
    무게: dict[str, float] = {}
    표본: dict[str, int] = {}
    for model in views:
        r = ft.rates(((market_track or {}).get(model) or {}).get(str(WEIGHT_MONTHS)))
        if r:
            표본[model] = r["n"]
        if r and r["enough"] and r["err"] > 0:
            무게[model] = 1 / r["err"]
    빠짐 = sorted(m for m in views if m not in 무게)
    if len(무게) < 2:
        언제 = None
        if since:
            from datetime import date

            언제 = ft.months_back(date.fromisoformat(str(since)[:10]), -WEIGHT_MONTHS).isoformat()
        return {"value": None, "pending": 빠짐, "available_from": 언제, "n": 표본}
    합 = sum(무게.values())
    return {"value": round(sum(views[m] * w for m, w in 무게.items()) / 합, 4),
            "weights": {m: round(w / 합, 4) for m, w in 무게.items()}, "left_out": 빠짐, "n": 표본}  # fmt: skip


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


#: 같은 업종 대체 후보 수 (docs/analysis.md 47장)
ALT_TOP = 3


def alternatives(sid: int, members: list[dict]) -> list[dict]:
    """같은 업종 대체 후보 (47장, 25.1068) — 같은 시장·같은 업종에서
    **종합 점수가 같거나 높고 연환산 변동성이 낮은** 종목,
    변동성 낮은 순 `ALT_TOP` 개. 새 문턱 없음 — "같거나 높다"·"낮다" 의 비교만. 이 종목 점수·변동성이 없으면 빈 목록."""
    me = next((m for m in members if m["stock_id"] == sid), None)
    if not me or not isinstance(me.get("total"), (int, float)) or not isinstance(me.get("vol"), (int, float)):
        return []
    후보 = [m for m in members if m["stock_id"] != sid and isinstance(m.get("total"), (int, float))
          and isinstance(m.get("vol"), (int, float))
          and m["total"] >= me["total"] and m["vol"] < me["vol"]]  # fmt: skip
    후보.sort(key=lambda m: (m["vol"], -m["total"], m["stock_id"]))
    return [{"stock_id": m["stock_id"], "ticker": m["ticker"], "name": m["name"], "total": m["total"],
             "vol": m["vol"], "mdd": m.get("mdd"), "my_vol": me["vol"], "my_total": me["total"]}
            for m in 후보[:ALT_TOP]]  # fmt: skip


def calibration_for(total: float | None, payloads: list[dict] | None) -> dict | None:
    """같은 점수대의 지난 신호 성적 (docs/analysis.md 22장, 25.1046) — 점수 보정표(docs/signals.md 10.1)에서
    이 종목 종합 점수가 든 10점 구간을 고른다.
    평균을 낸 구간(표본 하한을 넘은 것)이 있는 가장 긴 창 하나. 없으면 None."""
    if not isinstance(total, (int, float)) or not payloads:
        return None
    lo = min(int(total // 10) * 10, 90)
    for p in sorted(payloads, key=lambda x: -int(x.get("window") or 0)):
        b = next((b for b in p.get("buckets") or [] if b.get("lo") == lo and b.get("avg_ret") is not None), None)
        if b:
            return {"window": p["window"], "lo": b["lo"], "hi": b["hi"], "n": b["n"], "avg_ret": b["avg_ret"],
                    "win_rate": b.get("win_rate"), "rho": p.get("rho"), "verdict": p.get("verdict"),
                    "total_n": p.get("n")}  # fmt: skip
    return None


#: 보고서 코드 → 분기 번호 (국내 DART. 11011 사업보고서는 연간이라 쓰지 않는다 — 4분기 단독 값이 없다)
QUARTER_OF = {"11013": 1, "11012": 2, "11014": 3}
#: 실적 추세에서 보이는 최근 분기 수 (docs/analysis.md 25장)
TREND_QUARTERS = 3


def quarter_trend(rows: list[dict]) -> dict | None:
    """국내 분기 실적 추세 (docs/analysis.md 25장, 25.1049). rows = 한 종목의 분기 재무 행
    {fiscal_year, report_code, consolidated, report_date, revenue, operating_income}.

    - 기준 하나: 가장 최근 분기의 연결(있으면)·별도를 고르고 그 기준 행만 쓴다(웹 분기 표와 같은 규칙, 25.550)
    - 같은 분기 여러 보고서면 가장 늦게 접수된 것
    - 분기마다 전년 같은 분기 대비 매출·영업이익 증가율(전년 영업이익이 0 이하면 증가율 대신 흑자 전환·적자 지속),
      영업이익률
    - 최근 `TREND_QUARTERS` 분기의 영업이익 증가율이 계속 오르면 "가속", 계속 내리면 "감속" — 순서만 본다(문턱 없음)"""
    q = [r for r in rows if str(r.get("report_code")) in QUARTER_OF]
    if not q:
        return None
    key = lambda r: (int(r["fiscal_year"]), QUARTER_OF[str(r["report_code"])])  # noqa: E731
    최근 = max(q, key=lambda r: (*key(r), int(r.get("consolidated") or 0)))
    기준 = int(최근.get("consolidated") or 0)
    칸: dict[tuple[int, int], dict] = {}
    for r in q:
        if int(r.get("consolidated") or 0) != 기준:
            continue
        k = key(r)
        if k not in 칸 or str(r.get("report_date") or "") > str(칸[k].get("report_date") or ""):
            칸[k] = r
    out_rows = []
    for k in sorted(칸, reverse=True):
        r, 전 = 칸[k], 칸.get((k[0] - 1, k[1]))
        rev, op = r.get("revenue"), r.get("operating_income")
        행: dict[str, Any] = {"fy": k[0], "q": k[1],
                              "margin": op / rev if isinstance(op, (int, float)) and rev else None}  # fmt: skip
        if 전:
            prev_rev, prev_op = 전.get("revenue"), 전.get("operating_income")
            행["rev_yoy"] = rev / prev_rev - 1 if isinstance(rev, (int, float)) and prev_rev and prev_rev > 0 else None
            if isinstance(op, (int, float)) and isinstance(prev_op, (int, float)):
                if prev_op > 0:
                    행["op_yoy"] = op / prev_op - 1
                else:
                    행["op_note"] = "흑자 전환" if op > 0 else "적자 지속"
        out_rows.append(행)
        if len(out_rows) == TREND_QUARTERS:
            break
    if not out_rows:
        return None
    성장 = [x.get("op_yoy") for x in reversed(out_rows)]
    추세 = None
    if len(성장) >= 2 and all(isinstance(g, (int, float)) for g in 성장):
        if all(b > a for a, b in zip(성장, 성장[1:], strict=False)):
            추세 = "가속"
        elif all(b < a for a, b in zip(성장, 성장[1:], strict=False)):
            추세 = "감속"
    return {"basis": "연결" if 기준 else "별도", "rows": out_rows, "trend": 추세}


def quarter_line(t: dict | None) -> str | None:
    """분기 실적 추세 한 줄 — 오래된 분기부터."""
    if not t or not t.get("rows"):
        return None
    조각 = []
    for x in reversed(t["rows"]):
        값 = (f"{x['op_yoy'] * 100:+.0f}%" if isinstance(x.get("op_yoy"), (int, float))
              else x.get("op_note") or "전년 없음")  # fmt: skip
        조각.append(f"{x['fy']} {x['q']}분기 {값}")
    마진 = [f"{x['margin'] * 100:.1f}%" for x in reversed(t["rows"]) if isinstance(x.get("margin"), (int, float))]
    꼬리 = f" ({t['trend']})" if t.get("trend") else ""
    return (f"분기 영업이익 전년 같은 분기 대비({t['basis']}): " + " → ".join(조각) + 꼬리
            + (f" · 영업이익률 {' → '.join(마진)}" if len(마진) >= 2 else ""))  # fmt: skip


def decompose(score: dict | None) -> dict | None:
    """종합 점수 분해 (docs/analysis.md 26장, 25.1051) — `scoring.total_score` 를 거꾸로 펼친다.

    팩터 몫 = 팩터 점수 × 정규화 가중치(`scores.weights_json`, 살아 있는 팩터 합 100) ÷ 100,
    감성 몫 = (감성 −100~100 → 0~100 − 50) × 감성 가중치 ÷ 100. 몫의 합 = 종합 점수(0~100 자르기 전).
    score = {total, factors, weights, sentiment, sentiment_weight}. 재료가 모자라면 None."""
    s = score or {}
    f, w = s.get("factors") or {}, s.get("weights") or {}
    if not isinstance(s.get("total"), (int, float)):
        return None
    parts = []
    for k in FACTORS:
        v, wt = f.get(k), w.get(k)
        if isinstance(v, (int, float)) and isinstance(wt, (int, float)):
            parts.append({"factor": k, "score": v, "weight": wt, "contrib": round(v * wt / 100, 2)})
    if not parts:
        return None
    sent = 0.0
    sw, sv = s.get("sentiment_weight"), s.get("sentiment")
    if isinstance(sw, (int, float)) and sw > 0 and isinstance(sv, (int, float)):
        sent = round(((sv + 100) / 2 - 50) * sw / 100, 2)
    return {"parts": parts, "sentiment": sent, "total": s["total"]}


def decompose_change(now: dict | None, before: dict | None) -> dict[str, float] | None:
    """두 날의 분해에서 팩터별 몫의 변화 — "점수가 왜 움직였나".
    가중치가 바뀌었으면 그 몫도 섞인다(그날그날의 가중치)."""
    a, b = decompose(now), decompose(before)
    if not a or not b:
        return None
    pa = {p["factor"]: p["contrib"] for p in a["parts"]}
    pb = {p["factor"]: p["contrib"] for p in b["parts"]}
    out = {k: round(pa.get(k, 0.0) - pb.get(k, 0.0), 2) for k in set(pa) | set(pb)}
    out["sentiment"] = round(a["sentiment"] - b["sentiment"], 2)
    return out
