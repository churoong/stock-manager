import { cachedFetch, clearClientCache } from "@/lib/clientCache";
/**
 * 관심 종목 등록·해제 (docs/intraday.md 1장). 추천 카드와 종목 상세에서 한 번에 누른다.
 *
 * 관심 종목은 장중 경로가 watchlist 에서 바로 읽으므로 등록하면 다음 5분 호출부터 감시한다.
 * 실패는 값으로 돌려준다 — 던지지 않는다 (lib/http 와 같은 원칙).
 */

export interface WatchResult {
  ok: boolean;
  error: string | null;
}

async function call(url: string, init: RequestInit, fetchImpl: typeof fetch): Promise<WatchResult> {
  clearClientCache(); // 관심 종목을 바꾸면 화면 캐시를 버린다 (25.879)
  try {
    const res = await fetchImpl(url, init);
    if (res.ok) return { ok: true, error: null };
    let message = `HTTP ${res.status}`;
    try {
      const body = (await res.json()) as { errors?: string[] };
      if (body?.errors?.length) message = body.errors.join(", ");
    } catch {
      // 본문이 JSON 이 아니면 상태 코드만
    }
    return { ok: false, error: message };
  } catch {
    return { ok: false, error: "서버에 연결하지 못했습니다" };
  }
}

export function addWatch(stockId: number, fetchImpl: typeof fetch = fetch): Promise<WatchResult> {
  return call(
    "/api/watchlist",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stock_id: stockId }) },
    fetchImpl,
  );
}

/** 관심 행이 없을 때 경로가 돌려주는 말 — 지우기에서는 바라던 결과라 성공으로 본다 (25.817) */
export const WATCH_GONE = "그 관심 종목이 없습니다 — 이미 빠졌을 수 있습니다";

export async function removeWatch(watchId: number, fetchImpl: typeof fetch = fetch): Promise<WatchResult> {
  const r = await call(`/api/watchlist/${watchId}`, { method: "DELETE" }, fetchImpl);
  // 다른 탭에서 이미 뺀 종목이면 "해제 실패" 가 아니다 — 25.814 의 404 가 단추를 "등록됨" 에 묶어 두었다 (25.817, 교차검증)
  if (!r.ok && r.error === WATCH_GONE) return { ok: true, error: null };
  return r;
}

/**
 * 종목 id → 관심 행 id. **목록을 읽지 못하면 null(모름)** — 빈 Map 이면 모든 카드에 "관심 등록" 이 떠 "관심 종목 없음" 처럼 보였고,
 * 종목 상세는 등록 직후 다시 읽기가 실패하면 단추가 "관심 등록" 으로 되돌아갔다 (docs/infra.md 25.815, 감사)
 */
export async function loadWatchIds(fetchImpl?: typeof fetch): Promise<Map<number, number> | null> {
  try {
    // 화면 캐시를 쓴다 (25.893) — 추천·종목 상세에 들어올 때마다 관심 목록을 다시 읽었다. 등록·해제(`call`)가 캐시를 비우므로
    // 바꾼 뒤에는 새로 읽는다
    const res = fetchImpl ? await fetchImpl("/api/watchlist") : await cachedFetch("/api/watchlist");
    if (!res.ok) return null;
    const body = (await res.json()) as { watchlist?: Array<{ id: number; stock_id: number }> };
    return new Map((body.watchlist ?? []).map((w) => [w.stock_id, w.id]));
  } catch {
    return null;
  }
}

/** 목표 매수가가 마지막 종가와 이만큼 넘게 벌어지면 단위를 잘못 넣은 것으로 본다 (docs/infra.md 25.360) */
export const TARGET_PRICE_MAX_RATIO = 10;

/**
 * 목표 매수가가 **단위를 잘못 넣은 값**인가 (docs/infra.md 25.360). 문제가 있으면 사유, 없으면 null.
 *
 * 입력 칸에 통화가 없어 미국 종목에 원화(AAPL 300,000)를 넣을 수 있었다. 장중 판정은 `가격 ≤ 목표가` 라
 * 그날 첫 호출에서 "목표 매수가 도달" 이 나간다 — 거짓 알림이다. 마지막 종가의 10배를 넘거나 1/10 아래면
 * 단위 실수로 보고 막는다(목표가를 그만큼 멀리 두는 것은 알림으로서 뜻이 없다). 종가를 모르면 막지 않는다.
 */
