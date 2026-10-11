"""공개 소형 언어모델이 DART 사업보고서를 쓸 만큼 읽는지 시험한다 (docs/infra.md 25.1073).

**무엇을 재나.** 사업보고서 "매출 및 수주상황"·"원재료" 절에서 실명 매출처·매입처를 뽑게 하고
1. 근거율 — 뽑은 회사명과 근거 문장이 원문에 글자 그대로 있나(지어내기 검사, 정답 없이 기계로 잰다)
2. 익명률 — 원문이 거래처를 "A사"·"해외 고객사" 로만 적는 비율(공급망 지도가 얼마나 채워질지)
3. 속도 — 문서 하나에 몇 초(Actions 무료 러너 4코어 CPU)
재현율(놓친 회사)은 이 출력의 원문 창을 사람이 읽어 정답을 매긴 뒤 따로 잰다.

**왜 Actions 인가.** 클라우드 세션 프록시가 DART·HuggingFace 를 막는다(2026-10-09 확인, 403).
공개 저장소라 Actions 는 무료다. 출력은 공시 원문과 모델 답뿐이라 개인 정보가 없지만, 규칙대로
`ops_tee.py` 를 거쳐 비공개 운영 저장소 이슈로만 낸다. 인증키는 찍지 않는다.

**운영에 쓰지 않는다.** 시험 결과가 기준(docs/infra.md 25.1073)을 넘을 때만 수집기를 따로 만든다.

실행
  DART_API_KEY=... python scripts/probe_llm_filings.py --out probe-out
"""

from __future__ import annotations

import argparse
import html
import io
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import requests

DART = "https://opendart.fss.or.kr/api"

#: 시험 종목 — 공급망이 뚜렷한 대형·중형 제조사. 사용자 보유와 무관하게 골랐다
TICKERS = (
    "005930", "000660", "373220", "012330", "006400", "011070",
    "042700", "247540", "003670", "009150", "066970", "240810",
)  # fmt: skip

#: 2회차 종목 (docs/infra.md 25.1131) — 1회차 정답을 보고 장치를 고쳤으므로 **같은 회사로 다시 재면 과적합**이다.
#: 1회차와 겹치지 않는 제조사 12곳(공급망을 공시에 적는 편인 업종 위주, 사용자 보유와 무관).
#: 워크플로 `tickers` 입력으로 넘긴다
TICKERS_R2 = (
    "005380", "066570", "051910", "012450", "267260", "034020",
    "058470", "007660", "357780", "005290", "353200", "011790",
)  # fmt: skip

#: 모델 후보 — 모두 Apache-2.0 (Qwen3). 앞에서부터 받아지는 것을 쓴다. 저장소 이름은 [확인필요]라 여럿 둔다
MODELS = {
    "qwen3-4b": (
        "Qwen/Qwen3-4B-Instruct-2507-GGUF",
        "unsloth/Qwen3-4B-Instruct-2507-GGUF",
        "lmstudio-community/Qwen3-4B-Instruct-2507-GGUF",
        "Qwen/Qwen3-4B-GGUF",
    ),
    "qwen3-1.7b": ("Qwen/Qwen3-1.7B-GGUF", "unsloth/Qwen3-1.7B-GGUF"),
    # 4B 가 익명 "A사" 를 고객으로 뽑았다(25.1074 핑) — 러너 램 16GB 에 드는 한 단계 큰 모델 (Q4 약 5GB)
    "qwen3-8b": ("unsloth/Qwen3-8B-GGUF", "Qwen/Qwen3-8B-GGUF"),
}

#: 절 제목에 이 말이 있으면 읽힌다 — 사업보고서 서식 "II. 사업의 내용" 의 하위 절
SECTION_KEYS = ("매출 및 수주", "원재료")
#: 창을 고를 때 붙잡는 말. 거래처가 적히는 문단만 남겨 문맥 길이를 줄인다
#: 2회차에 넓혔다(25.1131) — 1회차 원재료 절 여섯 곳의 창이 0~53자였다(표 머리에 "매입처" 가 없고 "공급받" 만 있는 등)
WINDOW_KEYS = ("매출처", "매입처", "고객", "거래처", "공급", "납품", "구매처", "매입", "조달", "협력사", "구입")
#: 모델에 주는 원문 상한(글자). 한국어는 대략 글자당 토큰 1 안팎 [확인필요] — n_ctx 8192 안에 들게
WINDOW_CHARS = 6000

