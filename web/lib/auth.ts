/**
 * 인증.
 *
 * 이 앱은 사용자가 한 명이다. 회원가입도 비밀번호 재설정도 없다.
 * 환경변수에 둔 비밀번호 하나로 들어오고, 서명한 쿠키로 상태를 유지한다.
 *
 * 왜 이렇게 하나
 *   - 외부 인증 서비스를 쓰면 계정이 하나 더 늘고 무료 조건을 다시 확인해야 한다
 *   - 한국거래소 약관이 데이터의 제3자 제공을 금지하므로 인증 자체는 반드시 있어야 한다
 *   - 나중에 구글 로그인으로 바꾸려면 이 파일의 세션 발급·검증만 갈아 끼우면 된다
 *
 * 쿠키는 서명만 하고 암호화하지 않는다. 안에 담는 것은 만료 시각뿐이라
 * 남이 읽어도 얻을 게 없다. 위조는 서명이 막는다.
 * 세션을 모두 끊으려면 `APP_PASSWORD` 나 `AUTH_SECRET` 을 바꾼다 (서명 키에 둘 다 들어간다).
 */

const COOKIE_NAME = "sm_session";
const SESSION_DAYS = 30;

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`환경변수 ${name} 이(가) 비어 있습니다`);
  }
  return value;
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function fromBase64Url(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded + "=".repeat((4 - (padded.length % 4)) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

/**
 * 서명 키. 비밀번호를 함께 섞는다 (docs/infra.md 25.395).
 *
 * 토큰 안에는 만료 시각뿐이라, 비밀번호를 바꿔도 이미 새어 나간 쿠키가 30일 살아 있었다.
 * 비밀번호를 키에 섞으면 `APP_PASSWORD` 를 바꾸는 순간 그 전 세션이 모두 끊긴다.
 * 비밀번호 자체는 토큰에 들어가지 않는다 — HMAC 키로만 쓴다.
 */
function signingKey(): string {
  return `${required("AUTH_SECRET")}\n${process.env.APP_PASSWORD ?? ""}`;
}

async function hmac(message: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(signingKey()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message),
  );
  return new Uint8Array(signature);
}

/** 길이와 내용이 같은지 시간 차이를 드러내지 않고 비교한다. */
function timingSafeEqual(a: string, b: string): boolean {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  // 길이가 다르면 즉시 실패지만, 비교 자체는 끝까지 돌려 시간 차이를 없앤다
  let diff = left.length ^ right.length;
  const max = Math.max(left.length, right.length);
  for (let i = 0; i < max; i += 1) {
    diff |= (left[i] ?? 0) ^ (right[i] ?? 0);
  }
  return diff === 0;
}

export async function createSessionToken(
  now: number = Date.now(),
): Promise<string> {
  const expiresAt = now + SESSION_DAYS * 24 * 60 * 60 * 1000;
  const payload = toBase64Url(
    new TextEncoder().encode(JSON.stringify({ exp: expiresAt })),
  );
  const signature = toBase64Url(await hmac(payload));
  return `${payload}.${signature}`;
}

export async function verifySessionToken(
  token: string | undefined,
  now: number = Date.now(),
): Promise<boolean> {
  if (!token) return false;

  const dot = token.lastIndexOf(".");
  if (dot <= 0) return false;

  const payload = token.slice(0, dot);
  const signature = token.slice(dot + 1);
  // 세션 쪽도 글자를 가른다 (25.1425, 교차검증) — 기기 표를 `device.<payload>` 로 다시 감싸면 HMAC 은 통과하고 해독기의 엄격함만이
  // 막고 있었다. base64url 글자만 받는다
  if (!/^[A-Za-z0-9_-]+$/.test(payload)) return false;

  const expected = toBase64Url(await hmac(payload));
  if (!timingSafeEqual(signature, expected)) return false;

  try {
    const decoded = JSON.parse(
      new TextDecoder().decode(fromBase64Url(payload)),
    );
    return typeof decoded.exp === "number" && decoded.exp > now;
  } catch {
    return false;
  }
}

/**
 * **기기 표** (docs/infra.md 25.1424, 보안 감사 #1). 로그인에 성공한 브라우저에 1년짜리 서명 쿠키를 준다. 안에는 무작위 기기 번호와
 * 만료 시각뿐이다. 로그인 경로(`lib/loginGuard.reserveAttempt` 의 `trustedDevice`)가 이것이 맞는 요청은 **전체 잠금을 보지 않고** 기기
 * 번호로 센다 — 남이 전체 잠금을 계속 걸어 두어도 전에 들어온 기기에서는 들어올 수 있다. 세션 쿠키로는 쓸 수 없다: 서명을
 * `device.` 를 앞에 붙인 글에 하므로 세션 검증(`verifySessionToken`)의 서명과 다르다. 서명 키에 비밀번호가 섞여 있어 비밀번호를 바꾸면
 * 기기 표도 모두 무효다
 */
const DEVICE_DAYS = 365;

export async function createDeviceToken(deviceId: string, now: number = Date.now()): Promise<string> {
  const payload = toBase64Url(
    new TextEncoder().encode(JSON.stringify({ dev: deviceId, exp: now + DEVICE_DAYS * 24 * 60 * 60 * 1000 })),
  );
  return `${payload}.${toBase64Url(await hmac(`device.${payload}`))}`;
}

/** 맞으면 기기 번호, 아니면 null */
export async function verifyDeviceToken(token: string | undefined, now: number = Date.now()): Promise<string | null> {
  if (!token) return null;
  const dot = token.lastIndexOf(".");
  if (dot <= 0) return null;
  const payload = token.slice(0, dot);
  if (!timingSafeEqual(token.slice(dot + 1), toBase64Url(await hmac(`device.${payload}`)))) return null;
  try {
    const decoded = JSON.parse(new TextDecoder().decode(fromBase64Url(payload)));
    return typeof decoded.dev === "string" && /^[A-Za-z0-9_-]{16,64}$/.test(decoded.dev) && typeof decoded.exp === "number" && decoded.exp > now
      ? decoded.dev
      : null;
  } catch {
    return null;
  }
}

/** 새 기기 번호 — 128비트 무작위 */
export function newDeviceId(): string {
  return toBase64Url(crypto.getRandomValues(new Uint8Array(16)));
}

export const DEVICE_COOKIE = "sm_device";
export const deviceCookieOptions = {
  name: DEVICE_COOKIE,
  httpOnly: true,
  sameSite: "strict" as const,
  secure: process.env.NODE_ENV === "production",
  // 로그인 경로에만 실려 간다 — 다른 요청에는 필요 없다
  path: "/api/auth/login",
  maxAge: DEVICE_DAYS * 24 * 60 * 60,
};

/**
 * 비밀번호 최소 길이.
 *
 * 공개 주소에 걸리는 앱이라 원래는 16자 이상을 요구했다.
 * 짧은 비밀번호를 쓰기로 하면서 기준을 낮추는 대신, 시도 횟수 제한을 붙였다.
 * lib/loginGuard.ts 가 그 역할을 한다. 둘 중 하나만 있으면 안 된다.
 */
export const MIN_PASSWORD_LENGTH = 4;

/** 로그인 비밀번호를 확인한다. 값이 맞는지만 보고 이유는 밝히지 않는다. */
export function checkPassword(input: string): boolean {
  const expected = process.env.APP_PASSWORD ?? "";
  if (expected.length < MIN_PASSWORD_LENGTH) {
    // 설정이 비었거나 너무 짧으면 아무도 들어올 수 없다.
    return false;
  }
  return timingSafeEqual(input, expected);
}

export const SESSION_COOKIE = COOKIE_NAME;
export const SESSION_MAX_AGE = SESSION_DAYS * 24 * 60 * 60;

export const cookieOptions = {
  name: COOKIE_NAME,
  httpOnly: true,
  sameSite: "lax" as const,
  secure: process.env.NODE_ENV === "production",
  path: "/",
  maxAge: SESSION_MAX_AGE,
};

export { timingSafeEqual };
