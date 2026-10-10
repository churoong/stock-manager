"""종목 하나로 돈 성과 지표가 나라 전체 신고가 분포를 덮어쓰지 않는다 (docs/infra.md 25.1085)."""

from __future__ import annotations

import inspect
import json

from batch.core import db
from batch.jobs import metrics as metrics_job
from batch.services import history as hs
from tests.test_metrics_beta import client as client  # noqa: F401 — 픽스처
from tests.test_metrics_beta import 계열, 수익률, 시세넣기
from tests.test_metrics_beta import 기준일 as 지표_기준일


def test_종목_하나로_돌면_시장_분포를_두고_간다(client) -> None:  # noqa: ANN001, F811
    시장 = {"as_of": "2026-10-02", "h": {"1": {"n": 5000, "median": 0.01, "up": 0.55}}}
    db.set_setting(client, hs.market_key("KR"), 시장)
    시세넣기(client, 1, 계열(1000, 수익률(700)))
    metrics_job.compute_all(client, [(1, "005930", "KR")], ["5Y"], 지표_기준일, market_wide=False)  # type: ignore[arg-type]
    남은 = json.loads(client.conn.execute("SELECT value FROM settings WHERE key = ?", [hs.market_key("KR")]).fetchone()[0])
    assert 남은["h"]["1"]["n"] == 5000
    # 종목 패턴은 그대로 쌓는다
    assert client.conn.execute("SELECT COUNT(*) FROM price_patterns").fetchone()[0] == 1


def test_종목_실행은_주간_작업과_다른_이름으로_남긴다() -> None:
    """같은 이름이면 주간 지표가 멈춰도 '오래 안 돈 작업' 경고가 참고 분석의 성공 기록에 가려진다."""
    src = inspect.getsource(metrics_job.run)
    assert "JOB_NAME_ONE if ticker else JOB_NAME" in src and "market_wide=ticker is None" in src
    assert metrics_job.JOB_NAME_ONE != metrics_job.JOB_NAME
