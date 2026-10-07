"""배치의 SQL 을 **실제 스키마에 대 본다** (docs/infra.md 25.61).

`migrations/` 를 그대로 올린 메모리 SQLite 에 배치·스크립트의 SQL 을 `EXPLAIN` 으로
컴파일해 본다. 값을 넣지 않아도 **없는 표·없는 열**은 그 자리에서 드러난다.

**왜 있나.** 열 이름을 바꾸는 마이그레이션은 그 열을 읽는 곳을 전부 따라 고쳐야 한다.
자주 도는 작업은 다음 날 아침에 깨져서 알게 되지만, **주 1회·월 1회 도는 작업**은 몇 주 뒤에
깨진다. 그때는 무엇을 고치다 그랬는지 아무도 기억하지 못한다. 게다가 지금은 Actions 가
멈춰 있어 아무것도 돌려 볼 수 없다(25.27) — 돌리지 않고 잡는 그물이 특히 값지다.

**글자만 본다.** `ast` 로 파일을 읽어 문자열 상수를 꺼낸다. 파이썬이 이웃한 문자열을
하나로 합쳐 주므로 `"SELECT …" "WHERE …"` 로 나눠 쓴 것도 온전히 잡힌다.

**못 보는 것도 세어 둔다.** f-string 으로 만드는 질의와, 변수를 끼워 넣느라 조각으로 끝나는
문자열은 컴파일되지 않는다. 그 수를 **상한으로 박아** 두었다 — 새로 늘면 테스트가 알려 주고,
그때 "정말 동적으로 만들어야 하나" 를 다시 묻게 된다.
"""

from __future__ import annotations

import ast
import contextlib
import re
import sqlite3
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
SQL시작 = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", re.I)
DDL시작 = re.compile(r"^\s*CREATE\s+(TABLE|INDEX|VIEW)\b", re.I)

