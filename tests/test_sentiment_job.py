"""뉴스 감성 채점·집계 배치 테스트. 실제 마이그레이션을 적용한 메모리 SQLite, VADER 는 실제 사전(네트워크 없음)."""

from __future__ import annotations

import pytest

from batch.jobs import sentiment as job
from tests.test_portfolio_job import MemClient


def test_VADER_는_금융_헤드라인에서_방향을_맞춘다() -> None:
    score = job.vader_scorer()
    assert score("Apple posts strong results, analysts see great growth")[0] > 0.05
    assert score("Company slashes forecast amid fraud probe, stock plunges")[0] < -0.05


def test_채점하고_종목별로_집계한다(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
        " VALUES (1, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't'),"
        " (2, 'MSFT', 'NASDAQ', 'US', 'Microsoft', 'USD', 'active', 't', 't')"
    )
    titles = [
        "Apple beats estimates, shares surge to record",
        "Apple wins big contract, analysts upgrade",
        "Apple posts strong growth in services",
        "Apple stock rallies on great iPhone demand",
        "Apple announces excellent quarterly results",
    ]
    for i, title in enumerate(titles):
        c.execute(
            "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (1, ?, ?, ?, 'en', 'nasdaq_rss', 't')",
            [title, f"https://x/{i}", f"2026-09-1{i}T12:00:00.000Z"],
        )
    c.execute(
        "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (2, 'Microsoft faces lawsuit', 'https://x/m', '2026-09-16T12:00:00.000Z', 'en', 'nasdaq_rss', 't')"
    )

    assert job.run("US", "2026-09-17") == 0
    assert c.execute("SELECT COUNT(*) FROM article_sentiments WHERE method = 'vader'").fetchone()[0] == 6
    rows = {r[0]: r for r in c.execute("SELECT stock_id, sentiment, article_count FROM sentiment_scores").fetchall()}
    assert rows[1][1] > 0 and rows[1][2] == 5
    assert rows[2][1] is None and rows[2][2] == 1  # 5건 미만이면 점수 없음

    # 다시 돌려도 같은 기사를 두 번 채점하지 않는다
    assert job.run("US", "2026-09-17") == 0
    assert c.execute("SELECT COUNT(*) FROM article_sentiments").fetchone()[0] == 6

    # **보정 사전 판이 다른 미국 기사는 다시 채점한다** (docs/infra.md 25.545) — 옛 점수와 새 점수가 섞이지 않게
    c.execute("UPDATE article_sentiments SET method_version = 'old', score = 0.99 WHERE news_id = 6")
    assert job.run("US", "2026-09-17") == 0
    판, 점수 = c.execute("SELECT method_version, score FROM article_sentiments WHERE news_id = 6").fetchone()
    assert 판 == job.vader_method_version() and 점수 != 0.99
    assert c.execute("SELECT COUNT(*) FROM article_sentiments").fetchone()[0] == 6
    # 판이 달라도 **재채점 창(61일) 밖 기사는 다시 쓰지 않는다** (25.548·25.553) — 전 이력을 다시 쓰고 있었다
    c.execute("UPDATE article_sentiments SET method_version = 'old', score = 0.99 WHERE news_id = 1")
    assert job.run("US", "2026-11-15") == 0  # 9/10 기사는 61일 창 밖
    assert c.execute("SELECT method_version, score FROM article_sentiments WHERE news_id = 1").fetchone() == ("old", 0.99)
    # 30일 변화가 보는 창(49일 전 기사)은 다시 매긴다 — 38일이던 때는 옛 판이 남았다 (25.553)
    c.execute("UPDATE article_sentiments SET method_version = 'old', score = 0.99 WHERE news_id = 2")
    assert job.run("US", "2026-10-30") == 0
    assert c.execute("SELECT method_version FROM article_sentiments WHERE news_id = 2").fetchone()[0] != "old"

    # **30일 창에서 기사가 모두 빠진 날도 행을 쓴다** (docs/infra.md 25.413). 예전에는 건너뛰어
    # 어제의 점수가 사흘 더(점수 배치의 3일 창) 쓰였다. 9/10~14 기사는 10/14 에 창 밖이다
    assert job.run("US", "2026-10-15") == 0
    빈날 = c.execute(
        "SELECT sentiment, article_count, delta_7d FROM sentiment_scores WHERE stock_id = 1 AND as_of_date = '2026-10-15'"
    ).fetchone()
    assert 빈날 == (None, 0, None)


