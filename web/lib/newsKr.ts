/**
 * 국내 뉴스 수집 (docs/sentiment.md 1.2, 사용자 결정 2026-09-17 "도입"). 연합뉴스 RSS → 종목 이름 매칭.
 *
 * 피드 저작권 문구 "무단 전재-재배포, AI 학습 및 활용 금지". RSS 안내는 "개인적인 용도" 허용.
 * 사용자가 개인 용도로 도입을 결정했다. 지키는 것: 제목·주소·시각만 저장(요약·본문 버림), 재배포 없음(로그인 뒤 본인만),
 * **학습에 쓰지 않는다**(이미 학습된 모델로 채점만).
 *
 * 매칭은 제목 안의 종목 한글 이름 문자열이다. 오탐을 줄이는 규칙은 matchStocks 주석.
 */

import { parseRss, type FeedItem } from "@/lib/news";

/** 2026-09-17 실측: 각 120건, 매시간 갱신. 덮는 기간 산업 ≈3시간 · 경제 ≈6시간 · 증권 ≈24시간 → 1시간마다 받는다 */
export const YNA_FEEDS = [
  "https://www.yna.co.kr/rss/industry.xml",
  "https://www.yna.co.kr/rss/economy.xml",
  "https://www.yna.co.kr/rss/market.xml",
] as const;

export interface NamedStock {
  stock_id: number;
  name: string;
}

/**
 * 이름 바로 뒤에 붙어도 되는 **조사·어미 덩어리 전체** (docs/infra.md 25.546, 감사 재현). 예전에는 첫 글자만 봐서
 * "HLB**이**노베이션" 이 HLB, "에코프로**에**이치엔" 이 에코프로, "HD현대**에**너지솔루션" 이 HD현대, "삼성전자**로**지텍" 이
 * 삼성전자 기사로 붙었다 — 조사 첫 글자는 다른 회사 이름의 일부이기도 하다. 이름 뒤에 붙은 한글 덩어리가 통째로 이 목록에
 * 있어야 인정한다
 */
const PARTICLES = new Set([
  "은", "는", "이", "가", "을", "를", "의", "에", "와", "과", "도", "로", "으로", "만", "까지", "보다", "측",
  "에서", "에게", "에도", "에는", "와의", "과의", "로서", "으로서", "로의", "으로의", "만의", "이며", "이고", "이다",
  "마저", "조차", "부터", "처럼", "만큼", "쪽", "측이", "측은",
  // 복합 조사 (25.548, 교차검증) — 덩어리 전체를 보게 되며 "삼성전자에선" "셀트리온이란" 을 놓쳤다
  "에선", "에서도", "에서는", "에서의", "로선", "으로선", "로는", "으로는", "와는", "과는", "와도", "과도",
  "로부터", "으로부터", "이란", "란", "랑", "이랑", "에겐", "에게도", "이나", "나", "까지도", "보다도", "만은",
]);

/** 이보다 짧은 이름은 매칭하지 않는다. "LG" "SK" "KT" 는 그룹·계열사 기사 전반에 나와 어느 종목인지 알 수 없다 */
export const MIN_NAME_LENGTH = 3;

/**
 * 이름이 짧아 국내 뉴스를 **매칭하지 않는** 종목에 붙일 안내 (docs/infra.md 25.747, 감사). 기아·농심·효성처럼 두 글자 한글 이름도
 * `MIN_NAME_LENGTH` 에 걸려 뉴스가 한 건도 매칭되지 않는데, 화면은 "기사 5건 미만"·"집계 없음" 이라 적어 이유를 가렸다.
 * 두 글자를 허용하지 않는 까닭은 "대상"(수출 대상)처럼 흔한 낱말과 겹치는 이름이 있어서다 — 목록 없이 가를 수 없다 `[확인필요]`.
 */
export function unmatchedNameNote(country: string, nameKo: string | null | undefined): string | null {
  if (country !== "KR") return null;
  const 이름 = (nameKo ?? "").trim();
  // 매칭은 **한글 이름(name_ko)만** 본다 — 없거나 빈 값이면 영영 매칭되지 않는다 (25.748, 교차검증: 영문 이름으로 판정해 까닭을 가렸다)
  if (!이름) return "한글 종목 이름이 없어 국내 뉴스를 이 종목에 매칭하지 않습니다 — 감성·감성 급락 플래그가 나오지 않습니다";
  if (이름.length >= MIN_NAME_LENGTH) return null;
  return `종목 이름(${이름})이 ${MIN_NAME_LENGTH}글자 미만이라 국내 뉴스를 이 종목에 매칭하지 않습니다 — 감성·감성 급락 플래그가 나오지 않습니다`;
}

function isWordChar(ch: string | undefined): boolean {
  return !!ch && /[가-힣A-Za-z0-9]/.test(ch);
}