_SECTION_OPEN = re.compile(r"<(SECTION-\d)\b[^>]*>")
_TITLE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.DOTALL)


def to_text(xml: str) -> str:
    """DART 문서 XML 조각을 사람이 읽는 글로. 표는 칸을 ' | ', 줄을 줄바꿈으로 둔다."""
    s = re.sub(r"</(TD|TH|TE|TU)>", " | ", xml)
    s = re.sub(r"</TR>|<P\b[^>]*>|</P>|<BR\s*/?>|</TITLE>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t ]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def sections(xml: str, keys: tuple[str, ...] = SECTION_KEYS) -> dict[str, str]:
    """제목에 keys 중 하나가 든 절을 통째로(하위 절 포함) 글로 돌려준다.

    제목 바로 앞에 열린 SECTION-k 를 찾고, 그 뒤 첫 </SECTION-k> 까지가 그 절이다 — 같은 깊이의
    절은 자기 안에 다시 들어가지 않는다.
    """
    out: dict[str, str] = {}
    for m in _TITLE.finditer(xml):
        title = to_text(m.group(1))
        key = next((k for k in keys if k in title), None)
        if key is None or key in out:
            continue
        opens = list(_SECTION_OPEN.finditer(xml, 0, m.start()))
        if not opens:
            continue
        tag = opens[-1].group(1)
        end = xml.find(f"</{tag}>", m.end())
        out[key] = to_text(xml[opens[-1].start() : end if end >= 0 else len(xml)])
    return out


def window(text: str, limit: int = WINDOW_CHARS, keys: tuple[str, ...] = WINDOW_KEYS) -> str:
    """거래처 낱말이 든 줄과 그 앞뒤 한 줄만 남긴다. 상한을 넘으면 앞에서부터 자른다.

    낱말 줄이 하나도 없으면 **절 앞부분**을 준다(25.1131) — 1회차는 빈 창을 모델에 줘 그 절을 통째로 놓쳤다."""
    lines = text.split("\n")
    keep = sorted({j for i, ln in enumerate(lines) if any(k in ln for k in keys) for j in (i - 1, i, i + 1)
                   if 0 <= j < len(lines)})  # fmt: skip
    got = "\n".join(lines[i] for i in keep)[:limit]
    return got if got.strip() else text[:limit]


def numbered(text: str) -> tuple[str, list[str]]:
    """창에 줄 번호를 붙인다. 모델은 근거를 **줄 번호**로 답한다(25.1131).

    1회차 4b 는 거래처 20곳 문장을 이름마다 통째로 되풀이하다 출력 상한에서 잘려 JSON 이 깨졌다
    (한미반도체 20곳 전부 놓침)."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return "\n".join(f"[{i}] {ln}" for i, ln in enumerate(lines, 1)), lines


def resolve(item: dict, lines: list[str]) -> dict:
    """모델 답의 줄 번호를 그 줄 원문으로 바꿔 `evidence` 에 둔다.

    번호가 없거나 범위 밖이면 예전처럼 `evidence` 그대로."""
    n = item.get("line")
    if isinstance(n, str) and n.strip().isdigit():
        n = int(n.strip())
    if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(lines):
        return {**item, "evidence": lines[n - 1]}
    return item


def _norm(s: str) -> str:
    return re.sub(r"[\s|·,.()\"'“”‘’]+", "", s).lower()


def grounded(item: dict, source: str) -> tuple[bool, bool]:
    """(이름이 원문에 있나, 근거가 원문에 있고 **그 근거에 이름이 있나**). 띄어쓰기·표 칸·문장부호는 무시한다.

    2회차부터 근거는 모델이 고른 줄 번호의 원문 줄이라 "원문에 있나" 는 늘 참이다 —
    그 줄에 이름이 있어야 맞는 줄을 고른 것이다 (25.1131). 1회차 답(글자 그대로 복사한 구절)에도 같은 잣대가 맞는다."""
    src = _norm(source)
    name = _norm(str(item.get("name") or ""))
    quote = _norm(str(item.get("evidence") or ""))
    return bool(name) and name in src, bool(quote) and quote in src and bool(name) and name in quote


#: 2회차 프롬프트 (25.1131). 1회차의 익명 예시("A사", "해외 고객사", "국내 완성차 업체")를 두 모델이
#: 원문에 없는데도 베꼈다 — 예시 문구를 두지 않는다. 근거는 줄 번호.
#: 회사가 아닌 것(나라·제품)과 역할(누가 누구에게 파나)을 짚는다
PROMPT = """다음은 한국 상장사 {company} 의 사업보고서 일부다. 각 줄 앞에 [번호]가 있다.
{company} 에 물건을 **사 가는** 회사는 customers, {company} 에 **공급하는(파는)** 회사는 suppliers 다.
- 원문에 **회사 이름이 글자로 적힌 것**만 뽑아라. 나라·지역 이름, 제품·원재료 이름, 업종 이름은 회사가 아니다.
- 이름 없이 회사를 가리키는 표현은 anonymous 에 원문 글자 그대로 적어라. 원문에 그런 표현이 없으면 빈 목록이다.
- 자회사·종속회사 간 거래는 뽑지 마라.
- line 에는 그 회사 이름이 적힌 줄의 번호를 숫자로 적어라.
- 원문에 없는 회사를 지어내지 마라. 없으면 빈 목록이다.
JSON 하나만 답하라:
{{"customers":[{{"name":"","line":0}}],"suppliers":[{{"name":"","line":0}}],"anonymous":[]}}

원문:
{text}"""

#: 출력 상한(토큰). 1회차 1,024 에서 거래처 20곳 절이 잘렸다 — 줄 번호로 답이 짧아진 데다 두 배로 둔다 (25.1131)
MAX_TOKENS = 2048

def load_model(choice: str):  # noqa: ANN201 — llama_cpp 는 실행할 때만 import 한다
    from huggingface_hub import HfApi, hf_hub_download
    from llama_cpp import Llama

    api = HfApi()
    for repo in MODELS[choice]:
        try:
            files = api.list_repo_files(repo)
        except Exception as exc:  # noqa: BLE001 — 후보를 차례로 시험한다
            print(f"  모델 저장소 없음 {repo}: {type(exc).__name__}")
            continue
        gguf = sorted(f for f in files if f.lower().endswith("q4_k_m.gguf"))
        if not gguf:
            print(f"  {repo}: Q4_K_M 파일 없음")
            continue
        try:
            card = api.model_info(repo).card_data
            lic = getattr(card, "license", None) if card else None
        except Exception:  # noqa: BLE001 — 라이선스 표기는 참고용이다. 못 읽어도 시험은 계속한다
            lic = None
        t = time.time()
        path = hf_hub_download(repo, gguf[0])
        print(f"  모델 {repo}/{gguf[0]} 라이선스={lic} 받기 {time.time() - t:.0f}초 "
              f"{os.path.getsize(path) / 1e9:.2f}GB")  # fmt: skip
        llm = Llama(model_path=path, n_ctx=8192, n_threads=os.cpu_count() or 4, verbose=False)
        return llm, repo
    raise SystemExit(f"{choice}: 받을 수 있는 모델이 없다")


def parse_answer(raw: str) -> dict | None:
    """모델 답에서 JSON 하나. 생각 꼬리표·코드펜스를 걷어 낸다."""
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        got = json.loads(raw)
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def local_backend(choice: str):  # noqa: ANN201
    llm, repo = load_model(choice)
    thinking_off = "2507" not in repo  # 2507 판은 생각 없는 지시형, 나머지는 섞인형이라 /no_think

    def complete(prompt: str) -> tuple[str, dict]:
        res = llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt + (" /no_think" if thinking_off else "")}],
            temperature=0.0, max_tokens=MAX_TOKENS, response_format={"type": "json_object"})  # fmt: skip
        return res["choices"][0]["message"]["content"] or "", res.get("usage") or {}

    return complete, repo


#: 호스팅 무료 등급 — 둘 다 **새 계정 없이** 지금 있는 시크릿으로 시험한다 (docs/infra.md 25.1074)
#: GitHub Models: Actions GITHUB_TOKEN + permissions models: read. 2026-06 신규 차단설 [확인필요] — 실패도 결과
#: Cloudflare Workers AI: 하루 10,000 뉴런 무료(공식 가격 문서). D1 계정·토큰 재사용 — 권한 [확인필요]
REMOTE = {
    "github": ("https://models.github.ai/inference/chat/completions", "GITHUB_TOKEN", "openai/gpt-4.1-mini"),
    # 예전 주소. 새 주소가 JSON 아닌 답을 줬다(2026-10-09 11:06 UTC) — 어느 쪽이 살아 있나 함께 본다
    "github-azure": ("https://models.inference.ai.azure.com/chat/completions", "GITHUB_TOKEN", "gpt-4o-mini"),
    "cloudflare": ("https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1/chat/completions",
                   "D1_API_TOKEN", "@cf/qwen/qwen3-30b-a3b-fp8"),
}  # fmt: skip


def remote_backend(name: str, model: str | None):  # noqa: ANN201
    url, token_env, default_model = REMOTE[name]
    url = url.format(account=os.environ.get("D1_ACCOUNT_ID", ""))
    token = os.environ.get(token_env, "")
    model = model or default_model

    def complete(prompt: str) -> tuple[str, dict]:
        r = requests.post(url, timeout=180, headers={"Authorization": f"Bearer {token}"}, json={
            "model": model, "temperature": 0, "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": prompt}]})  # fmt: skip
        if r.status_code != 200:
            # 본문만 짧게 — 요청 머리(토큰)는 찍지 않는다
            return f"HTTP {r.status_code}: {r.text[:300]}", {}
        try:
            body = r.json()
            return body["choices"][0]["message"].get("content") or "", body.get("usage") or {}
        except (ValueError, KeyError, IndexError, TypeError):
            return f"HTTP {r.status_code} JSON 아님: {r.text[:300]!r}", {}

    return complete, f"{name}:{model}"


def ask(complete, company: str, text: str) -> tuple[dict | None, dict]:  # noqa: ANN001
    """줄 번호를 붙여 묻고, 답의 줄 번호를 원문 줄로 바꿔 돌려준다(25.1131)."""
    body, lines = numbered(text)
    t = time.time()
    raw, usage = complete(PROMPT.format(company=company, text=body))
    took = time.time() - t
    parsed = parse_answer(raw)
    if parsed is not None:
        for side in ("customers", "suppliers"):
            parsed[side] = [resolve(i, lines) if isinstance(i, dict) else i for i in parsed.get(side) or []]
    return parsed, {"seconds": round(took, 1), "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"), "raw": raw[:1500]}  # fmt: skip


#: DART 없이 백엔드만 시험하는 지어낸 견본(실제 공시 아님). 정답: 고객 Apple·Verizon, 공급 SK실트론, 익명 A사
PING_TEXT = (
    "당사의 주요 매출처는 Apple, Verizon 등이며 국내 완성차 업체 A사에도 일부 납품하고 있습니다.\n"
    "주요 원재료 | 웨이퍼 | 매입처 | SK실트론\n"
    "당사의 종속회사인 한빛전자(주)에 대한 매출은 연결 시 제거됩니다."
)


_ANON = re.compile(r"^(?:[가-힣]*\s*)?(?:[A-Z]|[A-Z]\d?)\s*사$|고객사|업체|거래처|해외\s*고객|국내\s*고객")


def is_anonymous(name: str) -> bool:
    """'A사'·'국내 완성차 업체'·'해외 고객사' 처럼 이름이 없는 것. 모델이 지시를 어겨도 규칙으로 걸러 낸다."""
    return bool(_ANON.search(name.strip()))


#: 회사가 아닌 나라·지역 이름 (25.1131) — 1회차 4b 가 "일본, 한국 … 에 생산시설을 보유한 공급사" 에서
#: 나라 여덟 개를 공급사로 뽑았다
_PLACE = re.compile(
    r"^(?:한국|국내|일본|중국|중화권|대만|미국|독일|베트남|인도|인도네시아|유럽|북미|남미|동남아(?:시아)?|아시아|해외|홍콩|싱가포르"
    r"|멕시코|폴란드|헝가리|체코|영국|프랑스|캐나다|호주|브라질|태국|말레이시아|필리핀)$"
)


def is_place(name: str) -> bool:
    """나라·지역 이름 그 자체인가. '한국전력'·'일본제철' 처럼 회사 이름 안에 든 것은 아니다."""
    return bool(_PLACE.match(name.strip()))


def ping_score(parsed: dict | None, rule_filter: bool = False) -> str:
    if parsed is None:
        return "JSON 아님"

    def names(side: str) -> set[str]:
        return {_norm(str(i.get("name"))) for i in parsed.get(side) or []
                if isinstance(i, dict) and not (rule_filter and is_anonymous(str(i.get("name"))))}  # fmt: skip

    cust, sup = names("customers"), names("suppliers")
    ok = cust == {"apple", "verizon"} and sup == {_norm("SK실트론")}
    return f"{'정답' if ok else '오답'} — 고객 {sorted(cust)} 공급 {sorted(sup)} 익명 {parsed.get('anonymous')}"


def latest_annual(corp_code: str, key: str) -> dict | None:
    """가장 최근 사업보고서(A001) 접수 한 건. 정정본이 있으면 목록 맨 위가 최신이다."""
    r = requests.get(f"{DART}/list.json", timeout=30, params={
        "crtfc_key": key, "corp_code": corp_code, "bgn_de": "20250101", "pblntf_detail_ty": "A001",
        "page_count": 10})  # fmt: skip
    rows = (r.json().get("list") or []) if r.status_code == 200 else []
    rows = [x for x in rows if "사업보고서" in x.get("report_nm", "")]
    return rows[0] if rows else None


def document_xml(rcept_no: str, key: str) -> str:
    r = requests.get(f"{DART}/document.xml", params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=60)
    if not r.content.startswith(b"PK"):
        raise RuntimeError(f"zip 아님: {r.text[:200]}")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        # 본문은 접수번호와 같은 이름이다. 감사보고서 등 첨부는 뒤에 _번호가 붙는다
        names = sorted(z.namelist(), key=lambda n: (not n.startswith(rcept_no + "."), n))
        raw = z.read(names[0])
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _backend(args: argparse.Namespace):  # noqa: ANN202
    if args.backend == "local":
        return local_backend(args.model)
    return remote_backend(args.backend, args.remote_model or None)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="probe-out")
    p.add_argument("--model", default="qwen3-4b", choices=sorted(MODELS))
    p.add_argument("--tickers", default="")
    p.add_argument("--backend", default="local", choices=["local", *REMOTE])
    p.add_argument("--remote-model", default="")
    p.add_argument("--ping", action="store_true", help="DART 없이 지어낸 견본 한 건으로 백엔드만 시험")
    args = p.parse_args()
    if args.ping:
        complete, repo = _backend(args)
        parsed, meta = ask(complete, "견본", PING_TEXT)
        print(f"핑 {repo}: {meta['seconds']}초 토큰 {meta['prompt_tokens']}/{meta['completion_tokens']} — "
              f"{ping_score(parsed)} / 규칙 거른 뒤 {ping_score(parsed, rule_filter=True)}\n"
              f"원답: {meta['raw'][:500]}")  # fmt: skip
        return 0
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 가 비었다")
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from batch.sources import dart

    # **DART 부터 본다** — 점검(상태 800) 중이면 모델(2.5~5GB)을 받기 전에 끝낸다 (25.1078, 10-09 한글날 재시도 여섯 번)
    codes = dart.fetch_corp_codes()
    if not codes.ok:
        print(f"고유번호 실패: {codes.error}")
        return 1
    by_ticker = {c.stock_code: c for c in codes.data}
    complete, repo = _backend(args)

    summary = {"model": repo, "docs": 0, "items": 0, "name_ok": 0, "quote_ok": 0, "anon": 0, "parse_fail": 0,
               "no_section": 0, "rule_dropped": 0, "place_dropped": 0, "anon_ok": 0, "seconds": []}  # fmt: skip
    tickers = [t.strip() for t in args.tickers.split(",") if re.fullmatch(r"\d{6}", t.strip())] or list(TICKERS)
    for ticker in tickers:
        corp = by_ticker.get(ticker)
        lines: list[str] = []
        if corp is None:
            print(f"{ticker}: 고유번호 없음")
            continue
        try:
            filing = latest_annual(corp.corp_code, key)
            if filing is None:
                print(f"{ticker}: 사업보고서 없음")
                continue
            xml = document_xml(filing["rcept_no"], key)
        except Exception as exc:  # noqa: BLE001 — 한 종목 실패로 시험을 멈추지 않는다
            print(f"{ticker}: 받기 실패 {type(exc).__name__}: {str(exc)[:200]}")
            continue
        secs = sections(xml)
        lines.append(f"# {corp.corp_name} ({ticker}) 접수 {filing['rcept_no']} {filing['report_nm']}")
        lines.append(f"절: { {k: len(v) for k, v in secs.items()} }")
        if not secs:
            summary["no_section"] += 1
            titles = [to_text(m.group(1)) for m in _TITLE.finditer(xml)][:60]
            lines.append(f"찾은 절 없음. 제목 앞 60개: {titles}")
        for name, body in secs.items():
            text = window(body)
            parsed, meta = ask(complete, corp.corp_name, text)
            summary["docs"] += 1
            summary["seconds"].append(meta["seconds"])
            lines.append(f"\n## 절 [{name}] 원문 {len(body)}자 → 창 {len(text)}자, "
                         f"{meta['seconds']}초, 토큰 입력 {meta['prompt_tokens']} "
                         f"출력 {meta['completion_tokens']}")  # fmt: skip
            lines.append("### 창(모델이 본 원문)\n" + text)
            if parsed is None:
                summary["parse_fail"] += 1
                lines.append("### 모델 답(JSON 아님)\n" + meta["raw"])
                continue
            lines.append("### 모델 답")
            for side in ("customers", "suppliers"):
                for item in parsed.get(side) or []:
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    if is_anonymous(str(item.get("name"))):
                        summary["rule_dropped"] += 1
                        lines.append(f"- {side} {item.get('name')!r} 규칙으로 뺌(익명)")
                        continue
                    if is_place(str(item.get("name"))):
                        summary["place_dropped"] += 1
                        lines.append(f"- {side} {item.get('name')!r} 규칙으로 뺌(나라·지역)")
                        continue
                    n_ok, q_ok = grounded(item, text)
                    summary["items"] += 1
                    summary["name_ok"] += n_ok
                    summary["quote_ok"] += q_ok
                    lines.append(f"- {side} {item.get('name')!r} 이름{'O' if n_ok else 'X'} 근거{'O' if q_ok else 'X'}"
                                 f" — {str(item.get('evidence'))[:160]!r}")  # fmt: skip
            anon = [str(a) for a in parsed.get("anonymous") or [] if a]
            summary["anon"] += len(anon)
            # 익명 표현도 원문에 있나 (25.1131) — 1회차 두 모델이 프롬프트 예시를 원문에 없는데 베꼈다
            anon_ok = sum(1 for a in anon if _norm(a) and _norm(a) in _norm(text))
            summary["anon_ok"] += anon_ok
            lines.append(f"- 익명 {len(anon)} (원문에 있음 {anon_ok}): {anon[:10]}")
        (out / f"{ticker}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{ticker}: 절 {len(secs)}")

    s = summary
    secs_sorted = sorted(s["seconds"])
    med = secs_sorted[len(secs_sorted) // 2] if secs_sorted else None
    (out / "_summary.txt").write_text(
        f"모델 {s['model']}\n문서 절 {s['docs']}, 뽑은 항목 {s['items']}, 이름 원문 일치 {s['name_ok']}, "
        f"근거 원문 일치 {s['quote_ok']}, 익명 언급 {s['anon']}(원문에 있음 {s['anon_ok']}), "
        f"JSON 실패 {s['parse_fail']}, "
        f"절 못 찾음 {s['no_section']}, 익명 규칙으로 뺌 {s['rule_dropped']}, 나라 규칙으로 뺌 {s['place_dropped']}\n"
        f"절당 초 중앙 {med}, 최대 {max(secs_sorted) if secs_sorted else None}, 합 {sum(secs_sorted):.0f}\n",
        encoding="utf-8",
    )
    print("끝")
    return 0


if __name__ == "__main__":
    sys.exit(main())
