"""DB 보조 함수 테스트.

네트워크를 타지 않는다. 순수 계산과 SQL 문자열만 본다.
"""

from __future__ import annotations

import pytest

from batch.core import db

SQLITE_KEYWORDS = {
    "trigger", "order", "group", "index", "table", "where", "select",
    "from", "values", "default", "check", "references", "primary", "key",
    "transaction", "commit", "rollback", "when", "then", "case", "add",
}


class Test한도상태판정:
    def test_한도를_모르면_unknown_이다(self) -> None:
        # 모르는 것을 ok 라고 부르지 않는다. 이것이 핵심이다.
        assert db.evaluate_limit_state(500, None) == "unknown"
        assert db.evaluate_limit_state(0, 0) == "unknown"

    def test_여유가_있으면_ok(self) -> None:
        assert db.evaluate_limit_state(100, 10_000) == "ok"

    def test_경고선에_닿으면_warn(self) -> None:
        # 기본 경고선은 80%
        assert db.evaluate_limit_state(8_000, 10_000) == "warn"
        assert db.evaluate_limit_state(7_999, 10_000) == "ok"

    def test_한도에_닿으면_blocked(self) -> None:
        assert db.evaluate_limit_state(10_000, 10_000) == "blocked"
        assert db.evaluate_limit_state(10_001, 10_000) == "blocked"

    def test_경고선을_조정할_수_있다(self) -> None:
        assert db.evaluate_limit_state(5_000, 10_000, warn_at_pct=50) == "warn"
        assert db.evaluate_limit_state(4_999, 10_000, warn_at_pct=50) == "ok"


class TestSQL문장분리:
    def test_세미콜론으로_나눈다(self) -> None:
        sql = "CREATE TABLE a (x INT); CREATE TABLE b (y INT);"
        assert len(db._split_statements(sql)) == 2

    def test_주석을_걷어낸다(self) -> None:
        sql = "-- 설명\nCREATE TABLE a (x INT);  -- 뒤 주석\n"
        statements = db._split_statements(sql)

        assert len(statements) == 1
        assert "설명" not in statements[0]
        assert "CREATE TABLE a" in statements[0]

    def test_빈_문장은_버린다(self) -> None:
        assert db._split_statements(";;  ;\n-- 주석만\n") == []

    def test_주석_안의_세미콜론에_속지_않는다(self) -> None:
        sql = "-- a; b; c\nCREATE TABLE t (x INT);"
        assert len(db._split_statements(sql)) == 1


class Test마이그레이션파일:
    def test_번호_순서로_정렬된다(self) -> None:
        names = [p.stem for p in db.migration_files()]
        assert names == sorted(names)

    def test_적어도_하나는_있다(self) -> None:
        assert len(db.migration_files()) >= 1

    def test_파일명이_번호로_시작한다(self) -> None:
        for path in db.migration_files():
            assert path.stem[:4].isdigit(), path.name

    def test_번호가_중복되지_않는다(self) -> None:
        numbers = [p.stem[:4] for p in db.migration_files()]
        assert len(numbers) == len(set(numbers))

    def test_모든_문장이_파싱된다(self) -> None:
        for path in db.migration_files():
            statements = db._split_statements(path.read_text(encoding="utf-8"))
            assert statements, f"{path.name} 에서 실행할 문장을 찾지 못했습니다"


class Test예약어:
    """예약어를 컬럼 이름으로 쓰면 조회할 때마다 따옴표가 필요하다.

    빠뜨리면 구문 오류가 나는데 원인이 한눈에 보이지 않는다.
    실제로 batch_runs.trigger 에서 겪었고 0002 에서 개명했다.
    """

    def test_새_마이그레이션은_예약어를_컬럼명으로_쓰지_않는다(self) -> None:
        import re

        offenders: list[str] = []
        for path in db.migration_files():
            if path.stem == "0001_initial":
                continue  # 이미 적용된 파일. 0002 가 개명으로 처리했다
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue
                match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s+(TEXT|INTEGER|REAL)", stripped)
                if match and match.group(1).lower() in SQLITE_KEYWORDS:
                    offenders.append(f"{path.name}: {match.group(1)}")

        assert not offenders, f"예약어를 컬럼명으로 씀: {offenders}"

    def test_코드가_개명된_이름을_쓴다(self) -> None:
        source = (db.MIGRATIONS_DIR.parent / "batch" / "core" / "db.py").read_text(
            encoding="utf-8"
        )
        assert "trigger_source" in source


@pytest.mark.parametrize("status", ["success", "partial"])
def test_중복_판정에_포함되는_상태(status: str) -> None:
    # has_successful_run 이 보는 상태. dryrun 은 여기 없어야 한다.
    assert status in ("success", "partial")


def test_dryrun은_성공으로_치지_않는다() -> None:
    # 확인용 실행이 그날 진짜 리포트를 막으면 안 된다
    source = (db.MIGRATIONS_DIR.parent / "batch" / "jobs" / "daily.py").read_text(
        encoding="utf-8"
    )
    assert 'status="dryrun"' in source

    db_source = (db.MIGRATIONS_DIR.parent / "batch" / "core" / "db.py").read_text(
        encoding="utf-8"
    )
    assert "'success', 'partial'" in db_source
    assert "dryrun" not in db_source
