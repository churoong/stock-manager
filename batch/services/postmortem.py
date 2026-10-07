"""틀린 추천 사후 분석 (docs/reports.md 3.8, docs/infra.md 25.998).

20거래일 성적이 이번 주에 나온 신호 가운데 **손실 난 것**을 셋으로 나눈다:

    종목 수익 = 시장 + (업종 − 시장) + (종목 − 업종)
              = 시장 몫   + 업종 몫       + 종목 고유 몫

- 시장 = 같은 기간 지수 수익(`signal_outcomes.bench_ret_20d`)
- 업종 = 같은 업종(우리 `stocks.sector`) 유니버스 종목들의 같은 기간 평균 수익(진입일 종가 → 20거래일째 종가)
- 가장 크게 깎은 몫이 그 손실의 "주된 원인". 기간 안에 공시가 있었으면 함께 적는다(원인이라 단정하지 않는다)

맞힌 것만 자랑하지 않고 틀린 이유를 스스로 밝힌다 — 주간 운영 요약에 싣는다. 점수·신호를 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 업종 평균을 믿는 최소 종목 수 — 이보다 적으면 업종 몫을 0 으로 두고(시장과 같다고 보고) 종목 고유 몫에 넣는다
MIN_SECTOR_PEERS = 5
#: 예로 보일 손실 수
MAX_EXAMPLES = 3

LABEL = {"market": "시장", "sector": "업종", "idio": "종목 고유"}


@dataclass(frozen=True)
class Loss:
    name: str
    horizon: str
    ret: float
    market: float
    sector_ret: float | None  # 업종 평균(표본이 모자라면 None)
    peers: int
    disclosure: str | None  # 기간 안 첫 공시 제목


def parts(x: Loss) -> dict[str, float]:
    sec = x.sector_ret if (x.sector_ret is not None and x.peers >= MIN_SECTOR_PEERS) else x.market
    return {"market": x.market, "sector": sec - x.market, "idio": x.ret - sec}


def cause(x: Loss) -> str:
    """가장 크게 깎은(가장 음수인) 몫의 키."""
    p = parts(x)
    return min(p, key=lambda k: p[k])


def render(total: int, losses: list[Loss], n_losses: int | None = None) -> list[str]:
    """주간 요약 절. 손실이 없으면 한 줄. `losses` 가 상한으로 잘렸으면 `n_losses` 가 전체 손실 수다."""
    if not total:
        return []
    전체손실 = len(losses) if n_losses is None else n_losses
    잘림 = f"(나쁜 순 {len(losses)}건만 나눔)" if 전체손실 > len(losses) else ""
    head = f"지난 추천 사후 분석 — 20거래일 성적이 이번 주 나온 신호 {total}건 중 손실 {전체손실}건{잘림}"
    if not losses:
        return [head]
    세기 = {k: sum(1 for x in losses if cause(x) == k) for k in LABEL}
    공시 = sum(1 for x in losses if x.disclosure)
    원인 = " · ".join(f"{LABEL[k]} {n}" for k, n in 세기.items())
    out = [head, f"  주된 원인: {원인} (기간 안 공시 있음 {공시})"]
    for x in sorted(losses, key=lambda x: x.ret)[:MAX_EXAMPLES]:
        p = parts(x)
        조각 = " · ".join(f"{LABEL[k]} {p[k] * 100:+.1f}%p" for k in LABEL)
        공시말 = f" · 공시 \"{x.disclosure}\"" if x.disclosure else ""
        업종말 = "" if x.peers >= MIN_SECTOR_PEERS else f" (업종 표본 {x.peers}개 — 시장과 같다고 봄)"
        out.append(f"  {x.name}({x.horizon}) {x.ret * 100:+.1f}% = {조각}{업종말}{공시말}")
    return out
