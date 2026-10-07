/**
 * 국내 / 미국 시장 탭.
 *
 * 모든 화면(오늘의 추천·장기 적립·종목 찾기)이 같은 탭을 쓴다. 한 화면에서 고른
 * 시장을 다른 화면에서도 기억한다 — 미국을 보다가 탭을 옮겼는데 국내로 돌아가 있으면
 * 매번 다시 눌러야 한다(2026-09-17 사용자 요청으로 세로 나열을 탭으로 바꿈).
 *
 * 한국과 미국을 한 목록에 섞지 않는 것은 CLAUDE.md 의 원칙이기도 하다
 * ("한국·미국 종목을 같은 잣대로 직접 비교하지 않는다"). 통화도 다르다.
 */

export type Country = "KR" | "US";

export const MARKET_TABS: Array<{ country: Country; label: string }> = [
  { country: "KR", label: "국내" },
  { country: "US", label: "미국" },
];

/**
 * 그 시장의 **현지 날짜** (YYYY-MM-DD).
 *
 * `market_sessions.date`, `alerts.trade_date`, `health_alerts.local_date` 가 전부 이 값이다.
 * "하루 한 번" 을 보장하는 열쇠라서 틀리면 같은 알림이 두 번 나가거나 한 번도 안 나간다.
 *
 * **고정 오프셋으로 계산하지 않는다** (2026-09-21, docs/infra.md 25.59).
 * `health.ts` 에 미국을 늘 UTC-5 로 보는 사본이 있었다. "날짜 경계만 보므로 -5 로 충분하다"
 * 고 적혀 있었지만, 날짜 경계야말로 오프셋이 바꾸는 바로 그것이다 — 서머타임(EDT, UTC-4)
 * 동안 **매일 04:00~04:59 UTC 한 시간씩** 하루 전 날짜를 돌려줬다. 2026년으로 세어 보니
 * 238시간이다. IANA 시간대에 맡기면 이런 계산을 하지 않아도 된다.
 */
export function localDate(market: Country, now: Date): string {
  const tz = market === "KR" ? "Asia/Seoul" : "America/New_York";
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/**
 * UTC 시각(ISO)을 **사용자의 날짜(한국)** 로 (docs/infra.md 25.240). 앞 10자를 자르면 UTC 날짜라 00~09시 KST 에 하루 이르다.
 * 읽지 못하면 받은 글자의 앞 10자를 그대로 — 지어내지 않는다.
 */
export function userDateOf(ts: string | null | undefined): string {
  if (!ts) return "";
  const t = Date.parse(String(ts));
  return Number.isNaN(t) ? String(ts).slice(0, 10) : localDate("KR", new Date(t));
}

/**
 * UTC 시각(ISO)을 **사용자의 시각(KST)** "YYYY-MM-DD HH:MM" 으로 (docs/infra.md 25.266).
 * 화면 네 곳이 `slice(0, 16).replace("T", " ")` 로 UTC 를 그대로 잘라 보여 줬다 — 08:27 KST 에 만든 리포트가
 * "만든 시각 전날 23:27" 로 보였고, 바로 아래 텔레그램 본문은 "08:27 KST" 였다. 읽지 못하면 받은 글자의 앞 16자를 그대로.
 */
export function userTimeOf(ts: string | null | undefined): string {
  if (!ts) return "";
  const t = Date.parse(String(ts));
  if (Number.isNaN(t)) return String(ts).slice(0, 16).replace("T", " ");
  return new Date(t + 9 * 3_600_000).toISOString().slice(0, 16).replace("T", " ");
}

const STORAGE_KEY = "market.country";

/**
 * 마지막에 본 시장. 저장소가 막혀 있거나(사생활 모드) 서버에서 그릴 때는 국내로 간다.
 * 기억은 편의일 뿐 화면의 전제가 아니다.
 */
export function readSavedCountry(): Country {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "KR" || saved === "US") return saved;
  } catch {
    // 저장소를 못 쓰는 환경이다
  }
  return "KR";
}

export function saveCountry(country: Country): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, country);
  } catch {
    // 기억하지 못해도 된다
  }
}