/**
 * 제목에 이름이 "단어로" 나오는 종목. 규칙
 *   1. 이름이 MIN_NAME_LENGTH 글자 미만이면 건너뛴다
 *   2. 이름 앞 글자가 한글·영숫자면 다른 단어의 일부("신삼성전자")로 보고 뺀다
 *   3. 이름 뒤에 한글이 붙으면 그 덩어리 전체가 조사·어미일 때만 인정("삼성전자가" O, "삼성전자로지텍" X, 25.546).
 *      영숫자가 붙어도 뺀다
 *   4. 겹치면 긴 이름이 이긴다("삼성전자"와 "삼성전자우"가 같은 자리에 걸리면 "삼성전자우")
 */
export function matchStocks(title: string, stocks: NamedStock[]): NamedStock[] {
  const hits: Array<{ stock: NamedStock; start: number; end: number }> = [];
  for (const stock of stocks) {
    const name = stock.name.trim();
    if (name.length < MIN_NAME_LENGTH) continue;
    let from = 0;
    while (true) {
      const at = title.indexOf(name, from);
      if (at < 0) break;
      from = at + 1;
      const before = title[at - 1];
      const after = title[at + name.length];
      if (isWordChar(before)) continue;
      if (after && /[A-Za-z0-9]/.test(after)) continue;
      if (after && /[가-힣]/.test(after)) {
        const 덩어리 = /^[가-힣]+/.exec(title.slice(at + name.length))?.[0] ?? "";
        if (!PARTICLES.has(덩어리)) continue;
      }
      hits.push({ stock, start: at, end: at + name.length });
    }
  }
  const kept = hits.filter(
    (h) => !hits.some((o) => o !== h && o.start <= h.start && o.end >= h.end && o.end - o.start > h.end - h.start),
  );
  const seen = new Set<number>();
  return kept.filter((h) => !seen.has(h.stock.stock_id) && seen.add(h.stock.stock_id)).map((h) => h.stock);
}

/** 여러 피드의 항목을 합치고 같은 주소는 하나만 */
export function mergeFeeds(xmls: string[]): FeedItem[] {
  const byUrl = new Map<string, FeedItem>();
  for (const xml of xmls) for (const item of parseRss(xml)) if (!byUrl.has(item.url)) byUrl.set(item.url, item);
  return [...byUrl.values()];
}

/** 국내 대상: 보유 ∪ 관심 ∪ 오늘 감시 대상 ∪ 최신 점수 상위 N. 인자 [상위 N] */
/** 국내 뉴스 대상. 미국과 같은 이유로 배치가 만든 news_targets 를 읽는다 (docs/infra.md 24절) */
export const KR_TARGETS = `WITH candidates AS (
  SELECT stock_id FROM news_targets WHERE market = 'KR'
  UNION SELECT stock_id FROM positions
  -- 관심 종목은 알림을 꺼도 뉴스는 모은다 — 배치의 공시·실적 일정 수집과 같은 대상 (docs/infra.md 25.815)
  UNION SELECT stock_id FROM watchlist
)
SELECT s.id AS stock_id, s.name_ko AS name
FROM candidates c CROSS JOIN stocks s ON s.id = c.stock_id
WHERE s.country = 'KR' AND s.status = 'active' AND s.name_ko IS NOT NULL`;

export const NEWS_INSERT_KR = `INSERT INTO news (stock_id, title, url, published_at, publisher, lang, source, fetched_at)
VALUES (?, ?, ?, ?, '연합뉴스', 'ko', 'yna_rss', ?) ON CONFLICT (stock_id, url) DO NOTHING`;

/**
 * 국내 뉴스 크론의 호출 결과 (docs/infra.md 25.836·25.838). 피드를 하나도 못 받았거나, 받았는데 읽힌 기사가 0건이면 실패다 —
 * 연합 피드는 늘 120건 안팎이라 0 이면 형식이 바뀐 것이다
 */
export function newsKrOutcome(feeds: number, items: number): "checked" | "error" {
  return feeds > 0 && items > 0 ? "checked" : "error";
}

/** 국내 뉴스 매칭용 별칭 (docs/infra.md 25.840, 0043). 사람이 관리하는 표 */
export const KR_ALIASES = `SELECT a.stock_id, a.alias FROM stock_aliases a CROSS JOIN stocks s ON s.id = a.stock_id WHERE s.country = 'KR'`;

/**
 * 대상 종목에 별칭을 **같은 종목 번호의 다른 이름**으로 더한다 (25.840). 매칭 규칙(3글자 이상·단어 경계·조사)은 약칭과 같고,
 * `matchStocks` 가 종목 번호로 한 번만 돌려준다. 대상이 아닌 종목의 별칭은 더하지 않는다
 */
export function withAliases(stocks: NamedStock[], aliases: Array<{ stock_id: number; alias: string }>): NamedStock[] {
  const 대상 = new Map(stocks.map((s) => [s.stock_id, s]));
  const 더함: NamedStock[] = [];
  for (const a of aliases) {
    const 원래 = 대상.get(Number(a.stock_id));
    if (원래 && a.alias && a.alias.trim() !== 원래.name.trim()) 더함.push({ ...원래, name: a.alias.trim() });
  }
  return [...stocks, ...더함];
}