#: 동적으로 만들어 **이 대조로는 못 보는** 질의의 상한.
#:
#: 2026-09-21 재실측: 조각으로 끝나는 것 **6**, f-string 55 — 합쳐 **61**.
#:
#: 처음에는 114 로 적었는데 **재는 쪽이 틀렸다.** `"SELECT …" f" WHERE {x}"` 한 덩이를
#: 조각과 f-string 양쪽에 **두 번 세고** 있었다(위 `꺼내기` 주석). 고치고 나니 조각은 6개뿐이고,
#: 이 그물은 배치·스크립트 SQL 202개 중 **196개**를 실제 스키마에 대 보고 있었다.
#: 못 본다고 여긴 것의 대부분은 사실 보고 있었다.
#:
#: 줄어드는 것은 좋은 일이라 상한만 본다. 늘리려면 그 질의를 정말 동적으로 만들어야 하는지
#: 먼저 따져 보라 — 못 보는 SQL 이 늘면 이 그물에 구멍이 늘어난다.
#: 2026-09-21 에 **62 로 한 칸 올렸다.** `jobs/daily.load_price_series` 의
#: `WHERE stock_id IN ({자리})` 하나다 (docs/signals.md 3.6 상관 기반 분산).
#: 목록의 길이가 보유 종목 수에 달렸으니 **정말로 동적이어야 한다** — 같은 꼴이
#: `adjust_kr`·`backtest`·`portfolio`·`stress`·`valuation_bands` 에 이미 있다.
#: 올리기 전에 따져 본 것을 적어 둔다: 나라 전체를 읽고 파이썬에서 거르는 길도 있지만
#: 900종목 × 126일을 읽게 되고, 자리 수를 90 으로 고정해도 f-string 은 그대로다.
#: 2026-09-23 에 **63 으로 한 칸 더 올렸다.** `move_user_data` 의
#: `SELECT … FROM settings WHERE key NOT IN ({자리})` 하나다 (docs/infra.md 25.156).
#: 자리 수가 `backup_db.설정_제외_사유` 의 길이라 목록이 늘면 따라 늘어난다 — 손으로
#: 박으면 목록과 갈라진다. 열쇠를 글자로 박는 길도 있지만 그것이 곧 두 벌이다(25.118).
#: 2026-09-23 에 **64 로 한 칸 더 올렸다.** `jobs/signals.load_metrics` 의
#: `WHERE m.window IN ({창자리})` 하나다 (docs/infra.md 25.176).
#: 자리 수가 `metrics.RISK_WINDOWS` 의 길이다. 예전에는 `IN ('3Y', '1Y')` 라고 박혀 있어
#: 이 그물이 볼 수 있었지만, 그 대가로 **창 목록이 두 곳에** 있었다 — `jobs/scores` 는
#: 이미 같은 꼴로 만들고 있었고 거기만 고치면 둘이 갈라진다.
#: 따져 본 것: 자리 수를 2로 고정하면 f-string 은 없어지지만 `RISK_WINDOWS` 의 길이를
#: 다시 박는 셈이고, 길이를 바꾸면 **조용히 반만 읽는다** — 가장 나쁜 실패다.
#: 2026-09-26 에 **65 로 한 칸 더 올렸다.** `jobs/signals.load_band` 의 가격 식을
#: `db.SPLIT_ONLY_PRICE_SQL`(분할만 반영한 가격, docs/infra.md 25.213)로 바꾼 것이다. 같은 식을
#: 포트폴리오 평가·밸류에이션 밴드 적재가 함께 쓴다 — 글자로 박으면 **세 벌**이 되고 한 곳만 고치면 갈라진다.
#: 나머지 두 자리는 이미 `IN ({자리})` 때문에 f-string 이라 늘지 않았다.
#: 2026-09-26 에 **66 으로 한 칸 더.** `jobs/monitor_targets._단위_비율` 도 같은 식을 쓴다(docs/infra.md 25.218).
#: 파이썬에 같은 판정을 한 벌 더 두는 것보다 식 하나를 가리키는 편이 낫다고 보았다.
#: 2026-09-26 에 **67 로 한 칸 더.** `jobs/daily._detect_kr_actions` 가 전날 행이 없는(정지) 종목의 마지막 종가를
#: `IN ({자리})` 로 읽는다(docs/infra.md 25.246). 종목 수만큼 자리가 바뀌므로 f-string 을 피할 수 없다.
#: 2026-09-26 에 **69 로 두 칸 더.** `jobs/earnings_calendar` 가 지우기·읽기를 **이번에 다룬 종목만**으로 좁혔다
#: (`stock_id IN ({자리})`, docs/infra.md 25.252). 종목 목록이 바뀌므로 f-string 을 피할 수 없다.
#: 2026-10-02 에 **70 으로 한 칸 더.** `core/price_replica._copy_stocks` 가 로컬 사본에 Turso `stocks` 의 열을
#: **그대로**
#: 만든다(docs/infra.md 25.888) — 열을 손으로 적으면 마이그레이션마다 사본이 어긋난다. 운영 DB 가 아니라 로컬 SQLite 다.
#: 같은 모듈의 Turso 질의 넷은 고정 문자열로 두었다(`json_each(?)` 로 종목 목록을 넘긴다)
#: 2026-10-04 에 **71 로 한 칸 더.** `scripts/restore_drill.drill` 의 `SELECT COUNT(*) FROM "{t}"` 하나다
#: (docs/infra.md 25.946).
#: 표 이름이 **백업 파일에서** 온다(그 백업이 담은 표마다 센다) — 코드가 고를 수 없어 정말로 동적이다. 운영 DB 가 아니라
#: 리허설용 로컬 SQLite 에만 쓰는 질의라 스키마 대조의 뜻도 없다
못보는것_상한 = 71


def 파일들() -> list[Path]:
    return sorted((뿌리 / "batch").rglob("*.py")) + sorted((뿌리 / "scripts").rglob("*.py"))


def 꺼내기() -> tuple[list[tuple[str, int, str]], list[tuple[str, int]], list[str]]:
    """(온전한 SQL, 동적이라 못 보는 것, 코드가 만드는 DDL).

    **한 질의를 두 번 세지 않는다** (2026-09-21 고침). `"SELECT …" f" WHERE … {x}"` 처럼
    상수와 f-string 을 이어 쓰면 파이썬이 통째로 `JoinedStr` 하나로 만드는데, 그 **안쪽**
    상수도 `SELECT` 로 시작한다. `ast.walk` 는 바깥과 안쪽을 다 훑으므로 예전에는 같은 질의가
    `동적` 에 한 번, `온전`(→ 조각) 에 한 번 들어가 **둘로 세어졌다.** 재는 자가 한 문제를
    둘로 보고하면 "못 보는 질의가 몇 개인가" 라는 숫자 자체를 믿을 수 없다.
    """
    온전, 동적, ddl = [], [], []
    for 길 in 파일들():
        이름 = str(길.relative_to(뿌리))
        나무 = ast.parse(길.read_text(encoding="utf-8"))
        # f-string 안쪽 상수는 바깥 JoinedStr 로 이미 세었으므로 건너뛴다
        안쪽 = {id(값) for 마디 in ast.walk(나무) if isinstance(마디, ast.JoinedStr) for 값 in 마디.values}
        for 마디 in ast.walk(나무):
            if isinstance(마디, ast.Constant) and isinstance(마디.value, str):
                if id(마디) in 안쪽:
                    continue
                if SQL시작.match(마디.value):
                    온전.append((이름, 마디.lineno, 마디.value))
                elif DDL시작.match(마디.value):
                    ddl.append(마디.value)
            elif isinstance(마디, ast.JoinedStr) and 마디.values:
                첫 = 마디.values[0]
                if isinstance(첫, ast.Constant) and isinstance(첫.value, str) and SQL시작.match(첫.value):
                    동적.append((이름, 마디.lineno))
    return 온전, 동적, ddl


