"""관문 R 대비·변동성 대비와 순위표 R 대비는 같은 끝 날로 잰다 (docs/infra.md 25.2143, 11-05 결함 회차 [권고]).

예전엔 계열마다 제 마지막 값까지 재, 대조 R 하나의 평가가 멈추면 H 는 지금까지·그 R 은 멈춘 날까지의 수익이 견줘졌다 —
오르는 장에서 실력 0 인 H 가 R 대비 +11%p 로 통과했다(반박 쪽 재현).
"""

from __future__ import annotations

from datetime import date, timedelta

from batch.services import desk


def _계열(n: int, 일수익: float = 0.002, 시작: date = date(2026, 1, 1)) -> list[tuple[str, float]]:
    return [((시작 + timedelta(days=i)).isoformat(), (1 + 일수익) ** i) for i in range(n)]


def _재현() -> dict[str, list[tuple[str, float]]]:
    # H 와 R1 이 똑같이 시장을 따른다(실력 0). R2 만 60일째부터 평가가 없다
    return {"H": _계열(150), "R1": _계열(150), "R2": _계열(60)}


def test_평가가_멈춘_대조가_있어도_실력_0_이면_R_대비는_0() -> None:
    g = desk.gate(_재현(), "H", since="", version=2, recon_bad_days=0, batch_failed=0, kis=None)
    vs_r = next(i for i in g["items"] if i["key"] == "vs_r")
    assert abs(vs_r["now"]) < 1e-9 and not vs_r["ok"], vs_r["now"]  # 예전엔 +11.08%p·통과


def test_변동성_대비도_같은_끝_날() -> None:
    s = _재현()
    s["R2"] = [(d, v * (1.001 if i % 2 else 0.999)) for i, (d, v) in enumerate(s["R2"])]  # 대조에 흔들림
    끝 = s["R2"][-1][0]
    g = desk.gate(s, "H", since="", version=2, recon_bad_days=0, batch_failed=0, kis=None)
    ratio = next(i for i in g["items"] if i["key"] == "ratio")
    assert ratio["now"] == desk.risk_ratio(s["H"], s["H"][0][0], 끝)


def test_순위표_R_대비도_같은_끝_날() -> None:
    lb = desk.leaderboard(_재현())
    h = next(r for r in lb["rows"] if r["id"] == "H")
    assert abs(h["vs_r_pp"]) < 1e-9, h["vs_r_pp"]
    assert h["window_pct"] > 30  # 화면의 구간 수익은 그대로 지금까지
