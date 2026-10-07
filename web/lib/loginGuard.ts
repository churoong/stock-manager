/**
 * 로그인 시도 제한.
 *
 * 비밀번호가 짧아도 쓸 수 있게 하는 장치다. 네 자리 숫자는 경우의 수가
 * 1만 가지라 제한이 없으면 몇 시간이면 뚫린다. 아래 기준이면 한 주소에서
 * 15분에 5번만 시도할 수 있어 1만 가지를 다 훑는 데 500시간이 걸린다.
 *
 * 두 겹으로 막는다.
 *   1. 주소별: 15분에 5번. 공격자 한 명을 늦춘다
 *   2. 전체: 1시간에 20번. 여러 주소로 나눠 들어와도 막고 텔레그램으로 알린다
 *
 * 전체 잠금은 본인도 못 들어오게 하지만, 그 상황이면 알아야 할 일이다.
 * 잠금은 시간이 지나면 저절로 풀린다.
 */


import { execute, rowsToObjects } from "@/lib/db";
import { TelegramUncertainError, sendTelegram } from "@/lib/telegram";

export const PER_IP_WINDOW_MINUTES = 15;
export const PER_IP_MAX_FAILURES = 5;

export const GLOBAL_WINDOW_MINUTES = 60;
export const GLOBAL_MAX_FAILURES = 20;

const CLEANUP_AFTER_HOURS = 24;

export type GuardVerdict =
  | { allowed: true; recentFailures: number }
  | { allowed: false; reason: "ip" | "global" | "unavailable"; retryAfterSeconds: number; memory?: boolean };

/** 표가 아직 없다(마이그레이션 전)는 오류인가. 그때만 막지 않는다 (docs/infra.md 25.393) */
function 표가_없나(error: unknown): boolean {
  return /no such table/i.test(error instanceof Error ? error.message : String(error));
}

/** 요청을 보낸 주소를 찾는다. 프록시 뒤에 있으므로 헤더를 본다. */
export function clientIp(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  if (forwarded) {
    // 여러 개가 쉼표로 이어져 온다. 맨 앞이 원래 요청자다.
    return forwarded.split(",")[0].trim();
  }
  return request.headers.get("x-real-ip")?.trim() || "unknown";
}