def test_국내는_일일_배치에서_채점하지_않고_집계만(monkeypatch: pytest.MonkeyPatch) -> None:
    """국내 모델은 무거워 sentiment-kr.yml 이 따로 채점한다. 일일 배치(score=None)는 채점된 것만 집계한다."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(
        job, "korfinasc_scorer", lambda: (_ for _ in ()).throw(AssertionError("모델을 불러오면 안 된다"))
    )
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    for i in range(5):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, '삼성전자 실적 호조', ?, ?, 'ko', 'yna_rss', 't')",
            [i + 1, f"https://y/{i}", f"2026-09-1{i}T00:00:00.000Z"],
        )
        c.execute(
            "INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (?, 0.9, 'korfinasc', 't')",
            [i + 1],
        )
    c.execute(
        "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (9, 1, '아직 채점 안 됨', 'https://y/9', '2026-09-16T00:00:00.000Z', 'ko', 'yna_rss', 't')"
    )
    assert job.run("KR", "2026-09-17") == 0
    row = c.execute("SELECT sentiment, article_count, method FROM sentiment_scores").fetchone()
    assert row[0] == pytest.approx(90.0) and row[1] == 5 and row[2] == "korfinasc"


def test_채점_안_된_기사가_많으면_값을_내지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """국내 채점이 멈춘 날 옛 기사로만 낸 값이 오늘 감성이 됐다 (docs/infra.md 25.834, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    for i in range(5):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, ?, 'ko', 'yna_rss', 't')",
            [i + 1, f"삼성전자 호재 {i}", f"https://y/{i}", f"2026-09-1{i}T00:00:00.000Z"],
        )
        c.execute(
            "INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (?, 0.9, 'korfinasc', 't')",
            [i + 1],
        )
    for i in range(3):  # 최근 악재 셋이 아직 채점되지 않았다 — 3/8 > 20%
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, '2026-09-16T00:00:00.000Z', 'ko', 'yna_rss', 't')",
            [20 + i, f"삼성전자 악재 {i}", f"https://y/u{i}"],
        )
    assert job.run("KR", "2026-09-17") == 0
    row = c.execute("SELECT sentiment, article_count, delta_7d, delta_30d FROM sentiment_scores").fetchone()
    # 값을 내지 않은 날은 변화도 내지 않는다 — 매도 플래그가 delta_7d 로 판정한다 (25.838, 교차검증)
    assert row[0] is None and row[1] == 5 and row[2] is None and row[3] is None
    log = c.execute("SELECT step_log FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert '"dropped_for_unscored": 1' in log
    # 다른 방식(method)으로 채점된 기사는 국내 방식으로는 미채점이다
    c.execute("INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (20, 0.1, 'vader', 't')")
    assert job.run("KR", "2026-09-17") == 0
    assert c.execute("SELECT sentiment FROM sentiment_scores").fetchone()[0] is None


def test_창_밖_옛_기사는_미채점_비율을_묽히지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """61일 전부터 세면 오래된 기사가 많을수록 보호가 무력해졌다 — 30일 창 안에서 잰다 (25.838, 교차검증)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    n = 0
    # 창 밖(8/1~15) 채점된 기사 15건, 창 안 채점 6건, 창 안 미채점 2건 — 창 안 2/8 = 25% > 20%, 61일로 세면 2/23 = 9%
    for 날, 채점 in [(f"2026-08-{d:02d}", True) for d in range(1, 16)] + [(f"2026-09-{d:02d}", True) for d in range(10, 16)] \
            + [("2026-09-16", False), ("2026-09-16", False)]:
        n += 1
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, ?, 'ko', 'yna_rss', 't')",
            [n, f"삼성전자 소식 {n}", f"https://y/{n}", f"{날}T00:00:00.000Z"],
        )
        if 채점:
            c.execute("INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (?, 0.5, 'korfinasc', 't')", [n])
    assert job.run("KR", "2026-09-17") == 0
    assert c.execute("SELECT sentiment FROM sentiment_scores").fetchone()[0] is None
    # 문턱 바로 아래(1/7)면 값을 낸다
    c.execute("DELETE FROM news WHERE id = ?", [n])
    assert job.run("KR", "2026-09-17") == 0
    assert c.execute("SELECT sentiment FROM sentiment_scores").fetchone()[0] is not None


def test_국내_채점은_긍정_빼기_부정(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    calls: list[list[tuple[str, str]]] = []

    def fake_scorer():
        def score(pairs):
            calls.append(pairs)
            return [(0.7 - 0.1, {"positive": 0.7, "negative": 0.1, "neutral": 0.2}) for _ in pairs]

        return score

    monkeypatch.setattr(job, "korfinasc_scorer", fake_scorer)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '012750', 'KOSPI', 'KR', '에스원', 'KRW', 'active', 't', 't')"
    )
    c.execute(
        "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
        " VALUES (1, 1, '에스원 보안 수주', 'https://y/1', '2026-09-16T00:00:00.000Z', 'ko', 'yna_rss', 't')"
    )
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test")
    assert job.run("KR", "2026-09-17", score=True) == 0
    assert calls == [[("에스원 보안 수주", "에스원")]]  # 종목명을 대상으로 넘긴다
    assert c.execute("SELECT score, method FROM article_sentiments").fetchone() == (pytest.approx(0.6), "korfinasc")


def test_별칭으로_붙은_기사는_제목에_나온_이름을_대상으로_채점한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """"네이버, 3분기 영업익 급감" 을 NAVER 에 대한 감성으로 물었다 (docs/infra.md 25.938, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    calls: list[list[tuple[str, str]]] = []

    def fake_scorer():
        def score(pairs):
            calls.append(pairs)
            return [(0.0, {"positive": 0.3, "negative": 0.3, "neutral": 0.4}) for _ in pairs]

        return score

    monkeypatch.setattr(job, "korfinasc_scorer", fake_scorer)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (7, '035420', 'KOSPI', 'KR', 'NAVER', 'KRW', 'active', 't', 't')"
    )
    c.execute("INSERT OR IGNORE INTO stock_aliases VALUES (7, '네이버', 'claude', 't')")
    for i, 제목 in ((1, "네이버, 3분기 영업익 급감"), (2, "NAVER 신고가")):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 7, ?, ?, '2026-09-16T00:00:00.000Z', 'ko', 'yna_rss', 't')",
            [i, 제목, f"https://y/{i}"],
        )
    monkeypatch.setattr("importlib.metadata.version", lambda name: "test")
    assert job.run("KR", "2026-09-17", score=True) == 0
    assert sorted(calls[0]) == [("NAVER 신고가", "NAVER"), ("네이버, 3분기 영업익 급감", "네이버")]


