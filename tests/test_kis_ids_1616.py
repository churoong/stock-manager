"""25.1615 교차검증 [필수] 1·2·[권고] 4 (docs/paper.md 15장, docs/infra.md 25.1616).

지키는 것(같은 티커 두 행 — 옛 행 30 NYSE excluded, 새 행 2 NASDAQ active)
- 같은 날 다시 돌 때 이긴 행이 바뀌어도(오늘 새 행을 처음 삼) 앞 실행이 낸 정리를 다시 내지 않는다
- 맞추지 못한(`unknown`) 매수도 **국내는** 그 행의 증권사 쪽 순매수로 센다 — H 가 든 그 행 주식을 이긴 행 정리로 팔지 않는다
- 매도가 걸린 행이 없으면 주문가능은 이긴 행부터
"""

from __future__ import annotations

from batch.jobs import broker_mirror as job
from batch.services import broker_mirror as bm
from tests.test_kis_ids_1608 import _두_행
from tests.test_kis_ids_1614 import _bo, _fill, _run
from tests.test_kis_vts_us_1485 import c  # noqa: F401 — 픽스처
from tests.test_paper_job_1445 import Client


def test_같은_날_다시_돌아_이긴_행이_바뀌어도_정리를_다시_내지_않는다(c: Client) -> None:  # noqa: F811
    # H 는 옛 행 3주를 이미 팔았는데 KIS 베낀 매도는 안 찼다(KIS 에 3주). 오늘 H 가 새 행을 처음 산다(2주)
    _두_행(c, 30, "NYSE", 2, "NASDAQ")
    c.conn.execute("UPDATE paper_orders SET status = 'filled', fill_session = '2026-10-15' WHERE id = 301")
    _fill(c, 320, 30, "sell", 3, "2026-10-15")
    _bo(c, 30, "sell", "unfilled", 0, 3, "2026-10-15", 320)
    c.conn.execute("INSERT INTO paper_orders (id, account_id, market, stock_id, side, order_type, limit_price, quantity,"
                   " currency, decided_on, fill_session, reason, rule_version, tranche, created_at) VALUES (330, 'B',"
                   " 'US', 2, 'buy', 'limit', 200, 2, 'USD', '2026-10-15', '2026-10-16', 'r', 1, 2, 't')")  # fmt: skip
    첫 = _run(c, 3)[0]
    assert sorted(첫) == [("buy", "NASD", 2), ("sell", "NYSE", 3)]
    assert _run(c, 3)[0] == []  # 예전엔 이긴 행이 새 행으로 바뀌어 NASD 로 1주 더 정리했다


def test_unknown_매수는_국내만_그_행_몫으로_센다(c: Client) -> None:  # noqa: F811
    # 국내 unknown(번호 없는 줄이 체결 조회에서 안 잡힘·3개월 넘김)은 실제로 찼을 수 있다 — 0 으로 세면 H 가 든 그 행 주식이
    # 이긴 행 정리로 팔렸다. 미국 unknown 은 대개 예약이 개장 때 전송되지 않은 것(안 참)이라 0
    _bo(c, 2, "buy", "unknown", 0, 3, "2026-10-14", None)
    _bo(c, 2, "buy", "filled", 2, 2, "2026-10-15", None)
    _bo(c, 2, "sell", "filled", 1, 1, "2026-10-15", None)
    for 국내, 기대 in ((1, 3 + 2 - 1), (0, 2 - 1)):
        assert c.q(job.NET_SQL, [국내, "kis_vts_us", "[2]"]) == [(2, 기대)]


def test_매도_걸린_행이_없으면_주문가능은_이긴_행부터() -> None:
    assert bm.split_holding(5, 2, [30, 2], {30: 3}) == {30: (3, 0), 2: (2, 2)}
    assert bm.split_holding(5, 2, [30, 2], {30: 3}, {30}) == {30: (3, 2), 2: (2, 0)}
