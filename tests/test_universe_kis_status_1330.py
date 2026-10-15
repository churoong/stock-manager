"""국내 유니버스가 소속부를 지난 편입 종목을 KIS 종목 상태로 한 번 더 거른다 (docs/infra.md 25.1330, 20회차 P4 2단계).

탐침(25.1328): 코스피 편입 385종목의 소속부가 전부 빈 글자 — 코스피 관리종목은 소속부로 걸릴 수 없었고, 거래정지
제외는 시장 전체 0, 편입인데 KIS 로 정지인 종목 1. 모름(응답 없음·실패·키 없음)은 지금 판정 그대로다.
"""

from __future__ import annotations

from typing import Any

from batch.jobs import universe as job
from batch.services import universe as uni
from tests.test_universe_snapshot import client, 기준일, 시세넣기  # noqa: F401 — 픽스처

보통 = {"iscd_stat_cls_code": "55", "mang_issu_cls_code": "N", "sltr_yn": "N", "temp_stop_yn": "N"}


def test_상태_코드를_사유로() -> None:
    assert uni.kis_status_reason(보통) is None
    assert uni.kis_status_reason(보통 | {"iscd_stat_cls_code": "58"}) == uni.REASON_HALTED
    assert uni.kis_status_reason(보통 | {"temp_stop_yn": "Y"}) == uni.REASON_HALTED
    assert uni.kis_status_reason(보통 | {"iscd_stat_cls_code": "51"}) == uni.REASON_SUPERVISED
    assert uni.kis_status_reason(보통 | {"mang_issu_cls_code": "Y"}) == uni.REASON_SUPERVISED
    # 관리이면서 정지·정리매매면 judge 와 같은 순서 — 정리매매 → 거래정지 → 관리종목
    assert uni.kis_status_reason({"iscd_stat_cls_code": "58", "mang_issu_cls_code": "Y"}) == uni.REASON_HALTED
    assert uni.kis_status_reason({"sltr_yn": "Y", "mang_issu_cls_code": "Y"}) == uni.REASON_LIQUIDATION
    assert uni.kis_status_reason(None) is None and uni.kis_status_reason({}) is None
    # 경고 단계 코드는 보지 않는다
    assert uni.kis_status_reason(보통 | {"iscd_stat_cls_code": "53", "mrkt_warn_cls_code": "02"}) is None


def _돌림(client: Any, fetch: Any) -> tuple[dict, list[str], dict, dict[str, Any]]:  # noqa: F811
    for i in (1, 2, 3):
        시세넣기(client.conn, i, 40, 1_000_000_000)
    info: dict[str, Any] = {}
    counts, warnings = job.build_snapshot(client, "KR", 기준일, kis_status=fetch, info=info)
    rows = dict(client.conn.execute("SELECT stock_id, COALESCE(exclude_reason, '편입') FROM universe_members").fetchall())
    return counts, warnings, rows, info


def test_KIS_가_정지라_하면_뺀다(client: Any) -> None:  # noqa: F811
    부른것: list[list[str]] = []

    def fetch(tickers: list[str]) -> tuple[dict[str, dict], int]:
        부른것.append(tickers)
        return {"005931": 보통, "005932": 보통 | {"iscd_stat_cls_code": "58"}}, 1  # 005933 은 응답 없음

    counts, warnings, rows, info = _돌림(client, fetch)
    assert 부른것 == [["005931", "005932", "005933"]]  # 편입만 부른다
    assert rows == {1: "편입", 2: uni.REASON_HALTED, 3: "편입"}  # 응답 없는 종목은 그대로 편입
    assert counts["편입"] == 2 and counts[uni.REASON_HALTED] == 1
    assert info["kis_status"]["excluded"][0][:3] == ["005932", uni.REASON_HALTED, "58"]
    assert info["kis_status"]["failed"] == 1
    assert any("실패 1/3" in w for w in warnings)  # 5% 넘는 실패는 말한다


def test_키가_없으면_그대로_두고_말한다(client: Any) -> None:  # noqa: F811
    counts, warnings, rows, info = _돌림(client, None)
    assert set(rows.values()) == {"편입"} and "kis_status" not in info
    assert any("KIS 키가 없어" in w for w in warnings)


def test_부르기가_통째로_실패해도_그대로(client: Any) -> None:  # noqa: F811
    def fetch(_tickers: list[str]) -> tuple[dict[str, dict], int]:
        raise RuntimeError("토큰 HTTP 500")

    _counts, warnings, rows, _info = _돌림(client, fetch)
    assert set(rows.values()) == {"편입"}
    assert any("하지 못했습니다" in w for w in warnings)


def test_수치_사유로_빠진_종목도_KIS_사유로_바로잡는다(client: Any) -> None:  # noqa: F811
    """장기 정지는 거래대금이 0 이라 수치 사유로 먼저 빠졌다 — 매도 플래그가 "거래대금미달" 로 나갔다 (25.1407)"""
    부른것: list[list[str]] = []

    def fetch(tickers: list[str]) -> tuple[dict[str, dict], int]:
        부른것.append(tickers)
        return {t: 보통 | {"iscd_stat_cls_code": "58"} if t == "005932" else 보통 for t in tickers}, 0

    시세넣기(client.conn, 1, 40, 1_000_000_000)
    시세넣기(client.conn, 2, 40, 0)  # 20일 거래대금 0 — 장기 정지
    시세넣기(client.conn, 3, 40, 1_000_000_000)
    info: dict[str, Any] = {}
    job.build_snapshot(client, "KR", 기준일, kis_status=fetch, info=info)
    rows = dict(client.conn.execute("SELECT stock_id, COALESCE(exclude_reason, '편입') FROM universe_members").fetchall())
    assert "005932" in 부른것[0]
    assert rows[2] == uni.REASON_HALTED  # 예전 거래대금미달
    assert rows[1] == "편입" and rows[3] == "편입"