def test_대상_이름은_약칭_다음_가장_긴_별칭() -> None:
    assert job.aspect_target("신한금융 순익", "신한지주", ["신한금융", "신한금융지주"]) == "신한금융"
    assert job.aspect_target("신한금융지주 순익", "신한지주", ["신한금융", "신한금융지주"]) == "신한금융지주"
    assert job.aspect_target("신한지주 순익", "신한지주", ["신한금융"]) == "신한지주"
    assert job.aspect_target("아무 이름 없음", "신한지주", ["신한금융"]) == "신한지주"


def test_금융_제목_보정_사전으로_급락_제목은_음수다() -> None:
    """VADER 일반 사전은 "shares"(+1.2)를 긍정으로 보고 plunge 를 몰라 급락 제목이 양수였다 (docs/infra.md 25.545, 감사 재현)."""
    pytest.importorskip("vaderSentiment")
    from batch.jobs import sentiment as job

    채점 = job.vader_scorer()
    for 제목 in ("Apple shares plunge after earnings miss", "Apple shares slide for fifth day",
                 "Apple stock tumbles 9% after report", "Apple downgraded to sell at Goldman",
                 "Why Apple Stock Is Plummeting Today"):  # fmt: skip
        assert 채점(제목)[0] <= -0.05, 제목
    assert 채점("Apple shares rise after strong iPhone sales")[0] >= 0.05
    assert 채점("Apple shares plunge")[0] < 채점("Apple shares rise")[0]
    # 판 2 (25.548, 교차검증) — 흔한 하락 동사가 0 이었고, 반등 제목은 음수였다
    for 제목 in ("Apple stock drops 5%", "Apple stock slips", "Apple shares dip", "Nvidia crashes 10%",
                 "Apple shares tank"):  # fmt: skip
        assert 채점(제목)[0] <= -0.05, 제목
    assert 채점("Apple shares rebound after selloff")[0] > 채점("Apple shares selloff")[0]


