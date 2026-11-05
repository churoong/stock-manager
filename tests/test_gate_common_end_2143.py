"""관문 R 대비·변동성 대비, 순위표·판 이력 R 대비는 R 마다 함께 평가된 마지막 날까지 짝지어 잰다
(docs/infra.md 25.2143·25.2145, 11-05 결함 회차 [권고]·교차검증 [권고]).

예전엔 계열마다 제 마지막 값까지 재, 대조 R 하나의 평가가 멈추면 오르는 장에서 실력 0 인 H 가 R 대비 +11%p 로 통과했다.
25.2143 의 공통 끝 하나(가장 이른 날)는 멈춘 R 에 얼어붙고, 끝이 시작보다 이르면 거꾸로 잰 값(내리는 장에서 +2.1%p)이 났다.
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import desk


def _계열(n: int, 일수익: float = 0.002, 시작: date = date(2026, 1, 1), 흔들림: float = 0.0) -> list[tuple[str, float]]:
    out, x = [], 1.0
    for i in range(n):
        out.append(((시작 + timedelta(days=i)).isoformat(), x))
        x *= 1 + 일수익 + (흔들림 if i % 2 else -흔들림)
    return out


def _재현(일수익: float = 0.002, 멈춤: int = 60) -> dict[str, list[tuple[str, float]]]:
    # H 와 R1 이 똑같이 시장을 따른다(실력 0). R2 만 `멈춤` 일째부터 평가가 없다
    return {"H": _계열(150, 일수익), "R1": _계열(150, 일수익), "R2": _계열(멈춤, 일수익)}


def _칸(g: dict, key: str) -> dict:
    return next(i for i in g["items"] if i["key"] == key)


def test_평가가_멈춘_대조가_있어도_실력_0_이면_R_대비는_0() -> None:
    g = desk.gate(_재현(), "H", since="", version=2, recon_bad_days=0, batch_failed=0, kis=None)
    vs_r = _칸(g, "vs_r")
    assert abs(vs_r["now"]) < 1e-9 and not vs_r["ok"], vs_r["now"]  # 예전엔 +11.08%p·통과
    assert vs_r["to"] == _재현()["R2"][-1][0]  # 가장 이른 짝 끝(R2 멈춘 날) — 화면이 "…까지" 를 적는다


def test_멈춘_R_이_관문_시작_전이면_그_R_은_빼고_내리는_장에서도_거꾸로_재지_않는다() -> None:
    """25.2145 — 교차검증 재현: −0.2%/일, R2 40일째 멈춤, since 60일째. 25.2143 은 +2.147%p·통과였다"""
    s = _재현(-0.002, 40)
    since = s["H"][59][0]
    g = desk.gate(s, "H", since=since, version=2, recon_bad_days=0, batch_failed=0, kis=None)
    vs_r = _칸(g, "vs_r")
    assert abs(vs_r["now"]) < 1e-9 and not vs_r["ok"], vs_r["now"]
    assert desk.r_pairs(s["H"], s, since) == [("R1", s["H"][-1][0])]  # R2 는 함께 평가된 구간이 없다


def test_멈춘_R_에_얼어붙지_않는다() -> None:
    """R2 가 멈춘 뒤 H 가 R1 보다 크게 뒤지면 R 대비가 그만큼 내려간다(25.2143 의 공통 끝은 멈춘 날에 얼었다)"""
    s = _재현()
    s["H"] = [(d, v * (0.8 if i >= 100 else 1.0)) for i, (d, v) in enumerate(s["H"])]  # 100일째 H −20%
    g = desk.gate(s, "H", since="", version=2, recon_bad_days=0, batch_failed=0, kis=None)
    assert _칸(g, "vs_r")["now"] < -5


def test_변동성_대비는_R_마다_짝지은_끝으로() -> None:
    s = {"H": _계열(150, 0.002, 흔들림=0.003), "R1": _계열(150, 0.002, 흔들림=0.004), "R2": _계열(60, 0.002, 흔들림=0.002)}
    g = desk.gate(s, "H", since="", version=2, recon_bad_days=0, batch_failed=0, kis=None)
    ratio = _칸(g, "ratio")
    시작 = s["H"][0][0]
    짝 = [(desk.risk_ratio(s["H"], 시작, e), desk.risk_ratio(s[r], 시작, e)) for r, e in desk.r_pairs(s["H"], s, 시작)]
    assert len(짝) == 2
    assert abs(ratio["now"] - sum(m for m, _ in 짝) / 2) < 1e-12 and abs(ratio["target"] - sum(o for _, o in 짝) / 2) < 1e-12


def test_순위표_R_대비도_짝지어_재고_끝_날을_적는다() -> None:
    s = _재현()
    s["R1"] = s["R1"][:120]  # 두 R 모두 H 보다 먼저 멈춤
    lb = desk.leaderboard(s)
    h = next(r for r in lb["rows"] if r["id"] == "H")
    assert abs(h["vs_r_pp"]) < 1e-9, h["vs_r_pp"]
    assert h["vs_r_to"] == s["R2"][-1][0]  # 가장 이른 짝 끝 — 화면이 "…까지" 를 적는다
    assert h["window_pct"] > 30  # 화면의 구간 수익은 그대로 지금까지


def test_판_이력_지금_판도_짝지어_잰다() -> None:
    hist = desk.rule_history([("H", 2, "")], _재현())
    assert abs(hist["H"][0]["vs_r_pp"]) < 1e-9, hist["H"][0]["vs_r_pp"]  # 예전엔 +11.08%p