export function targetPriceProblem(target: number | null | undefined, lastClose: number | null | undefined, currency: string): string | null {
  if (target === null || target === undefined || !lastClose || lastClose <= 0) return null;
  const ratio = target / lastClose;
  if (ratio > TARGET_PRICE_MAX_RATIO || ratio < 1 / TARGET_PRICE_MAX_RATIO) {
    const unit = currency === "USD" ? "달러" : currency === "KRW" ? "원" : currency;
    return `목표 매수가 ${target.toLocaleString()} 이 마지막 종가 ${lastClose.toLocaleString()}${unit} 와 너무 멉니다. 이 종목은 ${unit} 단위입니다`;
  }
  return null;
}

/**
 * 목표 매수가가 **마지막 종가 이상**인가 (docs/infra.md 25.584, 감사). 장중 판정은 `가격 ≤ 목표가` 라 곧바로 "목표 매수가 도달" 이
 * 나가고, 가격이 그 아래인 동안 거래일마다 되풀이된다. 장중에 오른 뒤 걸어 둘 수도 있어 막지는 않고 경고한다.
 */
export function targetAboveCloseWarning(target: number | null | undefined, lastClose: number | null | undefined): string | null {
  if (!target || !lastClose || lastClose <= 0 || target < lastClose) return null;
  // 1.5배를 넘으면 장중 감시가 분할 전 값으로 보고 **버린다**(25.600) — "곧바로 알림" 이라 말하면 거짓이다 (25.813, 감사)
  if (target > lastClose * WATCH_PRICE_MAX_RATIO) return watchPriceIgnoredNote(lastClose);
  return `목표 매수가 ${target.toLocaleString()} 이 마지막 종가 ${lastClose.toLocaleString()} 이상입니다 — 오늘 가격이 그 아래면 첫 장중 호출에서 곧바로 "도달" 알림이 나갑니다`;
}

/** 전일 종가 대비 이 배수를 넘는 관심 목표 매수가는 장중 감시가 분할 전 값으로 보고 쓰지 않는다 (25.600·25.602). 정의처는 여기 (25.813) */
export const WATCH_PRICE_MAX_RATIO = 1.5;

/** 장중 감시가 이 목표 매수가를 쓰지 않는다는 말 — 저장 경고와 목록이 같은 글을 쓴다 (25.813) */
export function watchPriceIgnoredNote(lastClose: number): string {
  return `장중 감시는 마지막 종가(${lastClose.toLocaleString()})의 ${WATCH_PRICE_MAX_RATIO}배를 넘는 목표 매수가를 분할 전 값으로 보고 쓰지 않습니다 — 이 값으로는 알림이 나가지 않습니다`;
}

/** 목록의 그 관심 종목이 지금 장중 감시에서 목표가로 빠지는가 — 마지막 종가 기준(장중 경로는 전일 종가, 거의 같다) */
export function watchPriceIgnored(target: number | null, lastClose: number | null): boolean {
  return target !== null && lastClose !== null && lastClose > 0 && target > lastClose * WATCH_PRICE_MAX_RATIO;
}

/** 목표가 검사에 쓰는 종목의 통화와 마지막 종가 */
export const WATCH_PRICE_BASIS = `SELECT s.currency,
  (SELECT close FROM prices WHERE stock_id = s.id AND close IS NOT NULL ORDER BY date DESC LIMIT 1) AS last_close
FROM stocks s WHERE s.id = ?`;

/** 같은 것을 관심 종목 번호로 */
export const WATCH_PRICE_BASIS_BY_WATCH_ID = `SELECT s.currency,
  (SELECT close FROM prices WHERE stock_id = s.id AND close IS NOT NULL ORDER BY date DESC LIMIT 1) AS last_close
FROM stocks s WHERE s.id = (SELECT stock_id FROM watchlist WHERE id = ?)`;