def test_미채점_창_시작은_UTC_로_비교한다(monkeypatch: pytest.MonkeyPatch) -> None:
    """창 시작을 현지(+09:00) 문자열로 넘기면 UTC 로 저장된 기사와 글자 비교가 어긋나, 창 첫날 9시간 안의
    미채점 기사를 세지 않는다 (25.838 의 `.astimezone(UTC)`, 25.850 에서 지키는 검사를 더함)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    for i in range(5):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, ?, 'ko', 'yna_rss', 't')",
            [i + 1, f"삼성전자 소식 {i}", f"https://y/{i}", f"2026-09-1{i}T00:00:00.000Z"],
        )
        c.execute(
            "INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (?, 0.5, 'korfinasc', 't')",
            [i + 1],
        )
    # 기준일 9/17 의 창 끝은 9/17 23:59:59 KST, 시작은 8/18 23:59:59 KST = 8/18 14:59:59Z.
    # 8/18 18:00Z 는 창 안이지만 "2026-08-18T18…" < "2026-08-18T23:59:59+09:00" 라 현지 문자열로 비교하면 빠진다
    for n, 시각 in ((8, "2026-08-18T16:00:00.000Z"), (9, "2026-08-18T18:00:00.000Z")):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, ?, 'ko', 'yna_rss', 't')",
            [n, f"삼성전자 악재 {n}", f"https://y/{n}", 시각],
        )
    assert job.run("KR", "2026-09-17") == 0
    # 2/7 = 29% > 20% — 값을 내지 않는다
    assert c.execute("SELECT sentiment FROM sentiment_scores").fetchone()[0] is None
    # 대조: 창 시작(14:59:59Z) 바로 앞으로 옮기면 창 밖이라 값을 낸다
    c.execute("UPDATE news SET published_at = '2026-08-18T14:00:00.000Z' WHERE id IN (8, 9)")
    assert job.run("KR", "2026-09-17") == 0
    assert c.execute("SELECT sentiment FROM sentiment_scores").fetchone()[0] is not None


def test_실행_기록에_유효_기사_수_분포를_남긴다_25_873(monkeypatch: pytest.MonkeyPatch) -> None:
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_ko, currency, status, source, fetched_at)"
        " VALUES (1, '005930', 'KOSPI', 'KR', '삼성전자', 'KRW', 'active', 't', 't')"
    )
    for i in range(5):
        c.execute(
            "INSERT INTO news (id, stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (?, 1, ?, ?, ?, 'ko', 'yna_rss', 't')",
            [i + 1, f"삼성전자 소식 {i}", f"https://y/{i}", f"2026-09-1{i}T00:00:00.000Z"],
        )
        c.execute("INSERT INTO article_sentiments (news_id, score, method, created_at) VALUES (?, 0.5, 'korfinasc', 't')", [i + 1])
    assert job.run("KR", "2026-09-17") == 0
    log = c.execute("SELECT step_log FROM batch_runs ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert '"effective_n": {"n": 1' in log


def test_NaN_점수는_강한_긍정으로_저장하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`max(-1, min(1, nan))` 이 +1.0 이라 모델이 NaN 을 내면 강한 긍정으로 저장됐다 (docs/infra.md 25.1097, 감사)."""
    from batch.core import db

    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    db.apply_migrations(mem)  # type: ignore[arg-type]
    c = mem.conn
    c.execute(
        "INSERT INTO stocks (id, ticker, market, country, name_en, currency, status, source, fetched_at)"
        " VALUES (1, 'AAPL', 'NASDAQ', 'US', 'Apple', 'USD', 'active', 't', 't')"
    )
    for i, title in enumerate(["nan please", "Apple beats estimates"]):
        c.execute(
            "INSERT INTO news (stock_id, title, url, published_at, lang, source, fetched_at)"
            " VALUES (1, ?, ?, '2026-09-16T12:00:00.000Z', 'en', 'nasdaq_rss', 't')",
            [title, f"https://x/{i}"],
        )
    진짜 = job.vader_scorer()
    monkeypatch.setattr(job, "vader_scorer", lambda: lambda t: (float("nan"), {}) if t.startswith("nan") else 진짜(t))
    assert job.run("US", "2026-09-17") == 0
    점수 = dict(c.execute("SELECT n.title, a.score FROM article_sentiments a JOIN news n ON n.id = a.news_id").fetchall())
    assert "nan please" not in 점수 and 점수["Apple beats estimates"] > 0