/** 주소를 그대로 저장하지 않는다. 서명키를 섞어 해시한다. */
export async function hashIp(ip: string): Promise<string> {
  const salt = process.env.AUTH_SECRET ?? "";
  const data = new TextEncoder().encode(`${salt}:${ip}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, 32);
}

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

/**
 * 지금 시도해도 되는지 본다.
 *
 * 테이블이 아직 없으면 막지 않는다. 마이그레이션 전에 로그인 자체가
 * 불가능해지는 것이 더 나쁘다.
 */
export async function checkGuard(ipHash: string): Promise<GuardVerdict> {
  try {
    const [perIp, global] = await Promise.all([
      execute(
        "SELECT COUNT(*) AS n, MIN(attempted_at) AS oldest FROM login_attempts" +
          " WHERE ip_hash = ? AND success = 0 AND attempted_at > ?",
        [ipHash, minutesAgo(PER_IP_WINDOW_MINUTES)],
      ),
      execute(
        "SELECT COUNT(*) AS n, MIN(attempted_at) AS oldest FROM login_attempts" +
          " WHERE success = 0 AND attempted_at > ?",
        [minutesAgo(GLOBAL_WINDOW_MINUTES)],
      ),
    ]);

    const ipRow = rowsToObjects<{ n: number; oldest: string | null }>(perIp)[0];
    const globalRow = rowsToObjects<{ n: number; oldest: string | null }>(global)[0];

    if (globalRow.n >= GLOBAL_MAX_FAILURES) {
      return {
        allowed: false,
        reason: "global",
        retryAfterSeconds: retryAfter(globalRow.oldest, GLOBAL_WINDOW_MINUTES),
      };
    }

    if (ipRow.n >= PER_IP_MAX_FAILURES) {
      return {
        allowed: false,
        reason: "ip",
        retryAfterSeconds: retryAfter(ipRow.oldest, PER_IP_WINDOW_MINUTES),
      };
    }

    return { allowed: true, recentFailures: ipRow.n };
  } catch (error) {
    // **표가 없을 때만** 막지 않는다 (docs/infra.md 25.393). 마이그레이션 전에 본인마저 못 들어오는 것이 더 나쁘다.
    // 그 밖의 실패(한도·일시 장애)에 통과시키면 그동안 네 자리 비밀번호에 시도 제한이 없다 — 막는다
    if (표가_없나(error)) return { allowed: true, recentFailures: 0 };
    return { allowed: false, reason: "unavailable", retryAfterSeconds: 60 };
  }
}

/**
 * **DB 를 못 읽을 때의 시도 제한 — 서버 메모리** (docs/infra.md 25.843, 2026-10-01 사용자 화면).
 *
 * 25.393 은 시도 기록을 못 읽으면 막았다(통과시키면 그동안 시도 제한이 없다). 그런데 D1 하루 **읽기** 한도를 넘긴 날은 UTC 자정까지
 * 계속 못 읽어 **본인도 18시간 동안 로그인할 수 없었다**("시도 기록을 확인할 수 없어 잠시 막았습니다"). 막는 대신 이 함수 인스턴스의
 * 메모리로 더 엄격하게 센다 — 주소별 15분 3번, 전체 1시간 5번(25.846). 서버리스 인스턴스가 바뀌면 지워지지만, 네 자리 1만 가지를 훑으려면
 * 여전히 오래 걸리고 DB 가 돌아오면 원래 기준으로 돌아간다
 */
export const MEMORY_PER_IP_MAX_FAILURES = 3;
// 인스턴스마다 따로 세므로 DB 기준(20)보다 훨씬 낮게 — 인스턴스가 여럿 떠도 합이 DB 기준 근처에 머물게 (25.846, 교차검증)
export const MEMORY_GLOBAL_MAX_FAILURES = 5;
const 메모리_주소별 = new Map<string, number[]>();
let 메모리_전체: number[] = [];

/** `record=false` 면 판정만 하고 적지 않는다 — DB 를 못 읽는 날 잠긴 요청을 DB 에 쓰기 전에 돌려보낼 때 (25.851) */
export function memoryReserve(ipHash: string, now: number = Date.now(), record = true): GuardVerdict {
  const 주소창 = now - PER_IP_WINDOW_MINUTES * 60_000;
  const 전체창 = now - GLOBAL_WINDOW_MINUTES * 60_000;
  const 주소 = (메모리_주소별.get(ipHash) ?? []).filter((t) => t > 주소창);
  메모리_전체 = 메모리_전체.filter((t) => t > 전체창);
  if (메모리_전체.length >= MEMORY_GLOBAL_MAX_FAILURES) {
    return { allowed: false, reason: "global", memory: true, retryAfterSeconds: Math.max(1, Math.ceil((메모리_전체[0] + GLOBAL_WINDOW_MINUTES * 60_000 - now) / 1000)) };
  }
  if (주소.length >= MEMORY_PER_IP_MAX_FAILURES) {
    메모리_주소별.set(ipHash, 주소);
    return { allowed: false, reason: "ip", memory: true, retryAfterSeconds: Math.max(1, Math.ceil((주소[0] + PER_IP_WINDOW_MINUTES * 60_000 - now) / 1000)) };
  }
  if (!record) return { allowed: true, recentFailures: 주소.length };
  // 먼저 실패로 적어 둔다 — 맞으면 `memoryClear` 가 지운다 (DB 쪽 25.393 과 같은 순서)
  주소.push(now);
  메모리_주소별.set(ipHash, 주소);
  메모리_전체.push(now);
  return { allowed: true, recentFailures: 주소.length - 1 };
}

/** 로그인 성공 — 그 주소의 메모리 실패를 지운다(전체 몫에서도 하나) */
export function memoryClear(ipHash: string): void {
  const 주소 = 메모리_주소별.get(ipHash) ?? [];
  메모리_주소별.delete(ipHash);
  const 마지막 = 주소.at(-1);
  if (마지막 !== undefined) {
    const i = 메모리_전체.lastIndexOf(마지막);
    if (i >= 0) 메모리_전체.splice(i, 1);
  }
}

/** 시험용 */
export function memoryReset(): void {
  메모리_주소별.clear();
  메모리_전체 = [];
  메모리_알림시각 = null;
  lockMemoryReset();
}

/** DB 장부를 못 적을 때 쓰는 알림 쿨다운 — 인스턴스 메모리 (25.846) */
let 메모리_알림시각: number | null = null;

/**
 * **먼저 실패로 적어 두고** 센다 (docs/infra.md 25.393).
 *
 * 예전 순서는 "센다 → 비밀번호 검사 → 실패 기록" 이었다. 사이에 잠금이 없어, 1분 안에 수천 건을 **동시에**
 * 보내면 모두 0건을 읽고 전부 비밀번호 검사까지 갔다 — 네 자리 1만 가지를 한두 묶음에 훑을 수 있었다.
 * 이제 들어오자마자 실패 한 줄을 넣고 **자기 줄까지 세어** 판정한다. 동시에 들어온 요청은 서로의 줄을 보게 된다.
 * 성공하면 `recordAttempt(…, true)` 가 그 주소의 실패(이 줄 포함)를 지운다.
 */
/**
 * **DB 가 이미 잠갔다고 판정한 것을 이 인스턴스가 기억한다** (docs/infra.md 25.914, 감사). 잠긴 뒤에도 요청마다 읽기 세 번(주소·전체 COUNT,
 * 정확한 대기 OFFSET)이 돌아, 로그인 없이 두드리기만 해도 DB 읽기 한도를 태울 수 있었다 — 한도가 바닥나면 앱 전체가 비고(25.843)
 * 시도 제한도 인스턴스 메모리로 약해진다. DB 판정을 그대로 옮겨 두기만 하므로 더 느슨해지지 않는다(잠금 시각까지만 기억)
 */
const 잠금_주소별 = new Map<string, number>();
let 잠금_전체 = 0;

function 기억한_잠금(ipHash: string, now: number): GuardVerdict | null {
  if (잠금_전체 > now) return { allowed: false, reason: "global", retryAfterSeconds: Math.max(1, Math.ceil((잠금_전체 - now) / 1000)) };
  const 주소 = 잠금_주소별.get(ipHash);
  if (주소 !== undefined && 주소 > now) return { allowed: false, reason: "ip", retryAfterSeconds: Math.max(1, Math.ceil((주소 - now) / 1000)) };
  if (주소 !== undefined) 잠금_주소별.delete(ipHash);
  return null;
}

function 잠금_기억(ipHash: string, verdict: GuardVerdict, now: number): GuardVerdict {
  if (verdict.allowed || verdict.reason === "unavailable" || verdict.memory) return verdict;
  const 끝 = now + verdict.retryAfterSeconds * 1000;
  if (verdict.reason === "global") 잠금_전체 = Math.max(잠금_전체, 끝);
  else {
    if (잠금_주소별.size > 1000) 잠금_주소별.clear(); // 주소를 바꿔 가며 두드려도 메모리가 끝없이 늘지 않게
    잠금_주소별.set(ipHash, Math.max(잠금_주소별.get(ipHash) ?? 0, 끝));
  }
  return verdict;
}

/** 시험용 */
export function lockMemoryReset(): void {
  잠금_주소별.clear();
  잠금_전체 = 0;
}

export async function reserveAttempt(ipHash: string): Promise<GuardVerdict> {
  const 기억 = 기억한_잠금(ipHash, Date.now());
  if (기억) return 기억; // DB 를 읽지 않는다 (25.914)
  return 잠금_기억(ipHash, await reserveAttemptDb(ipHash), Date.now());
}

async function reserveAttemptDb(ipHash: string): Promise<GuardVerdict> {
  // **이미 잠겼으면 적지 않는다** (docs/infra.md 25.594, 감사). 25.393 뒤로 잠긴 뒤 온 요청도 실패 한 줄씩 쌓여, 공격을 멈춘 뒤에도
  // 전체 잠금이 최대 60분 늘어났고(본인도 못 들어옴) 안내한 대기 시간이 틀렸다(1분 뒤 → 여전히 15분). 잠긴 요청마다 쓰기도 한 번씩 태웠다.
  // 먼저 읽기만으로 보고(쓰기 없음), 잠겼으면 그대로 돌려보낸다
  const 먼저 = await checkGuardRaw(ipHash).catch(() => "unavailable" as const);
  if (먼저 !== "unavailable") {
    const 앞판정 = verdictOf(먼저, 0);
    if (!앞판정.allowed) return 정확한_대기(앞판정, ipHash, 먼저, 0);
  } else {
    // 못 읽는 날은 메모리 잠금을 **쓰기 전에** 본다 (25.851, 교차검증). 예전에는 잠긴 요청마다 실패 한 줄을 D1 에 쓴 뒤 메모리가 막아,
    // 두드리는 만큼 하루 쓰기 한도(10만)를 태웠고 읽기가 돌아온 아침 DB 전체 잠금까지 이어졌다(30번 두드리자 DB 행 30)
    const 앞판정 = memoryReserve(ipHash, Date.now(), false);
    if (!앞판정.allowed) return 앞판정;
  }
  const 시각 = new Date().toISOString();
  try {
    await execute("INSERT INTO login_attempts (ip_hash, attempted_at, success) VALUES (?, ?, 0)", [ipHash, 시각]);
  } catch (error) {
    if (표가_없나(error)) return { allowed: true, recentFailures: 0 };
    return memoryReserve(ipHash); // 못 쓰면 메모리로 센다 (25.843)
  }
  // 동시에 들어와 앞 읽기를 함께 통과한 요청의 줄은 **거두지 않는다** — 거두면 늦게 센 요청이 문턱 아래로 보고 통과해 25.393 이 막은
  // 동시 대입이 되살아난다. 한꺼번에 몰린 만큼만 남고, 잠긴 뒤 하나씩 오는 요청은 위에서 적지 않고 돌아선다
  return checkGuardCounting(ipHash, 1);
}

/** 이미 넣은 자기 줄(`own`)을 빼고 문턱과 견준다 */
async function checkGuardCounting(ipHash: string, own: number): Promise<GuardVerdict> {
  const v = await checkGuardRaw(ipHash);
  // 못 읽으면 막지 않고 메모리로 더 엄격하게 센다 — D1 읽기 한도 날 본인이 18시간 못 들어왔다 (25.843)
  if (v === "unavailable") return memoryReserve(ipHash);
  return 정확한_대기(verdictOf(v, own), ipHash, v, own);
}

/**
 * **잠금이 실제로 풀리는 때**를 센다 (docs/infra.md 25.596, 교차검증). `retryAfter(MIN(attempted_at))` 는 가장 오래된 실패가 창을 빠지는 때라,
 * 한꺼번에 몰린 실패가 있으면 "1분 뒤" 라고 해 놓고 그 뒤에도 문턱 위였다. 풀리는 때는 (실패 수 − 문턱 + 1)번째로 오래된 실패가 빠지는 때다.
 * 잠겼을 때만 한 번 더 읽는다
 */
async function 정확한_대기(
  verdict: GuardVerdict,
  ipHash: string,
  v: { ip: { n: number }; global: { n: number } },
  own: number,
): Promise<GuardVerdict> {
  if (verdict.allowed || verdict.reason === "unavailable") return verdict;
  const 전체 = verdict.reason === "global";
  // 자기 줄(`own`)도 거두지 않으므로 다음 요청이 볼 실패 수는 n 그대로다 — own 을 빼면 한 칸 모자라 안내가 이르다 (25.598, 교차검증)
  void own;
  const 넘친수 = (전체 ? v.global.n : v.ip.n) - (전체 ? GLOBAL_MAX_FAILURES : PER_IP_MAX_FAILURES);
  const 창 = 전체 ? GLOBAL_WINDOW_MINUTES : PER_IP_WINDOW_MINUTES;
  try {
    const 행 = rowsToObjects<{ t: string }>(
      await execute(
        전체
          ? "SELECT attempted_at AS t FROM login_attempts WHERE success = 0 AND attempted_at > ? ORDER BY attempted_at LIMIT 1 OFFSET ?"
          : "SELECT attempted_at AS t FROM login_attempts WHERE ip_hash = ? AND success = 0 AND attempted_at > ? ORDER BY attempted_at LIMIT 1 OFFSET ?",
        전체 ? [minutesAgo(창), Math.max(0, 넘친수)] : [ipHash, minutesAgo(창), Math.max(0, 넘친수)],
      ),
    )[0];
    return 행 ? { ...verdict, retryAfterSeconds: retryAfter(행.t, 창) } : verdict;
  } catch {
    return verdict; // 못 세면 가장 오래된 실패 기준(짧게 말할 수 있다)을 그대로 — 판정 자체는 이미 잠금이다
  }
}

function verdictOf(
  v: { ip: { n: number; oldest: string | null }; global: { n: number; oldest: string | null } },
  own: number,
): GuardVerdict {
  const ip = v.ip.n - own;
  const global = v.global.n - own;
  if (global >= GLOBAL_MAX_FAILURES) {
    return { allowed: false, reason: "global", retryAfterSeconds: retryAfter(v.global.oldest, GLOBAL_WINDOW_MINUTES) };
  }
  if (ip >= PER_IP_MAX_FAILURES) {
    return { allowed: false, reason: "ip", retryAfterSeconds: retryAfter(v.ip.oldest, PER_IP_WINDOW_MINUTES) };
  }
  return { allowed: true, recentFailures: ip };
}

async function checkGuardRaw(
  ipHash: string,
): Promise<{ ip: { n: number; oldest: string | null }; global: { n: number; oldest: string | null } } | "unavailable"> {
  try {
    const [perIp, global] = await Promise.all([
      execute(
        "SELECT COUNT(*) AS n, MIN(attempted_at) AS oldest FROM login_attempts" +
          " WHERE ip_hash = ? AND success = 0 AND attempted_at > ?",
        [ipHash, minutesAgo(PER_IP_WINDOW_MINUTES)],
      ),
      execute(
        "SELECT COUNT(*) AS n, MIN(attempted_at) AS oldest FROM login_attempts" +
          " WHERE success = 0 AND attempted_at > ?",
        [minutesAgo(GLOBAL_WINDOW_MINUTES)],
      ),
    ]);
    return {
      ip: rowsToObjects<{ n: number; oldest: string | null }>(perIp)[0],
      global: rowsToObjects<{ n: number; oldest: string | null }>(global)[0],
    };
  } catch {
    return "unavailable";
  }
}

/** 실패로 끝난 뒤 오래된 기록만 치운다 — 실패 줄은 `reserveAttempt` 가 이미 넣었다 (25.393) */
export async function cleanupAttempts(): Promise<void> {
  try {
    await execute("DELETE FROM login_attempts WHERE attempted_at < ?", [minutesAgo(CLEANUP_AFTER_HOURS * 60)]);
  } catch {
    // 치우기가 실패해도 판정은 이미 끝났다
  }
}

/** 창이 끝날 때까지 남은 초. 가장 오래된 실패가 빠지면 다시 시도할 수 있다. */
function retryAfter(oldest: string | null, windowMinutes: number): number {
  if (!oldest) return windowMinutes * 60;
  const expiresAt = new Date(oldest).getTime() + windowMinutes * 60_000;
  return Math.max(1, Math.ceil((expiresAt - Date.now()) / 1000));
}

export async function recordAttempt(ipHash: string, success: boolean): Promise<void> {
  if (success) memoryClear(ipHash); // DB 를 못 읽던 동안 메모리로 센 실패도 지운다 (25.843)
  try {
    const now = new Date().toISOString();

    if (success) {
      // 들어왔으면 그 주소의 실패 기록을 지운다. 다음에 깨끗한 상태로 시작한다.
      await execute("DELETE FROM login_attempts WHERE ip_hash = ?", [ipHash]);
    }

    await execute(
      "INSERT INTO login_attempts (ip_hash, attempted_at, success) VALUES (?, ?, ?)",
      [ipHash, now, success ? 1 : 0],
    );

    // 오래된 기록은 지운다. 쌓아 둘 이유가 없다.
    await execute("DELETE FROM login_attempts WHERE attempted_at < ?", [
      minutesAgo(CLEANUP_AFTER_HOURS * 60),
    ]);
  } catch {
    // 기록에 실패해도 로그인 판정 자체는 이미 끝났다. 조용히 넘어간다.
  }
}

/**
 * 실패가 쌓일수록 응답을 늦춘다.
 *
 * 잠기기 전까지도 공격자를 늦추는 효과가 있다. 본인이 한두 번 틀리는
 * 경우에는 거의 체감되지 않는다.
 */
export function failureDelayMs(recentFailures: number): number {
  const steps = [400, 700, 1200, 2000, 3000];
  return steps[Math.min(recentFailures, steps.length - 1)];
}

/** 전체 잠금 알림을 마지막으로 보낸 시각. `settings` 는 이미 운영용 열쇠를 담고 있다 */
const ALERT_KEY = "login_global_lock_alerted_at";

/**
 * 알림을 보낼 권리를 **원자적으로** 잡는다. 잡았으면 true.
 *
 * 잠긴 동안 **하나씩** 오는 요청은 기록하지 않으므로(`reserveAttempt` 가 먼저 읽어 429 로 돌려세운다 — 25.594) 실패 수가
 * 문턱에 멈춰 있다. 동시에 몰린 요청의 줄은 거두지 않는다(거두면 동시 대입이 되살아난다) — 몰아 보내면 잠금이 새로 걸린다(25.596 에 적음). 그래서 **잠긴 뒤 들어오는 모든 요청이 똑같이 "잠금" 판정**을 받는다.
 * 공격자가 계속 두드리면 그 수만큼 텔레그램이 날아간다 — 폰이 알림으로 뒤덮이고,
 * 정작 그 사이에 오는 아침 리포트가 묻히며, 봇이 텔레그램 쪽 제한에 걸릴 수 있다.
 * **알림이 공격을 증폭시키는 꼴이다.**
 *
 * 한 잠금 창에 한 번만 보낸다. `ON CONFLICT ... WHERE` 는 SQLite 안에서 한 번에
 * 판정되므로, 같은 순간 여러 요청이 들어와도 **한 쪽만** 행을 바꾼다. 읽고 나서
 * 쓰면 그 사이에 둘 다 "아직 안 보냈다" 를 보고 둘 다 보낸다.
 *
 * `affectedRows` 는 **0 인지 아닌지만** 본다. 2026-09-23 에 D1 쪽이 `meta.changes`
 * (바뀐 행)를 먼저 보게 바뀌었지만(25.152), 그 칸이 안 올 때는 `rows_written`
 * (인덱스까지 쓴 행)으로 떨어진다 — 그때 1 과 비교하면 백엔드에 따라 틀린다
 * (docs/infra.md 25.20·25.152). **여기서는 0 인지만 보면 충분하다.**
 */
async function claimAlert(now: Date): Promise<boolean> {
  const stamp = now.toISOString();
  const cooldownBefore = new Date(now.getTime() - GLOBAL_WINDOW_MINUTES * 60_000).toISOString();
  try {
    const rs = await execute(
      "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)" +
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at" +
        " WHERE settings.updated_at < ?",
      [ALERT_KEY, JSON.stringify(stamp), stamp, cooldownBefore],
    );
    return rs.affectedRows !== 0;
  } catch {
    // 표가 없거나 DB 가 안 되는 경우. **보내되 메모리로 한 창에 한 번만** (25.846, 교차검증) — 예전에는 늘 보내서, DB 를 못 읽는 날
    // 메모리 전체 잠금이 걸리면 잠긴 요청마다 텔레그램이 나갔다(25개 주소로 두드리자 15번). 장부를 못 적었다고 알림을 잃으면 안 되지만
    // 알림이 공격을 증폭해서도 안 된다
    const t = now.getTime();
    if (메모리_알림시각 !== null && t - 메모리_알림시각 < GLOBAL_WINDOW_MINUTES * 60_000) return false;
    메모리_알림시각 = t;
    return true;
  }
}

/**
 * 보내지 못한 알림의 장부를 되돌린다 — 다음 잠금 요청이 다시 보내게 (docs/infra.md 25.931).
 * 이 시각에 적은 것만 되돌린다. 그사이 다른 요청이 적은 것은 건드리지 않는다
 */
async function releaseAlert(now: Date): Promise<void> {
  const stamp = now.toISOString();
  if (메모리_알림시각 === now.getTime()) 메모리_알림시각 = null;
  try {
    await execute("UPDATE settings SET updated_at = ? WHERE key = ? AND updated_at = ?", [
      "1970-01-01T00:00:00.000Z",
      ALERT_KEY,
      stamp,
    ]);
  } catch {
    // 장부를 못 고치면 이 창은 조용하다 — 다음 창에 다시 보낸다
  }
}

/**
 * 전체 잠금이 걸렸을 때 알린다. 누군가 시도하고 있다는 뜻이다.
 *
 * **공용 발송기(`sendTelegram`)로 보낸다** (docs/infra.md 25.931, 감사). 예전에는 따로 `fetch` 를 불러 다른 발송 경로가 받은
 * 고침을 다 비켜 갔다 — 대화방 번호 끝 공백(25.587), 제한 시간(25.595), 응답의 `ok:false`(429·잘못된 토큰). 장부를 먼저 적고
 * 실패를 읽지 않아, 못 보낸 알림이 한 창(15분) 동안 다시 오지 않았다. 이제 확실히 실패하면 장부를 되돌린다.
 * 보냈는지 모르는 경우(`TelegramUncertainError`)는 되돌리지 않는다 — 두 번 가는 쪽보다 조용한 쪽을 고른다(25.618 과 같은 판단)
 */
export async function alertGlobalLock(now: Date = new Date(), memory = false): Promise<void> {
  if (!process.env.TELEGRAM_BOT_TOKEN || !process.env.TELEGRAM_CHAT_ID?.trim()) return;

  if (!(await claimAlert(now))) return;

  try {
    await sendTelegram(
      // 숫자를 문장에 박아 두면 문턱을 바꿨을 때 알림이 거짓말을 한다
      `로그인 실패가 ${GLOBAL_WINDOW_MINUTES}분에 ${memory ? MEMORY_GLOBAL_MAX_FAILURES : GLOBAL_MAX_FAILURES}번을 넘어` +
        " 전체 잠금이 걸렸습니다.\n" +
        // 실제로 걸린 기준을 말한다 — DB 를 못 읽는 날은 메모리 기준이다 (25.846)
        (memory ? "(DB 를 읽지 못해 서버 메모리로 센 더 엄격한 기준입니다.)\n" : "") +
        "본인이 아니라면 누군가 비밀번호를 맞춰 보고 있는 것입니다.\n" +
        `잠금은 ${GLOBAL_WINDOW_MINUTES}분 뒤 저절로 풀립니다.` +
        " 비밀번호를 바꾸는 것을 권합니다.\n" +
        `이 알림은 ${GLOBAL_WINDOW_MINUTES}분에 한 번만 옵니다.`,
    );
  } catch (e) {
    // 알림 실패가 로그인 처리를 막으면 안 된다
    console.error("전체 잠금 알림 실패:", e instanceof Error ? e.message : e);
    if (!(e instanceof TelegramUncertainError)) await releaseAlert(now);
  }
}
