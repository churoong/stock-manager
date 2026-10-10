/**
 * 뉴스 수집 (docs/sentiment.md 1장, Step 7). 순수 함수와 질의만. 네트워크·DB 는 경로가 한다.
 *
 * 저장하는 것은 제목·주소·발행 시각·매체뿐이다. **본문은 저장하지 않는다**(CLAUDE.md).
 * 점수 매기기와 종목 집계는 Python 배치(batch/jobs/sentiment.py)가 한다. 웹은 계산하지 않는다.
 */

export interface FeedItem {
  title: string;
  url: string;
  published_at: string; // ISO UTC
  publisher: string | null;
  tickers: string[]; // 나스닥 피드의 nasdaq:tickers. 다른 피드는 빈 배열
}

/** 나스닥 robots.txt 의 Crawl-delay 30 (2026-09-17 확인). 한 호출은 한 종목만 받는다 */
export const NASDAQ_CRAWL_DELAY_SEC = 30;

/** 같은 종목을 이보다 자주 받지 않는다. 피드가 약 2일치를 주므로 하루 한 번이면 빠짐이 없다 */
export const REFETCH_AFTER_HOURS = 20;

/** 받기에 실패한 종목은 이만큼 뒤에 다시 시도한다. 실패를 "받았다" 로 치면 20시간 동안 빠진다 */
export const RETRY_AFTER_FAILURE_MINUTES = 60;

/**
 * 브라우저인 척하지 않고 정체를 밝힌다. 봇 차단을 흉내로 피하지 않는다.
 *
 * 2026-09-17 실측: "Mozilla/5.0 (stock-manager personal; contact via GitHub)" 는 20초 동안 응답이 없었고(두 번),
 * 이 문자열은 0.06초에 200. 끝의 "GitHub" 같은 문구가 봇 규칙에 걸리는 것으로 보인다 [확인필요: 규칙은 공개돼 있지 않다]
 */
export const FEED_USER_AGENT = "stock-manager/1.0 (personal RSS reader)";

/** 한 번의 피드 요청 제한. 경로 전체가 cron-job.org 30초 안에 끝나야 한다 */
export const FEED_TIMEOUT_MS = 8000;

export function nasdaqFeedUrl(symbol: string): string {
  return `https://www.nasdaq.com/feed/rssoutbound?symbol=${encodeURIComponent(symbol)}`;
}

function unescapeXml(text: string): string {
  return text
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&amp;/g, "&")
    .trim();
}

function tag(block: string, name: string): string | null {
  const m = block.match(new RegExp(`<${name}(?:\\s[^>]*)?>([\\s\\S]*?)</${name}>`));
  return m ? unescapeXml(m[1]) : null;
}

/**
 * RSS 2.0 을 item 단위로 읽는다. 외부 파서 없이 정규식으로 충분한 범위만 다룬다(제목·링크·시각·작성자·티커).
 * 발행 시각이 없거나 읽을 수 없는 항목은 버린다 — 시간 감쇠 계산에 시각이 꼭 필요하다.
 */
/**
 * 응답이 RSS 문서인가 (docs/infra.md 25.640, 감사). 200 으로 온 봇 차단 HTML·형식이 바뀐 응답을 "기사 0건" 과 가른다 —
 * 예전에는 둘 다 `ok` 로 남아 20시간 다시 받지 않았고 호출 기록도 초록이라 수집이 조용히 멈출 수 있었다.
 * 기사가 0건인 진짜 RSS 는 그대로 정상이다.
 */
export function looksLikeFeed(text: string): boolean {
  return /<(rss|channel|feed|rdf:RDF)[\s>]/i.test(text);
}

export function parseRss(xml: string): FeedItem[] {
  const items: FeedItem[] = [];
  for (const m of xml.matchAll(/<item(?:\s[^>]*)?>([\s\S]*?)<\/item>/g)) {
    const block = m[1];
    const title = tag(block, "title");
    const url = tag(block, "link") ?? tag(block, "guid");
    const pub = tag(block, "pubDate") ?? tag(block, "dc:date");
    if (!title || !url || !pub) continue;
    const ms = Date.parse(pub);
    if (!Number.isFinite(ms)) continue;
    const tickers = (tag(block, "nasdaq:tickers") ?? "")
      .split(",")
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    items.push({
      title: title.replace(/\s+/g, " "),
      url: normalizeUrl(url),
      published_at: new Date(ms).toISOString(),
      publisher: tag(block, "dc:creator") ?? tag(block, "author"),
      tickers: [...new Set(tickers)],
    });
  }
  return items;
}

