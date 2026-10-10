"""뉴스 감성 채점·집계 (docs/sentiment.md, Step 7). 일일 배치가 점수 계산 **전에** 부른다.

1. 아직 채점하지 않은 기사(news)를 채점해 article_sentiments 에
     미국  VADER compound (−1~+1), 제목만. 가벼워 일일 배치 안에서 돈다
     국내  KorFinASC (apache-2.0, 2.24GB) P(긍정) − P(부정), "제목 </s> 종목명". 무거워 **sentiment-kr.yml 이
           일일 배치 전(07:40 KST)에 따로** 채점한다. 일일 배치 안에서는 채점하지 않고 집계만 한다
2. 최근 30일 기사가 있는 종목마다 집계해 sentiment_scores 에 (기준일 하루 한 줄)

수집은 여기서 하지 않는다. 미국은 /api/cron/news(1분마다 한 종목), 국내는 /api/cron/news-kr(1시간마다 연합뉴스 RSS).

실행
  python -m batch.jobs.sentiment --market US
  python -m batch.jobs.sentiment --market KR --score    # torch·transformers 가 설치된 곳에서만 (sentiment-kr.yml)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from dataclasses import replace as dc_replace
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from batch import config
from batch.core import calendar as cal
from batch.core import db
from batch.core.client import TursoClient
from batch.core.entry import guard
from batch.services import sentiment as st

log = logging.getLogger("sentiment")

JOB_NAME = "sentiment"
#: **채점 없이 집계만 한 실행은 다른 이름으로 남긴다** (docs/infra.md 25.371). 국내 아침 배치(`daily_kr`)는
#: 채점 없이 집계만 하는데(국내 모델이 무거워 `sentiment-kr.yml` 이 따로 채점) 같은 `sentiment` 로 성공을 남겨,
#: 채점 워크플로가 며칠째 안 돌아도 무응답 감시("국내 뉴스 감성 채점")가 그 기록으로 **조용했다.**
AGGREGATE_JOB_NAME = "sentiment_aggregate"
METHOD_EN = "vader"
METHOD_KO = "korfinasc"
KORFINASC_MODEL = "amphora/KorFinASC-XLM-RoBERTa"
# 시장별 (채점 방식, 기사 언어)
METHODS = {"US": (METHOD_EN, "en"), "KR": (METHOD_KO, "ko")}


#: **금융 제목 보정 사전** (docs/sentiment.md 2장, docs/infra.md 25.545, 감사 재현). VADER 는 일반 소셜 글 사전이라
#: "shares"(+1.2)를 긍정으로 보고 plunge·sink·tumble·slide·downgrade·soar·rally·beats 는 **아예 모른다** —
#: "Apple shares plunge after earnings miss" 가 +0.153, "shares rise" 와 "shares plunge" 가 같은 점수였다. 그래서
#: 미국 감성급락 플래그가 사실상 뜰 수 없었다. 값은 VADER 척도(−4~+4)로, 사전에 이미 있는 비슷한 말(drop −1.1,
#: crash −1.7, gain 2.4)에 맞춰 정한 추정이다 [확인필요: 실제 제목 표본으로 조정]
FINANCE_LEXICON: dict[str, float] = {
    # 주식을 뜻하는 말은 중립 — 일반 사전의 "share(나누다)" 긍정이 모든 주가 기사에 붙었다
    "shares": 0.0, "share": 0.0, "stock": 0.0, "stocks": 0.0,
    **dict.fromkeys(("plunge", "plunges", "plunged", "plunging"), -2.5),
    **dict.fromkeys(("plummet", "plummets", "plummeted", "plummeting"), -2.8),
    **dict.fromkeys(("tumble", "tumbles", "tumbled", "tumbling"), -2.2),
    **dict.fromkeys(("sink", "sinks", "sank", "sinking"), -1.8),
    **dict.fromkeys(("slide", "slides", "slid", "sliding"), -1.5),
    **dict.fromkeys(("fall", "falls", "fell", "falling"), -1.4),
    **dict.fromkeys(("slump", "slumps", "slumped"), -2.0),
    **dict.fromkeys(("downgrade", "downgrades", "downgraded"), -2.0),
    **dict.fromkeys(("selloff", "sell-off"), -2.0),
    # 판 2 (25.548, 교차검증) — "stock drops 5%" "shares dip" "crashes 10%" 가 0 이었다. VADER 는 활용형을 풀지 않는다
    **dict.fromkeys(("drop", "drops", "dropped", "dropping"), -1.5),
    **dict.fromkeys(("slip", "slips", "slipped", "slipping"), -1.2),
    **dict.fromkeys(("dip", "dips", "dipped"), -1.2),
    **dict.fromkeys(("crash", "crashes", "crashed", "crashing"), -2.5),
    **dict.fromkeys(("tank", "tanks", "tanked", "tanking"), -2.0),
    **dict.fromkeys(("rebound", "rebounds", "rebounded", "rebounding"), 1.8),
    **dict.fromkeys(("soar", "soars", "soared", "soaring"), 2.5),
    **dict.fromkeys(("surge", "surges", "surged", "surging"), 2.0),
    **dict.fromkeys(("rally", "rallies", "rallied"), 2.0),
    **dict.fromkeys(("jump", "jumps", "jumped"), 1.5),
    **dict.fromkeys(("rise", "rises", "rose", "rising"), 1.2),
    **dict.fromkeys(("climb", "climbs", "climbed"), 1.3),
    **dict.fromkeys(("upgrade", "upgrades", "upgraded"), 2.0),
    **dict.fromkeys(("beat", "beats"), 1.5),
}


#: 판이 바뀌었을 때 다시 채점하는 기간(일). 감성 창 30일 + **30일 변화**가 보는 30일 앞 + 여유 1일 (25.553, 교차검증 —
#: 38일이면 `delta_30d` 에 22일 동안 옛 판 점수가 섞였다). VADER 는 가벼워 두 달치도 싸다
RESCORE_DAYS = 61

#: 보정 사전의 판. 사전을 바꾸면 올린다 — 판이 다른 미국 기사는 다시 채점된다 (25.545)
FINANCE_LEXICON_VERSION = 2


def vader_method_version() -> str:
    from importlib.metadata import version

    return f"{version('vaderSentiment')} +fin{FINANCE_LEXICON_VERSION}"


def vader_scorer():
    """VADER 채점기. 제목 한 줄을 −1~+1 로. 불러오는 데 시간이 조금 들어 한 번만 만든다.

    금융 제목 보정 사전(`FINANCE_LEXICON`)을 얹는다 (docs/infra.md 25.545).
    """
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    analyzer = SentimentIntensityAnalyzer()
    analyzer.lexicon.update(FINANCE_LEXICON)

    def score(text: str) -> tuple[float, dict]:
        polarity = analyzer.polarity_scores(text)
        return float(polarity["compound"]), {k: polarity[k] for k in ("pos", "neu", "neg")}

    return score


def label_map(id2label: dict) -> dict[int, str]:
    """모델이 주는 라벨을 `{자리(int): 이름(소문자)}` 으로 고친다.

    **키가 문자열로 올 수 있다.** 허깅페이스 설정은 JSON 이라 `id2label` 이 `{"0": "positive"}`
    로 실려 있고, 라이브러리가 정수로 바꿔 주는지는 버전에 달렸다. 문자열인 채로 두면
    `labels[i]` 가 `KeyError` 로 터진다 — 채점 직전에, 모델을 다 받아 놓고서.
    """
    return {int(key): str(value).lower() for key, value in id2label.items()}


def score_from_probs(row: list[float], labels: dict[int, str]) -> tuple[float, dict[str, float]]:
    """확률 한 줄 → (점수, 라벨별 확률). 점수 = 긍정 확률 − 부정 확률.

    **라벨 이름을 못 찾으면 조용히 0 을 주지 않는다.** 예전에는 `named.get("positive", 0.0)` 이라
    라벨 이름이 다른 모델을 물리면 **모든 기사가 0 점**이 됐다. 센티먼트 축이 죽은 채로 매일
    도는 것이 터지는 것보다 나쁘다 — 아무도 눈치채지 못하기 때문이다 (docs/sentiment.md).
    """
    named = {labels[i]: round(p, 4) for i, p in enumerate(row)}
    if "positive" not in named and "negative" not in named:
        raise ValueError(
            f"모델 라벨에 positive/negative 가 없습니다: {sorted(named)}."
            " 라벨 이름이 다른 모델이면 점수 규칙을 먼저 정해야 합니다"
        )
    return named.get("positive", 0.0) - named.get("negative", 0.0), named


def korfinasc_scorer(batch_size: int = 32):
    """KorFinASC 채점기. (제목, 종목명) 묶음을 받아 [(점수, 확률), ...].

    2026-09-17 실측: 러너 CPU 건당 0.18초(스레드 1), 손으로 쓴 14문장 중 방향 13개 맞음.

    학습에는 쓰지 않는다(연합뉴스 피드 "AI 학습 금지", docs/sentiment.md 1.2). 이미 학습된 모델로 채점만 한다.
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(max(2, torch.get_num_threads()))
    tokenizer = AutoTokenizer.from_pretrained(KORFINASC_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(KORFINASC_MODEL)
    model.eval()
    labels = label_map(model.config.id2label)

    def score(pairs: list[tuple[str, str]]) -> list[tuple[float, dict]]:
        out: list[tuple[float, dict]] = []
        for start in range(0, len(pairs), batch_size):
            chunk = pairs[start : start + batch_size]
            enc = tokenizer([t for t, _ in chunk], [c for _, c in chunk], padding=True, truncation=True,
                            max_length=128, return_tensors="pt")  # fmt: skip
            with torch.no_grad():
                probs = torch.softmax(model(**enc).logits, dim=-1).tolist()
            for row in probs:
                out.append(score_from_probs(row, labels))
        return out

    return score


def aspect_target(title: str, name: str, aliases: list[str]) -> str:
    """국내 채점 모델(KorFinASC)에 넘길 **대상 이름** — 제목에 실제로 나온 이름 (docs/infra.md 25.938, 감사).

    국내 뉴스는 거래소 약칭뿐 아니라 별칭(`stock_aliases`, 25.840 — NAVER↔네이버, S-Oil↔에쓰오일)으로도
    종목에 붙는다. 그런데 채점은 늘 약칭을 넘겨, "네이버, 3분기 영업익 급감" 을 "NAVER 에 대한 감성" 으로 물었다
    — 제목에 없는 회사에 대한 물음이다.
    약칭이 제목에 있으면 약칭, 없으면 제목에 있는 가장 긴 별칭, 둘 다 없으면 약칭(예전과 같다).
    """
    if name and name in title:
        return name
    나온 = [a for a in aliases if a and a in title]
    return max(나온, key=len) if 나온 else name


def as_of_moment(as_of: str, market: str) -> datetime:
    """창 끝 — **다음 거래일 전날**의 그 시장 현지 23:59:59 를 UTC 로 (docs/infra.md 25.237·25.744).
    평일은 기준일 끝이고, 금요일·연휴 앞날이면 쉬는 날 끝까지다.

    2026-09-26 까지 UTC 23:59:59 였다. 국내는 그것이 **다음날 08:59 KST** 라, 기준일 D 의 점수에 D+1 아침 기사가
    섞였다(9시간 앞을 본다). 미국은 거꾸로 19:59 ET 에서 끊겨 그날 저녁 기사가 빠졌다.
    """
    tz = ZoneInfo(cal.MARKETS[market.upper()]["timezone"])
    기준일 = datetime.fromisoformat(as_of).date()
    # **다음 거래일 전날까지 넓힌다** (docs/infra.md 25.744, 감사). 기준일 D 의 점수는 D 다음 거래일 아침 리포트가 쓴다.
    # 금요일이면 창이 금 23:59 에 끝나 **토·일(연휴면 연휴 내내) 기사가 월요일 리포트에 안 들어가고** 화요일에야
    # 들어갔다.
    # 평일은 다음 거래일 전날 = D 라 예전과 같다. 다음 거래일 새벽 기사는 여느 평일처럼 다음 날 점수로 간다
    try:
        끝날 = max(기준일, cal.next_session(market.upper(), 기준일) - timedelta(days=1))
    except Exception as exc:  # noqa: BLE001 — 달력을 못 읽으면 예전 규칙(기준일 끝)으로
        log.warning("다음 거래일을 못 구해 감성 창을 기준일 끝에서 자릅니다: %s", exc)
        끝날 = 기준일
    끝 = datetime.combine(끝날, time(23, 59, 59), tzinfo=tz)
    return 끝.astimezone(UTC)


def _aliases(client: TursoClient) -> dict[int, list[str]]:
    """종목별 별칭. 표가 없으면(마이그레이션 0043 전) 빈 것 — 약칭만 쓴다."""
    try:
        rows = client.execute("SELECT stock_id, alias FROM stock_aliases").rows
    except Exception as exc:
        if not db.표가_없나(exc):
            raise
        return {}
    out: dict[int, list[str]] = {}
    for sid, alias in rows:
        out.setdefault(int(sid), []).append(str(alias))
    return out


def run(market: str, as_of: str | None = None, score: bool | None = None) -> int:
    """score 가 None 이면 미국만 채점한다(국내 모델은 무거워 sentiment-kr.yml 이 따로 채점)."""
    method, lang = METHODS[market]
    score = (market == "US") if score is None else score
    client = TursoClient()
    try:
        db.apply_migrations(client)
        # 직전 거래일 (docs/infra.md 25.234·25.237). UTC 오늘이면 월요일 07:40 KST 의 sentiment-kr 이 **일요일** 행을
        # 써서, 아침 배치(기준 금요일)가 덮지 못하는 행이 남았고 스크리너·매도 플래그는 그것을 가장 새 감성으로 읽었다
        as_of = as_of or cal.default_as_of(market)
        run_id = db.start_batch_run(
            client, job_name=JOB_NAME if score else AGGREGATE_JOB_NAME, market=market, trade_date=as_of
        )
        now = db.now_iso()

        # 1. 채점
        # 미국은 **보정 사전 판이 다른 기사도 다시 채점한다** (25.545) — 옛 사전 점수와 새 사전 점수가 7일 변화에서
        # 섞이면 판이 바뀐 날 가짜 급변이 난다. 다시 매기는 것은 **최근 `RESCORE_DAYS` 일 기사만**이다(25.548·25.553) —
        # 조건이 없어 전 이력을 다시 쓰고 있었다. 창 밖 기사는 어느 점수에도 들어가지 않는다
        현재판 = vader_method_version() if market == "US" else ""
        재채점_시작 = (date.fromisoformat(as_of) - timedelta(days=RESCORE_DAYS)).isoformat()
        pending = client.execute(
            "SELECT n.id, n.stock_id, n.title, COALESCE(s.name_ko, s.name_en, s.ticker) AS name FROM news n"
            " JOIN stocks s ON s.id = n.stock_id"
            " LEFT JOIN article_sentiments a ON a.news_id = n.id AND a.method = ?"
            " WHERE s.country = ? AND n.lang = ? AND (a.id IS NULL OR (? <> ''"
            "   AND COALESCE(a.method_version, '') <> ? AND n.published_at >= ?))",
            [method, market, lang, 현재판, 현재판, 재채점_시작],
        ).dicts() if score else []
        scored = 0
        못_매김 = 0
        if pending:
            from importlib.metadata import version

            if market == "US":
                vader = vader_scorer()
                results = [vader(str(r["title"])) for r in pending]
                method_version = 현재판
            else:
                별칭 = _aliases(client)
                results = korfinasc_scorer()([
                    (str(r["title"]), aspect_target(str(r["title"]), str(r["name"]), 별칭.get(int(r["stock_id"]), [])))
                    for r in pending
                ])
                method_version = f"{KORFINASC_MODEL} transformers {version('transformers')}"
            statements: list[tuple[str, list[Any]]] = []
            for row, (value, parts) in zip(pending, results, strict=True):
                # **NaN·inf 는 점수가 아니다** (docs/infra.md 25.1097, 감사 재현) — `max(-1, min(1, nan))` 이 +1.0 이라
                # 모델이 NaN 을 내면 강한 긍정으로 저장됐다. 적지 않고 "채점 안 된 기사" 로 남긴다 — 그 몫이 크면
                # 25.834 의 미채점 문이 그 종목 감성을 비운다
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    못_매김 += 1
                    continue
                statements.append((
                    "INSERT INTO article_sentiments (news_id, score, method, method_version, matched_terms, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (news_id, method) DO UPDATE SET"
                    " score = excluded.score, method_version = excluded.method_version,"
                    " matched_terms = excluded.matched_terms, created_at = excluded.created_at",
                    [row["id"], max(-1.0, min(1.0, value)), method, method_version, json.dumps(parts), now],
                ))  # fmt: skip
            for start in range(0, len(statements), 300):
                client.batch(statements[start : start + 300])
            scored = len(statements)

        # 2. 집계 — 7·30일 변화를 내려고 60일치를 읽는다
        moment = as_of_moment(as_of, market)
        since = (moment - timedelta(days=st.WINDOW_DAYS * 2 + 1)).isoformat()
        날것: dict[int, list[tuple[str, datetime, float]]] = {}
        for r in client.execute(
            "SELECT n.stock_id, n.title, n.published_at, a.score FROM news n JOIN stocks s ON s.id = n.stock_id"
            " JOIN article_sentiments a ON a.news_id = n.id AND a.method = ?"
            " WHERE s.country = ? AND n.published_at >= ? AND n.published_at <= ?",
            [method, market, since, moment.isoformat()],
        ).dicts():
            published = datetime.fromisoformat(str(r["published_at"]).replace("Z", "+00:00"))
            날것.setdefault(int(r["stock_id"]), []).append((str(r["title"] or ""), published, float(r["score"])))
        # **채점되지 않은 기사**가 창 안에 얼마나 있나 (docs/infra.md 25.834, 감사). 국내 채점(sentiment-kr)이 실패한
        # 날에도 아침 배치가
        # 집계를 돌려, 최근 악재가 빠진 채 옛 기사로만 낸 값이 "오늘 감성" 으로 점수·매도 플래그에 들어갔다
        미채점: dict[int, int] = {}
        # 비율은 **30일 창 안**에서 잰다 — 61일 전부터 세면 오래된 기사가 많은 종목일수록 보호가 무력해졌다 (25.838,
        # 교차검증)
        현지_시각 = moment.astimezone(ZoneInfo(cal.MARKETS[market.upper()]["timezone"]))
        창_시작 = (현지_시각 - timedelta(days=st.WINDOW_DAYS)).astimezone(UTC).isoformat()  # 저장 형식과 같은 UTC
        for r in client.execute(
            "SELECT n.stock_id, COUNT(*) FROM news n JOIN stocks s ON s.id = n.stock_id"
            " LEFT JOIN article_sentiments a ON a.news_id = n.id AND a.method = ?"
            " WHERE s.country = ? AND n.published_at > ? AND n.published_at <= ? AND a.news_id IS NULL"
            " GROUP BY n.stock_id",
            [method, market, 창_시작, moment.isoformat()],
        ).rows:
            미채점[int(r[0])] = int(r[1])
        # 판(1보·2보·종합)만 다른 기사는 하나로 (25.647)
        # 판 중복 날짜·30/7일 창은 그 시장 현지 달력으로 (25.749)
        현지tz = ZoneInfo(cal.MARKETS[market.upper()]["timezone"])
        by_stock = {sid: st.판_하나만(v, 현지tz) for sid, v in 날것.items()}

        rows: list[tuple[str, list[Any]]] = []
        with_score = 0
        덜_채점 = 0
        유효수: list[float] = []  # 값을 낸 종목의 유효 기사 수 — 분포 요약만 남긴다 (25.873, 8회차 4 1단계)
        for stock_id, articles in by_stock.items():
            agg = st.aggregate(articles, moment, 현지tz)
            # 채점 안 된 기사가 창 기사의 `UNSCORED_MAX_SHARE` 를 넘으면 값을 내지 않는다 — 빠진 기사가 무엇인지
            # 모른다 (25.834)
            빠짐 = 미채점.get(stock_id, 0)
            if agg.sentiment is not None and 빠짐 > st.UNSCORED_MAX_SHARE * (빠짐 + agg.article_count):
                agg = dc_replace(agg, sentiment=None)
                덜_채점 += 1
            # **0건이 된 날도 행을 쓴다** (docs/infra.md 25.413). 예전에는 건너뛰어, 30일 창에서 마지막 기사가
            # 빠진 날 행이 없었다 — 점수 배치(3일 안의 가장 새 행)와 매도 플래그가 **어제의 −80** 을 사흘 더 썼다.
            # 5건→4건처럼 줄 때는 NULL 행이 써져 막혔는데 한 번에 0건이 될 때만 샜다. 값은 비운다(없는 것은 없다)
            # 값을 내지 않은 날은 변화도 내지 않는다 — 미채점으로 비운 날 채점된 기사만으로 낸 7일 변화가 매도
            # 플래그에 들어갔다 (25.838)
            d7, d30 = st.deltas(articles, moment, 현지tz) if agg.sentiment is not None else (None, None)
            with_score += agg.sentiment is not None
            if agg.sentiment is not None and agg.effective_n is not None:
                유효수.append(agg.effective_n)
            rows.append((
                "INSERT INTO sentiment_scores (stock_id, as_of_date, sentiment, article_count, positive_count,"
                " negative_count, negative_count_7d, decay_halflife_days, delta_7d, delta_30d, method, calc_version,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (stock_id, as_of_date) DO UPDATE SET sentiment = excluded.sentiment,"
                " article_count = excluded.article_count, positive_count = excluded.positive_count,"
                " negative_count = excluded.negative_count, negative_count_7d = excluded.negative_count_7d,"
                " decay_halflife_days = excluded.decay_halflife_days, delta_7d = excluded.delta_7d,"
                " delta_30d = excluded.delta_30d, method = excluded.method, calc_version = excluded.calc_version,"
                " created_at = excluded.created_at",
                [stock_id, as_of, agg.sentiment, agg.article_count, agg.positive_count, agg.negative_count,
                 agg.negative_count_7d, st.HALFLIFE_DAYS, d7, d30, method, st.CALC_VERSION, now],
            ))  # fmt: skip
        for start in range(0, len(rows), 300):
            client.batch(rows[start : start + 300])

        step = {"scored": scored, "stocks": len(rows), "with_score": with_score, "as_of": as_of,
                "unscored_articles": sum(미채점.values()), "dropped_for_unscored": 덜_채점,  # 25.834
                "non_finite_scores": 못_매김,  # 25.1097
                "effective_n": st.effective_n_summary(유효수)}  # 25.873 — 기록만
        db.finish_batch_run(client, run_id, status="success", step_log=step)
        print(f"{market} 기사 채점 {scored}건, 종목 {len(rows)}개 집계 (점수 있음 {with_score}, 기사 5건 미만은 NULL)")
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="뉴스 감성 채점·집계")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--as-of", dest="as_of")
    parser.add_argument("--score", action="store_true", help="국내도 채점한다 (torch·transformers 필요)")
    args = parser.parse_args()
    logging.basicConfig(level=config.SETTINGS.log_level, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
    return run(args.market, args.as_of, True if args.score else None)


if __name__ == "__main__":
    sys.exit(guard(main))
