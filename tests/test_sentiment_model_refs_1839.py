"""국내 감성 채점은 받은 모델 판(HF 캐시 참조 → 커밋 sha)을 기사 행·실행 기록에 남긴다 (docs/infra.md 25.1839).

`from_pretrained` 가 리비전을 고정하지 않아 그날 `main` 설정과 변환 봇 PR(`refs/pr/1`) 가중치를 받는다 — 모델이 바뀌면
감성 점수가 소리 없이 달라져도 어느 판으로 매겼는지 몰랐다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch.jobs import sentiment as job
from tests.test_sentiment_job import MemClient


def _캐시(뿌리: Path) -> None:
    refs = 뿌리 / "models--amphora--KorFinASC-XLM-RoBERTa" / "refs"
    (refs / "refs" / "pr").mkdir(parents=True)
    (refs / "main").write_text("abf064de8f467cc872c78c5b845f179d787da6be", encoding="utf-8")
    (refs / "refs" / "pr" / "1").write_text("1234567890abcdef1234567890abcdef12345678\n", encoding="utf-8")


def test_캐시_참조를_읽고_없으면_빈_값(tmp_path: Path) -> None:
    _캐시(tmp_path)
    refs = job.hf_cache_refs(job.KORFINASC_MODEL, str(tmp_path))
    assert refs == {"main": "abf064de8f467cc872c78c5b845f179d787da6be",
                    "refs/pr/1": "1234567890abcdef1234567890abcdef12345678"}  # fmt: skip
    assert job.refs_text(refs) == "main@abf064de8f46 refs/pr/1@1234567890ab"
    assert job.hf_cache_refs(job.KORFINASC_MODEL, str(tmp_path / "없음")) == {}


def test_국내_채점은_모델_판을_기사_행과_실행_기록에_남긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from batch.core import db

    _캐시(tmp_path)
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "korfinasc_scorer", lambda: lambda pairs: [(0.5, {"positive": 0.6}) for _ in pairs])
    진짜 = job.hf_cache_refs
    monkeypatch.setattr(job, "hf_cache_refs", lambda repo: 진짜(repo, str(tmp_path)))
    monkeypatch.setattr("importlib.metadata.version", lambda name: "5.17.0")
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
    assert job.run("KR", "2026-09-17", score=True) == 0
    판 = c.execute("SELECT method_version FROM article_sentiments").fetchone()[0]
    assert 판 == (f"{job.KORFINASC_MODEL} main@abf064de8f46 refs/pr/1@1234567890ab transformers 5.17.0")
    기록 = json.loads(c.execute("SELECT step_log FROM batch_runs ORDER BY id DESC").fetchone()[0])
    assert 기록["model_refs"]["refs/pr/1"].startswith("1234567890ab")
    출력 = capsys.readouterr().out
    assert "모델 판: main@abf064de8f467cc872c78c5b845f179d787da6be refs/pr/1@" in 출력  # 고정할 sha 40자
    assert "창 안 미채점" not in 출력  # 25.1843 — 다 채점된 날엔 미채점 줄이 없다


def test_캐시를_읽다_실패해도_채점은_저장된다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """25.1841 (25.1839 교차검증 [권고]) — `hf_cache_refs` 는 채점 뒤·저장 전에 불린다. 예외가 새면 그날 국내 채점이
    통째로 저장되지 않는다(예전 시험은 except 를 지나지 않았다)"""
    from batch.core import db

    _캐시(tmp_path)

    def 터짐(self: Path, *a: object, **k: object) -> str:
        raise PermissionError("읽기 금지")

    with monkeypatch.context() as m:
        m.setattr(Path, "read_text", 터짐)
        with caplog.at_level("WARNING", logger="sentiment"):
            assert job.hf_cache_refs(job.KORFINASC_MODEL, str(tmp_path)) == {}
    assert "모델 판(HF 캐시 참조)을 읽지 못함: PermissionError" in caplog.text  # 25.1842 — 면제 사유의 경고 로그
    # 기본 경로(huggingface_hub 를 부름)도 — 못 불러오면 빈 값. 아래 실행도 이 길로 판 없이 저장한다
    monkeypatch.setitem(__import__("sys").modules, "huggingface_hub", None)
    assert job.hf_cache_refs(job.KORFINASC_MODEL) == {}
    mem = MemClient()
    monkeypatch.setattr(job, "TursoClient", lambda: mem)
    monkeypatch.setattr(job, "korfinasc_scorer", lambda: lambda pairs: [(0.5, {"positive": 0.6}) for _ in pairs])
    monkeypatch.setattr("importlib.metadata.version", lambda name: "5.17.0")
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
    assert job.run("KR", "2026-09-17", score=True) == 0
    assert c.execute("SELECT method_version FROM article_sentiments").fetchone()[0] == (
        f"{job.KORFINASC_MODEL} transformers 5.17.0"
    )
