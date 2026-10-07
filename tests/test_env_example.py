"""`.env.example` 이 코드와 **같은 것을 말하는가** (docs/infra.md 25.160).

CLAUDE.md 의 절대 규칙 중 둘이 이 파일에 걸려 있다.

> API 키는 GitHub Actions 시크릿과 Vercel 환경변수에만 저장. **하드코딩 금지**.
> 로컬 개발용 **.env.example을 항상 최신으로 유지**한다

"최신" 을 지키는 장치가 없었다. 그래서 어긋난 채로 넉 달이 갔다 — `FRED_API_KEY` 와
`ECOS_API_KEY` 는 **아무 코드도 읽지 않는데** 받으라고 적혀 있었다(25.160).

이 그물은 **양쪽으로** 본다. 한 방향만 걸면 틀리는 쪽은 언제나 안 건 쪽이다
(25.0 「그물이 한 방향만 본다」).

  1. 코드가 읽는 이름이 예시에 있는가 — 없으면 받는 사람이 무엇을 채울지 모른다
  2. 예시의 이름을 코드가 읽는가 — 안 읽으면 **쓰지도 않을 키를 받으러 가게** 된다
  3. 비밀처럼 생긴 값이 저장소 안에 적혀 있지 않은가
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

뿌리 = Path(__file__).resolve().parent.parent
예시길 = 뿌리 / ".env.example"

#: 예시에 **없어도 되는** 이름. 사람이 채우는 값이 아니라 플랫폼이 넣어 준다
플랫폼이_주는_것 = {
    "VERCEL_ENV": "Vercel 이 배포마다 넣는다(production·preview·development) — 텔레그램 웹훅 켜기를 운영 배포로 막는다 (25.1007)",
    "GITHUB_ACTIONS": "러너가 항상 넣는다. Actions 안인지 밖인지 가리는 데 쓴다",
    "GITHUB_EVENT_NAME": "무엇이 이 실행을 깨웠는가 (schedule · repository_dispatch …)",
    "GITHUB_OUTPUT": "스텝끼리 값을 넘기는 파일 경로. 러너가 만든다",
    "GITHUB_REPOSITORY": "owner/repo. 실행 주소를 만들 때 쓴다",
    "GITHUB_RUN_ID": "실행 번호. 실행 주소를 만들 때 쓴다",
    "GITHUB_SERVER_URL": "https://github.com. 자체 호스팅이면 달라진다",
    "GITHUB_TOKEN": "워크플로가 `${{ github.token }}` 으로 넘긴다. 사람이 발급하지 않는다",
    "RUNNER_OS": "Linux · macOS · Windows. 러너가 넣는다",
    "NODE_ENV": "Next.js 가 정한다 (development · production)",
    "STEPS_JSON": "워크플로가 같은 실행 안에서 만들어 넘기는 값. 비밀이 아니다",
    "PATH": "운영체제가 준다. 셸을 부르는 테스트가 이것을 손보고 되돌린다",
}

#: 예시에 **있는데 아무 코드도 안 읽는** 이름. 지우지 않는 이유를 함께 적는다.
#: 사유 없이 남으면 "받아 두라" 는 말만 남고 왜 받는지는 사라진다
아직_안_읽는_것 = {
    "FRED_API_KEY": "미국 무위험수익률을 FRED 에서 받기로 했다가 Step 5 를 **설정 화면 수동"
    " 입력**으로 마쳤다(2026-09-23 확인, docs/infra.md 25.160). 받아 오는 코드가 없다."
    " 키는 이미 발급돼 있고 시리즈(DTB3·TB3MS)도 정해져 있어 칸만 남겨 둔다",
    "ECOS_API_KEY": "한국은행 ECOS. 무료 한도가 확인되지 않아 **사용 보류**다"
    " (CLAUDE.md 비용 규칙). 보류의 기록으로 칸을 남긴다",
}

#: 비밀이 들어갈 자리. 예시 파일에서는 **반드시 비어 있어야** 한다
비밀_냄새 = ("TOKEN", "KEY", "SECRET", "PASSWORD", "PASSWD")

#: 환경변수를 읽는 모든 길. 파이썬은 `batch.config` 를 거치고 웹은 `process.env` 를 쓴다
_읽는_무늬 = re.compile(
    r"""(?:os\.environ(?:\.get)?[\[(]
        |os\.getenv\(
        |config\.get(?:_bool)?\(
        |config\.require\(
        |(?<![\w.])get(?:_bool)?\(
        |(?<![\w.])require\()\s*["']([A-Z][A-Z0-9_]+)["']
      |process\.env\.([A-Z][A-Z0-9_]+)
      |process\.env\[\s*["']([A-Z][A-Z0-9_]+)["']""",
    re.X,
)

#: 비밀을 코드에 적은 것처럼 보이는 줄.
#: **한글 이름도 본다.** 이 저장소는 변수를 한글로 짓는다 — 영어 단어만 찾으면
#: `시험용_토큰 = "…"` 이 그물을 그냥 지나간다 (25.153 에서 똑같이 당했다)
_박힌_무늬 = re.compile(
    r"""(?i)(?<![\w가-힣])([A-Za-z_가-힣][\w가-힣.]*
        (?:token|api_?key|secret|password|passwd|auth|토큰|비밀번호|암호|인증키|_키)[\w가-힣]*)
        \s*[:=]\s*["']([^"']{12,})["']""",
    re.X,
)

_훑을_자리 = ("batch", "scripts", "web/lib", "web/app", "web/components", "tests")
_확장자 = (".py", ".ts", ".tsx")


def _소스들(자리들: tuple[str, ...] = _훑을_자리, 확장자: tuple[str, ...] = _확장자) -> list[Path]:
    나온것: list[Path] = []
    for 자리 in 자리들:
        길 = 뿌리 / 자리
        if 길.is_file():
            나온것.append(길)
            continue
        나온것 += [
            f
            for f in sorted(길.rglob("*"))
            if f.is_file() and f.suffix in 확장자 and "node_modules" not in f.parts
        ]
    return 나온것


def _읽는_이름들() -> dict[str, set[str]]:
    본것: dict[str, set[str]] = {}
    for 길 in _소스들():
        글 = 길.read_text(encoding="utf-8", errors="ignore")
        for m in _읽는_무늬.finditer(글):
            이름 = next(x for x in m.groups() if x)
            본것.setdefault(이름, set()).add(str(길.relative_to(뿌리)))
    return 본것


def _예시의_이름들() -> list[str]:
    글 = 예시길.read_text(encoding="utf-8")
    return [m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]+)=", 글, re.M)]


def test_읽어_냈다() -> None:
    """훑기가 조용히 비면 아래가 전부 공짜로 통과한다 (25.0 「그물이 …」)."""
    assert len(_소스들()) > 100, "소스를 너무 적게 찾았다 — 훑는 자리가 바뀌었다"
    assert len(_예시의_이름들()) > 15, ".env.example 에서 이름을 못 읽었다"
    assert len(_읽는_이름들()) > 15, "환경변수를 읽는 자리를 못 찾았다 — 읽는 길이 바뀌었나"


def test_코드가_읽는_이름이_예시에_있다() -> None:
    """없으면 **받는 사람이 무엇을 채울지 모른다.** 배치는 그때서야 빈 값으로 죽는다."""
    있는것 = set(_예시의_이름들())
    읽는것 = _읽는_이름들()
    빠진것 = sorted(set(읽는것) - 있는것 - set(플랫폼이_주는_것))

    assert not 빠진것, (
        "코드가 읽는데 .env.example 에 없는 환경변수: "
        + ", ".join(f"{n}({sorted(읽는것[n])[0]})" for n in 빠진것)
        + "\n.env.example 에 칸을 만들거나, 플랫폼이 주는 값이면 `플랫폼이_주는_것` 에 사유와 함께 넣어라"
    )


def test_예시의_이름을_코드가_읽는다() -> None:
    """**쓰지도 않을 키를 받으러 가게 하지 않는다.**

    FRED 가 그랬다 — 계정을 만들고 키를 받고 워크플로에 시크릿까지 꽂았는데
    받아 오는 코드가 한 줄도 없었다 (25.160).
    """
    읽는것 = set(_읽는_이름들())
    고아 = [n for n in _예시의_이름들() if n not in 읽는것 and n not in 아직_안_읽는_것]

    assert not 고아, (
        f".env.example 에 있는데 아무 코드도 읽지 않는 환경변수: {고아}\n"
        "지우거나, 남겨 둘 이유를 `아직_안_읽는_것` 에 적어라"
    )


def test_안_읽는_목록이_낡지_않았다() -> None:
    """붙이고 나서 목록을 안 지우면, 다음 사람이 **또 안 붙은 줄** 안다."""
    읽는것 = set(_읽는_이름들())
    이제_읽는것 = sorted(set(아직_안_읽는_것) & 읽는것)

    assert not 이제_읽는것, (
        f"이제 코드가 읽는데 '아직 안 읽는' 목록에 남아 있다: {이제_읽는것} — 목록에서 지워라"
    )


def test_플랫폼_목록이_낡지_않았다() -> None:
    겹침 = sorted(set(플랫폼이_주는_것) & set(_예시의_이름들()))
    assert not 겹침, f"플랫폼이 준다고 해 놓고 예시에도 있다: {겹침} — 한쪽을 지워라"


@pytest.mark.parametrize("표이름", ["플랫폼이_주는_것", "아직_안_읽는_것"])
def test_목록마다_사유가_있다(표이름: str) -> None:
    """이름만 있는 예외는 **다음 사람이 지워도 되는지 모른다.**"""
    표 = {"플랫폼이_주는_것": 플랫폼이_주는_것, "아직_안_읽는_것": 아직_안_읽는_것}[표이름]
    짧은것 = [k for k, v in 표.items() if len(v.strip()) < 20]
    assert not 짧은것, f"{표이름} 의 사유가 너무 짧다: {짧은것}"


def test_예시에는_비밀값이_없다() -> None:
    """이 파일은 **커밋된다.** 값이 들어가면 그 순간 이력에 박힌다 (25.159)."""
    적힌것 = []
    for 줄 in 예시길.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]+)=(.*)$", 줄)
        if m and m.group(2).strip() and any(냄새 in m.group(1) for 냄새 in 비밀_냄새):
            적힌것.append(m.group(1))

    assert not 적힌것, (
        f".env.example 에 값이 적힌 비밀이 있다: {적힌것}"
        " — 이 파일은 커밋되므로 이름만 두고 값은 비운다"
    )


def test_비밀을_코드에_적지_않았다() -> None:
    """CLAUDE.md: "API 키는 … 시크릿과 환경변수에만 저장. **하드코딩 금지**".

    `tests/` 는 뺀다 — 가짜 토큰을 일부러 적는 자리다.
    """
    자리 = ("batch", "scripts", "web/lib", "web/app", "web/components", ".github/workflows")
    적힌것 = []
    for 길 in _소스들(자리, (".py", ".ts", ".tsx", ".yml")):
        for 번호, 줄 in enumerate(길.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            m = _박힌_무늬.search(줄)
            if not m:
                continue
            값 = m.group(2)
            # 환경변수에서 읽어 오는 줄과 워크플로의 `${{ secrets.X }}` 는 비밀이 아니다
            if any(표시 in 줄 for 표시 in ("${{", "process.env", "os.environ", "os.getenv", "config.")):
                continue
            적힌것.append(f"{길.relative_to(뿌리)}:{번호}: {m.group(1)} = {값[:20]!r}…")

    assert not 적힌것, "비밀이 코드에 적혀 있다:\n  " + "\n  ".join(적힌것)
