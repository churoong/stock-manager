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


def test_국내_채점은_모델_판을_기사_행과_실행_기록에_남긴다(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
