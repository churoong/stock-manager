"""주간 작업의 '함께 움직이는 종목'(52장)도 같은 발행사를 뺀다 (docs/infra.md 25.1084).

53장 짝(`pairs_from_prices`)은 같은 발행사를 뺐는데 52장은 `issuer_of` 를 넘기지 않아, 보통주·우선주나 GOOG·GOOGL 처럼
같은 회사의 두 주식이 서로 1순위로 잡혔다.
"""

from __future__ import annotations

import json
import random

from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처


def test_같은_발행사는_함께_움직이는_종목이_아니다(client) -> None:  # noqa: ANN001, F811
    from batch.jobs import metrics as metrics_job
    from tests.test_metrics_beta import 계열, 시세넣기
    from tests.test_metrics_beta import 기준일 as 지표_기준일

    random.seed(21)
    n = 400
    시장 = [random.gauss(0, 0.01) for _ in range(n - 1)]
    for d, c in 계열(1000.0, 시장):
        client.conn.execute("INSERT INTO index_prices (index_code, date, close, source, fetched_at)"
                            " VALUES ('KOSPI', ?, ?, 't', 't')", [d, c])  # fmt: skip
    코드 = {sid: f"{sid:05d}0" for sid in range(1, 31)}
    코드[3] = "000025"  # 2번(000020)의 우선주 — 앞 5자리가 같다
    고유 = {sid: [random.gauss(0, 0.01) for _ in range(n - 1)] for sid in range(1, 31)}
    고유[3] = [x + random.gauss(0, 0.0005) for x in 고유[2]]  # 2번과 거의 같이 움직인다
    for sid in range(1, 31):
        if sid > 1:
            client.conn.execute("INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source,"
                                " fetched_at) VALUES (?, ?, 'KOSPI', 'KR', ?, 'KRW', 'active', 't', 't')",
                                [sid, 코드[sid], f"회사{sid}"])  # fmt: skip
        else:
            client.conn.execute("UPDATE stocks SET ticker = ? WHERE id = 1", [코드[1]])
        시세넣기(client, sid, 계열(100.0, [m + e for m, e in zip(시장, 고유[sid], strict=True)]))
    metrics_job.compute_all(client, [(sid, 코드[sid], "KR") for sid in range(1, 31)], ["5Y"], 지표_기준일)  # type: ignore[arg-type]
    행 = json.loads(client.conn.execute("SELECT stats_json FROM price_patterns WHERE stock_id = 2").fetchone()[0])
    짝 = {c["stock_id"] for c in 행.get("comovers") or []}
    assert 짝, "함께 움직이는 종목이 비면 이 검사가 헛돈다"
    assert 3 not in 짝