/**
 * 같은 기사의 주소를 하나로 (docs/infra.md 25.546, 감사 재현). 쿼리만 떼서 `http`/`https`·`#조각` 이 다른 같은 기사가
 * 따로 저장돼, 한 사건의 부정 기사가 여러 건으로 세어져 7일 부정 건수·급락 판정이 부풀었다. 호스트는 바꾸지 않는다 —
 * 이미 저장된 주소와 어긋나 한 번 더 들어오는 일을 줄이려고
 */
export function normalizeUrl(url: string): string {
  return url.trim().split("#")[0].split("?")[0].replace(/^http:\/\//i, "https://");
}

/** 티커 표기를 하나로 — 거래소는 BRK.B, 야후는 BRK-B 로 쓴다 (25.546) */
export function canonicalTicker(t: string): string {
  return t.trim().toUpperCase().replace(/[.\-/]/g, ".");
}

/**
 * 종목 피드에 실린 기사 중 **그 종목이 관련 티커에 든 것만** 남긴다. 나스닥 종목 피드는 같은 매체의
 * 다른 기사도 섞어 준다(AAPL 피드에 버크셔 기사).
 *
 * **티커 목록이 없는 피드만** 전부 남긴다 (docs/infra.md 25.546, 감사 재현). 예전에는 항목마다 판정해, 티커가 달린
 * 피드에 섞인 티커 없는 "Pre-Market Movers: Tesla plunges" 가 AAPL 기사로 남았다. 클래스주(BRK-B)는 거래소 표기
 * (BRK.B)와 같은 것으로 본다 — 예전에는 자기 기사를 전부 잃고 티커 없는 남의 기사만 남았다
 */
export function filterForSymbol(items: FeedItem[], symbol: string, name?: string | null): FeedItem[] {
  const s = canonicalTicker(symbol);
  if (!items.some((i) => i.tickers.length > 0)) return items;
  return items.filter(
    (i) =>
      i.tickers.some((t) => canonicalTicker(t) === s) &&
      // **티커가 여럿 달린 모음 기사는 제목이 그 종목을 부를 때만** (docs/infra.md 25.745, 감사). 채점은 제목 전체로 하므로
      // "Tesla plunges on recall; Apple, Microsoft steady" 에 AAPL·TSLA·MSFT 가 달리면 AAPL 에도 −0.54 가 붙어
      // 부정 7일 건수·급락 판정을 부풀렸다. 티커가 하나인 기사는 예전처럼 남긴다
      // 25.748(교차검증): 티커 **둘까지는** 제목을 보지 않는다 — 한 회사의 두 클래스주(GOOG·GOOGL, BRK.A·BRK.B)가 함께 달린 단일 기사를
      // "Google" 로 부르면 빠졌다. 모음 기사("Tesla…; Apple, Microsoft")는 보통 셋 이상이다. 두 회사 비교 기사는 둘 다에 남는다(보수적)
      (i.tickers.length <= MULTI_TICKER_MIN - 1 || titleMentions(i.title, symbol, name)),
  );
}

/** 티커가 이만큼 이상 달린 항목만 모음 기사로 보고 제목을 본다 (25.745·25.748) */
export const MULTI_TICKER_MIN = 3;

/** 이름의 첫 낱말이 이만큼 흔하면 그 낱말만으로 종목을 부른 것으로 보지 않는다 — 둘째 낱말까지 본다 `[확인필요: 목록]` */
const 흔한_첫낱말 = new Set([
  "general", "american", "united", "first", "national", "international", "global", "new", "home", "bank", "trade",
  // 25.1098(감사 재현): "raise price targets" 가 Target, "Best stocks to buy" 가 Best Buy, "Dollar slides" 가 Dollar General
  "target", "best", "dollar", "news", "energy", "digital", "western", "southern", "state", "public", "capital", "world",
]);

/**
 * **영어 낱말과 같은 티커** — 제목에서 소문자로 바꿔 견주면 "Tesla, Apple, Microsoft all slide" 가 ALL(Allstate),
 * "Is now the time" 이 NOW(ServiceNow) 를 부른 것이 됐다 (docs/infra.md 25.1098, 감사 재현). 이런 티커와 한 글자 티커는
 * 제목에 **대문자 그대로**(`$NOW`·`(NOW)`·`NOW`) 나올 때만 부른 것으로 본다 `[확인필요: 목록]`
 */
const 낱말_티커 = new Set([
  "all", "now", "on", "it", "be", "are", "so", "go", "can", "for", "has", "key", "low", "big", "cat", "fun", "man",
  "pay", "run", "see", "two", "win", "well", "love", "good", "life", "real", "safe", "main", "next", "play", "rock",
  "ship", "site", "true", "most", "open", "tech", "fast", "cars", "nice", "ever", "out", "eat", "ago", "any", "who",
  "bull", "bear", "hope", "save", "wish", "care", "kind", "nine", "dna",
]);

/**
 * 제목이 그 종목을 부르는가 — 티커(낱말 경계, `$AAPL`·`(AAPL)` 포함) 또는 회사 이름의 첫 낱말(흔한 낱말이면 두 낱말).
 * 이름 뒤의 법인 꼬리(Inc., Corp.)는 보지 않는다. 대소문자는 가리지 않는다 (25.745)
 */
export function titleMentions(title: string, symbol: string, name?: string | null): boolean {
  // 낱말 끝의 마침표는 문장 부호다 — "Apple." 을 놓쳤다. 낱말 안의 점(BRK.B)만 남긴다 (25.748)
  const 제목 = ` ${title.toLowerCase().replace(/[^a-z0-9.]+/g, " ").replace(/\.+(?= |$)/g, "")} `;
  const 티커 = symbol.toLowerCase().replace(/[-/]/g, ".");
  if (티커.length === 1 || 낱말_티커.has(티커)) {
    const 대문자 = symbol.toUpperCase().replace(/[^A-Z0-9]/g, "");
    // 한 글자는 문장 첫 "A" 와도 같다 — `$A`·`(A)`·`NYSE: A` 꼴만 본다
    const 꼴 = 티커.length === 1
      ? `\\$${대문자}(?![A-Za-z0-9])|\\(${대문자}\\)|:\\s*${대문자}\\)`
      : `(^|[^A-Za-z0-9])\\$?${대문자}([^A-Za-z0-9]|$)`;
    if (new RegExp(꼴).test(title)) return true;
  } else if (제목.includes(` ${티커} `) || 제목.includes(` ${티커.replace(/\./g, " ")} `)) return true;
  // 앞의 "the" 는 떼고(The Coca-Cola Company), 흔한 첫 낱말이거나 3글자 미만이면(JP Morgan) 두 낱말을 본다.
  // 두 낱말은 붙여 쓴 꼴(jpmorgan)도 부른 것으로 본다 (25.748, 교차검증)
  const 낱말 = (name ?? "").toLowerCase().replace(/[^a-z0-9 ]+/g, " ").split(/\s+/).filter(Boolean);
  // 앞의 "the" 는 떼고 그 뒤 첫 낱말로 본다 — 두 낱말을 요구하면 "The Boeing Company" 가 "Boeing jets…" 를 놓친다.
  // "The Trade Desk" 처럼 떼고 남은 첫 낱말이 흔하면 `흔한_첫낱말` 이 두 낱말을 요구한다 (25.750, 교차검증)
  if (낱말[0] === "the") 낱말.shift();
  if (낱말.length === 0) return false;
  const 둘 = (흔한_첫낱말.has(낱말[0]) || 낱말[0].length < 3) && 낱말.length > 1;
  // 둘째 낱말이 of·and 면 셋째까지 본다 — "Bank of America" 와 "Bank of Japan" 이 "bank of" 로 서로 걸렸다 (25.755, 교차검증)
  const 셋 = 둘 && ["of", "and"].includes(낱말[1]) && 낱말.length > 2;
  const 부름들 = 셋
    ? [`${낱말[0]} ${낱말[1]} ${낱말[2]}`]
    : 둘
      ? [`${낱말[0]} ${낱말[1]}`, `${낱말[0]}${낱말[1]}`]
      : [낱말[0]];
  return 부름들.some((부름) => 부름.length >= 3 && (제목.includes(` ${부름} `) || 제목.includes(` ${부름}s `)));
}

/**
 * 다음에 받을 종목 하나. 대상 = 보유 ∪ 관심 ∪ 오늘 감시 대상(추천) ∪ 최신 점수 상위 N (설정 sentiment_target_top_n).
 * 한 번도 안 받은 종목이 먼저, 그다음 가장 오래전에 받은 종목. REFETCH_AFTER_HOURS 안에 받은 종목은 건너뛰고,
 * 실패한 종목은 RETRY_AFTER_FAILURE_MINUTES 뒤에 다시 준다. 인자: [상위 N, 성공 기준 시각, 실패 기준 시각]
 */
/**
 * 다음에 뉴스를 받을 종목 하나.
 *
 * **큰 표를 읽지 않는다.** 예전에는 여기서 scores 를 두 번 훑어(최신 기준일 + 그 날짜로 거르기)
 * 점수 상위 종목을 골랐다. 이 경로는 1분마다 도므로 하루 1,440번, 읽은 행이 수천만 행이 됐고
 * 2026-09-18 읽기 한도 사고의 원인이 됐다(docs/infra.md 24절). 후보 고르기는 배치가 하루 한 번
 * 하고(news_targets), 여기서는 작은 표만 합친다. 첫 인자(상위 N)는 이제 쓰지 않는다.
 *
 * **`CROSS JOIN` 은 일부러다** (docs/infra.md 25.857, 감사). SQLite 는 `CROSS JOIN` 의 왼쪽을 바깥 고리로 고정한다. 그냥 `JOIN` 이면
 * 통계 없는 D1 에서 planner 가 stocks 를 먼저 훑어(`SCAN s`) 호출마다 종목 수만큼(약 2,800행) 읽었다 — 1분마다라 하루 약 400만 행,
 * D1 하루 읽기 한도의 80% 였다. 후보부터 읽으면 후보 수만큼(수백 행)이다. `tests/queryPlan857.test.ts` 가 계획을 본다
 */
export const NEXT_TARGET = `WITH candidates AS (
  -- **보유 → 관심 → 그 밖** 순서로 준다 (docs/infra.md 25.909, 감사). 시간당 한 종목(25.880)이라 후보 200여 개가 한 바퀴에
  -- 8일 넘게 걸리는데, 나스닥 피드는 이틀치쯤만 담아 보유 종목의 기사 대부분을 놓쳤다 — 보유 감성 급락 플래그가 사실상
  -- 못 섰다. 다시 받는 간격(REFETCH_AFTER_HOURS)은 그대로라 보유는 하루 한 번쯤 받는다
  SELECT stock_id, MIN(prio) AS prio FROM (
    SELECT stock_id, 2 AS prio FROM news_targets WHERE market = 'US'
    UNION ALL SELECT stock_id, CASE WHEN quantity > 0 THEN 0 ELSE 2 END FROM positions
    -- 관심 종목은 알림을 꺼도 뉴스는 모은다 — 배치의 공시·실적 일정 수집과 같은 대상 (docs/infra.md 25.815)
    UNION ALL SELECT stock_id, 1 FROM watchlist
  ) GROUP BY stock_id
)
SELECT s.id AS stock_id, s.yahoo_symbol, COALESCE(s.name_en, s.ticker) AS name, f.last_fetched_at
FROM candidates c CROSS JOIN stocks s ON s.id = c.stock_id
LEFT JOIN news_fetch_log f ON f.stock_id = s.id
WHERE s.country = 'US' AND s.status = 'active' AND s.yahoo_symbol IS NOT NULL
  AND (f.last_fetched_at IS NULL
       OR (f.last_status = 'ok' AND f.last_fetched_at < ?)
       OR (f.last_status != 'ok' AND f.last_fetched_at < ?))
ORDER BY c.prio, f.last_fetched_at IS NOT NULL, f.last_fetched_at, s.id
LIMIT 1`;

export const NEWS_INSERT = `INSERT INTO news (stock_id, title, url, published_at, publisher, lang, source, fetched_at)
VALUES (?, ?, ?, ?, ?, 'en', 'nasdaq_rss', ?) ON CONFLICT (stock_id, url) DO NOTHING`;

export const FETCH_LOG_UPSERT = `INSERT INTO news_fetch_log (stock_id, last_fetched_at, last_status, items_seen, items_new)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (stock_id) DO UPDATE SET last_fetched_at = excluded.last_fetched_at, last_status = excluded.last_status,
  items_seen = excluded.items_seen, items_new = excluded.items_new`;

export function refetchCutoff(now: Date): string {
  return new Date(now.getTime() - REFETCH_AFTER_HOURS * 3600_000).toISOString();
}

export function retryCutoff(now: Date): string {
  return new Date(now.getTime() - RETRY_AFTER_FAILURE_MINUTES * 60_000).toISOString();
}
