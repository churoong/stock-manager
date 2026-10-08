"""일일 배치의 점수·신호가 시세 사본에서 읽는다 (docs/infra.md 25.1033).

사본이 틀리면 점수·신호가 **틀린 시세로** 계산된다. 그래서 "오늘 이 실행이 고친 시세까지 맞추는가" 와
"못 맞추면 Turso 로 가는가" 를 묶는다.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from batch.core import client as backend
from batch.core import db
from batch.core import price_replica as rep
from batch.jobs import daily
from tests.test_price_replica import _같나, _원격, 지금

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _깨끗한_손댐(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(db, "_가격_손댐", {})
    monkeypatch.delenv(rep.REPLICA_ENV, raising=False)
    monkeypatch.delenv(daily.REPLICA_FOR_SCORES_ENV, raising=False)


def _만든_사본(tmp_path: Path, 원격) -> Path:
    path = tmp_path / "prices.db"
    conn = rep.open_replica(path)
    assert rep.sync(원격, conn, backend="turso", allow_build=True, now=지금)["usable"]
    conn.close()
    return path


class Test같은_실행이_고친_시세:
    """시세를 넣은 실행은 아직 끝나지 않아 `batch_runs` 에 고친 범위가 없다 — `pending` 으로만 안다."""

    def _고치기(self, 원격) -> None:
        db.bulk_upsert_prices(원격, [(4, "2026-08-03", None, None, None, 999.0, 50.0, 10, 100, "KRW", "t", "t", None)])

    def test_pending_을_주면_다시_받는다(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)  # 표본 검증이 대신 고치지 않게
        원격 = _원격()
        conn = rep.open_replica(_만든_사본(tmp_path, 원격))
        self._고치기(원격)
        r = rep.sync(원격, conn, backend="turso", allow_build=False, now=지금 + timedelta(hours=1),
                     pending=db.prices_touched_summary())  # fmt: skip
        assert r["usable"] and r["repulled"] >= 1
        assert _같나(원격, conn)

    def test_pending_이_없으면_못_받는다(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """위 검사가 pending 덕에 통과한다는 것을 묶는다 — 없으면 고친 행을 놓친다."""
        monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
        원격 = _원격()
        conn = rep.open_replica(_만든_사본(tmp_path, 원격))
        self._고치기(원격)
        rep.sync(원격, conn, backend="turso", allow_build=False, now=지금 + timedelta(hours=1))
        assert not _같나(원격, conn)

    def test_전_종목이면_그_날부터_전부(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rep, "VERIFY_SAMPLE", 0)
        monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 0)
        monkeypatch.setattr(rep, "WIDE_RANGE_DAYS", 400)  # 고친 날(8월)이 넓은 범위로 비우는 문턱에 걸리지 않게
        원격 = _원격()
        conn = rep.open_replica(_만든_사본(tmp_path, 원격))
        self._고치기(원격)
        assert db.prices_touched_summary()["all"] is True
        rep.sync(원격, conn, backend="turso", allow_build=False, now=지금 + timedelta(hours=1),
                 pending=db.prices_touched_summary())  # fmt: skip
        assert _같나(원격, conn)

    def test_전_종목이_넓으면_비우고_Turso(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """오늘 수정주가로 먼 과거까지 고쳤으면 다시 받기가 새로 만들기만큼 크다 — 비우고 쓰지 않는다(25.907)."""
        monkeypatch.setattr(db, "PRICES_TOUCHED_MAX_STOCKS", 0)
        원격 = _원격()
        conn = rep.open_replica(_만든_사본(tmp_path, 원격))
        self._고치기(원격)
        r = rep.sync(원격, conn, backend="turso", allow_build=False, now=지금 + timedelta(hours=1),
                     pending=db.prices_touched_summary())  # fmt: skip
        assert not r["usable"]


class Test점수_신호_동안만_사본:
    def _원격_고정(self, monkeypatch: pytest.MonkeyPatch, 원격) -> None:
        monkeypatch.setattr(backend, "resolved_backend", lambda: backend.TURSO)
        monkeypatch.setattr(backend, "TursoClient", lambda *a, **k: 원격)
        monkeypatch.setattr(원격, "close", lambda: None, raising=False)

    def test_경로가_없으면_Turso(self) -> None:
        with daily._replica_for_scores() as 사본:
            assert 사본 is None
        assert rep.REPLICA_ENV not in os.environ

    def test_파일이_없으면_Turso(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(tmp_path / "없음.db"))
        with daily._replica_for_scores() as 사본:
            assert 사본 is None

    def test_맞추면_그_안에서만_사본_경로를_둔다(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        원격 = _원격()
        path = _만든_사본(tmp_path, 원격)
        self._원격_고정(monkeypatch, 원격)
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(path))
        with daily._replica_for_scores() as 사본:
            assert 사본 == str(path)
            assert os.environ[rep.REPLICA_ENV] == str(path)
            assert rep.usable_replica(path) is not None  # 방금 맞췄으니 ReplicaClient 가 연다
        assert rep.REPLICA_ENV not in os.environ  # 앞뒤 단계(시세 쓰기·리포트)는 사본을 보지 않는다

    def test_빈_사본은_만들지_않고_Turso(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "prices.db"
        rep.open_replica(path).close()
        self._원격_고정(monkeypatch, _원격())
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(path))
        with daily._replica_for_scores() as 사본:
            assert 사본 is None
        assert rep.REPLICA_ENV not in os.environ

    def test_D1_이면_Turso_와_같은_길(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _만든_사본(tmp_path, _원격())
        monkeypatch.setattr(backend, "resolved_backend", lambda: backend.D1)
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(path))
        with daily._replica_for_scores() as 사본:
            assert 사본 is None

    def test_맞추다_깨져도_배치는_계속(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = _만든_사본(tmp_path, _원격())
        monkeypatch.setattr(backend, "resolved_backend", lambda: backend.TURSO)

        def 깨짐(*a, **k):
            raise RuntimeError("Turso 끊김")

        monkeypatch.setattr(backend, "TursoClient", 깨짐)
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(path))
        with daily._replica_for_scores() as 사본:
            assert 사본 is None

    def test_원래_있던_경로는_되돌린다(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        원격 = _원격()
        path = _만든_사본(tmp_path, 원격)
        self._원격_고정(monkeypatch, 원격)
        monkeypatch.setenv(daily.REPLICA_FOR_SCORES_ENV, str(path))
        monkeypatch.setenv(rep.REPLICA_ENV, "앞서.db")
        with daily._replica_for_scores():
            pass
        assert os.environ[rep.REPLICA_ENV] == "앞서.db"


def test_점수_신호가_사본_안에서_돈다(monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh_recommendations` 가 점수·신호를 사본 문맥 안에서 부른다."""
    import contextlib

    본 = {}

    @contextlib.contextmanager
    def 가짜():
        os.environ[rep.REPLICA_ENV] = "x.db"
        try:
            yield "x.db"
        finally:
            os.environ.pop(rep.REPLICA_ENV, None)

    def 점수_신호(*a, **k):
        본["경로"] = os.environ.get(rep.REPLICA_ENV)
        return []

    monkeypatch.setattr(daily, "_replica_for_scores", 가짜)
    monkeypatch.setattr(daily, "_scores_and_signals", 점수_신호)
    src = Path(daily.__file__).read_text(encoding="utf-8")
    몸통 = src.split("def refresh_recommendations", 1)[1].split("\ndef ", 1)[0]
    assert "with _replica_for_scores() as" in 몸통 and "return _scores_and_signals(" in 몸통
    with daily._replica_for_scores():
        daily._scores_and_signals("KR", "2026-10-08", [], None, None)
    assert 본["경로"] == "x.db"


@pytest.mark.parametrize("name", ["daily-kr", "daily-us"])
def test_워크플로가_배치_전에_사본을_꺼내고_경로를_준다(name: str) -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows" / f"{name}.yml").read_text(encoding="utf-8"))
    steps = wf["jobs"]["run"]["steps"]
    names = [s.get("name", "") for s in steps]
    꺼내기 = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/cache/restore"))
    배치 = names.index("배치 실행")
    assert 꺼내기 < 배치
    assert steps[꺼내기]["with"]["path"] == ".price-replica"
    assert steps[배치]["env"][daily.REPLICA_FOR_SCORES_ENV] == ".price-replica/prices.db"
    넣기 = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/cache/save"))
    assert 넣기 > 배치  # 배치 안에서 맞춘 사본을 다음 실행에 넘긴다