온전한질의, 동적질의, 코드DDL = 꺼내기()


def 스키마올리기() -> sqlite3.Connection:
    """`migrations/` + **코드가 스스로 만드는 표**.

    `schema_migrations`(마이그레이션 이력)와 `step0_check`(점검 기록)은 마이그레이션 파일이
    아니라 코드가 `CREATE TABLE IF NOT EXISTS` 로 만든다. 그것들도 올려야 대조가 맞는다 —
    긁어서 올리므로 새 표가 같은 방식으로 생겨도 따라온다.
    """
    db = sqlite3.connect(":memory:")
    for f in sorted((뿌리 / "migrations").glob("*.sql")):
        db.executescript(f.read_text(encoding="utf-8"))
    for 문장 in 코드DDL:
        # 조각이거나 방언이 다르면 그냥 넘어간다. 여기서 못 만든 표는 아래에서 드러난다
        with contextlib.suppress(sqlite3.Error):
            db.executescript(문장)
    return db


스키마 = 스키마올리기()


def 판정(sql: str) -> tuple[str, str]:
    """(판정, 설명). 판정은 `맞음` · `없는것` · `조각` 셋 중 하나다.

    값을 안 넣어 나는 오류는 **컴파일에 성공했다는 뜻**이라 `맞음` 이다.
    """
    try:
        스키마.execute("EXPLAIN " + sql)
    except sqlite3.ProgrammingError:
        return "맞음", ""
    except sqlite3.OperationalError as error:
        말 = str(error)
        return ("없는것", 말) if "no such" in 말 else ("조각", 말)
    return "맞음", ""


def test_꺼내_냈다() -> None:
    """긁기가 조용히 0개를 내면 아래가 전부 공짜로 통과한다."""
    assert len(온전한질의) >= 180
    assert len({이름 for 이름, _, _ in 온전한질의}) >= 25


def test_코드가_만드는_표도_올렸다() -> None:
    표 = {r[0] for r in 스키마.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    assert "stocks" in 표 and "prices" in 표  # 마이그레이션
    assert "schema_migrations" in 표, "코드가 만드는 표를 못 올렸다 — 대조가 헛돈다"


@pytest.mark.parametrize(
    "이름,줄,sql", 온전한질의, ids=[f"{이름}:{줄}" for 이름, 줄, _ in 온전한질의]
)
def test_실제_스키마에_맞는다(이름: str, 줄: int, sql: str) -> None:
    결과, 설명 = 판정(sql)

    assert 결과 != "없는것", f"{이름}:{줄}\n{설명}\n{sql[:300]}"


def test_대조가_실제로_무엇인가를_보고_있다() -> None:
    """조각만 남고 온전한 것이 없으면 위 테스트가 전부 공짜로 통과한다."""
    맞은것 = [1 for _, _, sql in 온전한질의 if 판정(sql)[0] == "맞음"]

    # 2026-09-21 실측 196/202. 두 번 세던 것을 고치고 나서야 실제 값이 보였다
    assert len(맞은것) >= 190


def test_못_보는_질의가_늘지_않았다() -> None:
    """동적으로 만드는 질의는 이 그물의 구멍이다. 구멍이 커지는 것을 조용히 두지 않는다."""
    조각 = [이름 for 이름, _, sql in 온전한질의 if 판정(sql)[0] == "조각"]
    못봄 = len(동적질의) + len(조각)

    assert 못봄 <= 못보는것_상한, (
        f"동적으로 만드는 질의가 {못봄}개다 (상한 {못보는것_상한}, 조각 {len(조각)} + f-string {len(동적질의)}).\n"
        "정말 동적이어야 하는지 먼저 따져 보라 — 상한을 올리기 전에 고치는 쪽이 낫다."
    )
