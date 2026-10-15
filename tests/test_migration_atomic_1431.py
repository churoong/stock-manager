"""Turso 마이그레이션은 한 트랜잭션으로 — 중간 문장이 실패하면 앞 문장도 적용 기록도 남지 않는다 (docs/infra.md 25.1431)."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from batch.core import db, turso


def _값(v: dict) -> Any:
    return None if v.get("type") == "null" else (int(v["value"]) if v.get("type") == "integer" else v.get("value"))


class _가짜Hrana:
    """Hrana batch 의미를 sqlite 로 흉내낸다 — 조건이 거짓이면 그 단계를 건너뛰고(결과·오류 모두 null), 오류는 그 단계에만"""

    def __init__(self, begin_ok: bool = True) -> None:
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.begin_ok = begin_ok
        self.pipelines: list[list[str]] = []

    def _cond(self, c: dict | None, ok: list[bool | None]) -> bool:
        if c is None:
            return True
        if c["type"] == "ok":
            return ok[c["step"]] is True
        if c["type"] == "not":
            return not self._cond(c["cond"], ok)
        raise AssertionError(c)

    def post(self, payload: dict) -> Any:
        req = payload["requests"][0]
        if req["type"] == "execute":  # 예전 파이프라인 — 문장마다 커밋, 실패 뒤도 계속(나쁜 쪽 가정)
            out = []
            for r in payload["requests"]:
                if r["type"] != "execute":
                    continue
                try:
                    cur = self.con.execute(r["stmt"]["sql"], [_값(a) for a in r["stmt"].get("args", [])])
                    cols = [{"name": d[0]} for d in (cur.description or [])]
                    rows = [[{"type": "text", "value": str(v)} for v in row] for row in cur.fetchall()]
                    out.append({"type": "ok", "response": {"type": "execute", "result": {"cols": cols, "rows": rows}}})
                except sqlite3.Error as e:
                    out.append({"type": "error", "error": {"message": str(e)}})
            return _Resp({"results": out})
        steps = req["batch"]["steps"]
        ok: list[bool | None] = []
        results: list[dict | None] = []
        errors: list[dict | None] = []
        for st in steps:
            if not self._cond(st.get("condition"), ok):
                ok.append(None), results.append(None), errors.append(None)
                continue
            sql = st["stmt"]["sql"]
            try:
                if sql == "BEGIN" and not self.begin_ok:
                    raise sqlite3.OperationalError("BEGIN 불가")
                self.con.execute(sql, [_값(a) for a in st["stmt"].get("args", [])])
                ok.append(True), results.append({"cols": [], "rows": []}), errors.append(None)
            except sqlite3.Error as e:
                ok.append(False), results.append(None), errors.append({"message": str(e)})
        body = {"type": "ok", "response": {"type": "batch", "result": {"step_results": results, "step_errors": errors}}}
        return _Resp({"results": [body, {"type": "ok", "response": {"type": "close"}}]})


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


def _클라(h: _가짜Hrana, monkeypatch: pytest.MonkeyPatch) -> turso.TursoClient:
    c = turso.TursoClient("libsql://example.turso.io", "token")
    monkeypatch.setattr(c, "_post_with_retry", h.post)
    monkeypatch.setattr(c, "_flush_writes", lambda *a: None)
    monkeypatch.setattr(c, "_flush_reads", lambda *a: None)
    return c


def test_중간_문장이_실패하면_전부_되돌린다(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _가짜Hrana()
    c = _클라(h, monkeypatch)
    h.con.execute("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    with pytest.raises(turso.TursoError, match="되돌림"):
        c.batch_atomic([("CREATE TABLE a (x)", []), ("CREATE INDEX i ON a (없는칸)", []),
                        ("INSERT INTO schema_migrations VALUES (?, ?)", ["0099_x", "t"])])  # fmt: skip
    assert h.con.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchall() == []
    assert h.con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    # 성공하면 다 들어간다
    assert len(c.batch_atomic([("CREATE TABLE b (x)", []), ("INSERT INTO b VALUES (?)", [1])])) == 2
    assert h.con.execute("SELECT x FROM b").fetchall() == [(1,)]


def test_BEGIN_을_받지_않으면_예전_방식으로_물러난다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    h = _가짜Hrana(begin_ok=False)
    c = _클라(h, monkeypatch)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x);\n", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    assert db.apply_migrations(c) == ["0001_a"]
    assert h.con.execute("SELECT version FROM schema_migrations").fetchall() == [("0001_a",)]


def test_마이그레이션이_실패하면_기록이_남지_않는다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """감사 재현 — 예전엔 Hrana 가 실패 뒤를 계속 실행하면 적용 기록까지 커밋돼 그 파일이 영영 다시 돌지 않았다"""
    h = _가짜Hrana()
    c = _클라(h, monkeypatch)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x);\nCREATE INDEX i ON a (없는칸);\n", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(turso.TursoError):
        db.apply_migrations(c)
    assert h.con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    assert h.con.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchall() == []


def test_단계_모양() -> None:
    steps = turso.atomic_steps([("A", []), ("B", [1])])
    assert [s["stmt"]["sql"] for s in steps] == ["BEGIN", "A", "B", "COMMIT", "ROLLBACK"]
    assert steps[2]["condition"] == {"type": "ok", "step": 1}
    assert steps[4]["condition"] == {"type": "not", "cond": {"type": "ok", "step": 3}}


def test_응답이_낯설어도_적용됐으면_다시_돌리지_않는다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    h = _가짜Hrana()
    c = _클라(h, monkeypatch)
    원래 = h.post

    def 낯선(payload: dict) -> Any:
        r = 원래(payload)
        if payload["requests"][0]["type"] == "batch":
            return _Resp({"results": [{"type": "ok", "response": {"type": "batch", "result": {}}}]})
        return r

    monkeypatch.setattr(c, "_post_with_retry", 낯선)
    (tmp_path / "0001_a.sql").write_text("ALTER TABLE t ADD COLUMN y;\n", encoding="utf-8")
    h.con.execute("CREATE TABLE t (x)")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    assert db.apply_migrations(c) == ["0001_a"]  # 두 번 돌았으면 duplicate column 으로 깨졌다
    assert h.con.execute("SELECT version FROM schema_migrations").fetchall() == [("0001_a",)]


def test_요청_오류면_예전_방식으로_물러나지_않는다(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """sqld 는 batch 안 문장의 파싱 오류를 요청 오류로 준다 — 물러나면 예전 파이프라인이 끝까지 실행해 기록까지 남는다 (25.1432)"""
    h = _가짜Hrana()
    c = _클라(h, monkeypatch)
    원래 = h.post

    def 파싱오류(payload: dict) -> Any:
        if payload["requests"][0]["type"] == "batch":
            return _Resp({"results": [{"type": "error", "error": {"message": "SQL_PARSE_ERROR", "code": "SQL_PARSE_ERROR"}}]})
        return 원래(payload)

    monkeypatch.setattr(c, "_post_with_retry", 파싱오류)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x);\nCREATE INDEX i ON a (없는칸);\n", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    with pytest.raises(turso.TursoError, match="실행 안 됨"):
        db.apply_migrations(c)
    assert h.con.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    assert h.con.execute("SELECT name FROM sqlite_master WHERE name = 'a'").fetchall() == []


def test_재시도가_중복으로_깨져도_기록이_있으면_적용됨(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    h = _가짜Hrana()
    c = _클라(h, monkeypatch)
    원래 = h.post

    def 응답잃음(payload: dict) -> Any:
        if payload["requests"][0]["type"] == "batch":
            원래(payload)  # 커밋은 됐다
            return 원래(payload)  # 재시도 — 중복으로 실패하고 되돌린다
        return 원래(payload)

    monkeypatch.setattr(c, "_post_with_retry", 응답잃음)
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x);\n", encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", tmp_path)
    assert db.apply_migrations(c) == ["0001_a"]
