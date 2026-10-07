import { MIN_PASSWORD_LENGTH } from "@/lib/auth"; // 로그인 비밀번호 최소 길이 — 시도 제한과 짝 (25.849)
import { MIN_SECRET_LENGTH } from "@/lib/intraday"; // 서명·크론 비밀 최소 길이 — 장중 경로의 `secret_weak` 와 같은 값 (25.655·25.727)

/**
 * 운영 환경변수 점검 (docs/infra.md 25.842, 2026-10-01 사용자 요청). Vercel 화면은 값을 가려 **무엇을 넣었는지 확인할 수 없다.**
 * 그래서 서버가 값을 보지 않고 **들어 있는지·길이가 충분한지·모양이 맞는지**만 참·거짓으로 돌려준다.
 *
 * **값·길이·앞뒤 글자를 돌려주지 않는다.** 응답과 화면에 나오는 것은 이름과 판정뿐이다. 이 경로는 로그인 뒤에만 열린다(`web/proxy.ts`).
 */

type Env = Record<string, string | undefined>;
/** `required` 가 함수면 다른 변수를 보고 정한다 — 쓰는 DB 에 따라 필수가 바뀐다 (25.849) */
type Check = { name: string; purpose: string; required: boolean | ((env: Env) => boolean); rule?: (v: string) => string | null };

/** 지금 쓰는 DB — `lib/db.ts` `dbBackend()` 와 같은 규칙(비면 turso, 모르는 값도 turso) */
const 쓰는DB = (env: Env) => {
  const v = (env.DB_BACKEND ?? "turso").trim().toLowerCase();
  return v === "d1" ? "d1" : v === "auto" ? "auto" : "turso";
};
const Turso가_필요 = (env: Env) => 쓰는DB(env) !== "d1";
const D1이_필요 = (env: Env) => 쓰는DB(env) !== "turso";


const 길이 = (n: number) => (v: string) => (v.length >= n ? null : `${n}자 미만입니다 — 새 값으로 바꾸세요`);

export const ENV_CHECKS: Check[] = [
  { name: "APP_PASSWORD", purpose: "로그인 비밀번호", required: true, rule: (v) => (v.length >= MIN_PASSWORD_LENGTH ? null : `${MIN_PASSWORD_LENGTH}자 미만입니다 — 로그인이 거절됩니다`) },
  { name: "AUTH_SECRET", purpose: "로그인 쿠키 서명", required: true, rule: 길이(MIN_SECRET_LENGTH) },
  { name: "CRON_SECRET", purpose: "cron-job.org 호출 확인", required: true, rule: 길이(MIN_SECRET_LENGTH) },
  { name: "DART_API_KEY", purpose: "보유 종목 공시 알림(장중)", required: true, rule: (v) => (/^[0-9a-f]{40}$/i.test(v) ? null : "OpenDART 인증키 모양(40자리 16진수 — 0~9, a~f)이 아닙니다") },
  { name: "TELEGRAM_BOT_TOKEN", purpose: "텔레그램 알림", required: true, rule: (v) => (/^\d+:[A-Za-z0-9_-]{30,}$/.test(v) ? null : "봇 토큰 모양(숫자:문자열)이 아닙니다") },
  { name: "TELEGRAM_CHAT_ID", purpose: "텔레그램 받는 대화방", required: true, rule: (v) => (/^-?\d+$/.test(v) ? null : "대화방 번호(숫자) 모양이 아닙니다") },
  { name: "DB_BACKEND", purpose: "DB 고르기(auto·turso·d1)", required: false, rule: (v) => (["auto", "turso", "d1"].includes(v.toLowerCase()) ? null : "auto·turso·d1 가운데 하나가 아닙니다 — 모르는 값이면 turso 로 읽습니다") },
  { name: "TURSO_DATABASE_URL", purpose: "Turso 주소", required: Turso가_필요, rule: (v) => (/^(libsql|https):\/\//.test(v) ? null : "libsql:// 또는 https:// 로 시작하지 않습니다") },
  { name: "TURSO_AUTH_TOKEN", purpose: "Turso 토큰", required: Turso가_필요 },
  { name: "D1_ACCOUNT_ID", purpose: "D1(임시 운영 DB) 계정", required: D1이_필요 },
  { name: "D1_DATABASE_ID", purpose: "D1 데이터베이스", required: D1이_필요 },
  { name: "D1_API_TOKEN", purpose: "D1 토큰", required: D1이_필요 },
  { name: "GH_DISPATCH_TOKEN", purpose: "화면에서 Actions 깨우기·Turso 자동 복귀", required: false },
  { name: "GH_REPO", purpose: "깨울 저장소(owner/repo)", required: false, rule: (v) => (/^[\w.-]+\/[\w.-]+$/.test(v) ? null : "owner/repo 모양이 아닙니다") },
];

export interface EnvVerdict {
  name: string;
  purpose: string;
  present: boolean;
  required: boolean;
  /** 모양·길이 문제. 없으면 null — **값은 담지 않는다** */
  problem: string | null;
}

export function envReport(env: Env = process.env): EnvVerdict[] {
  return ENV_CHECKS.map((c) => {
    const required = typeof c.required === "function" ? c.required(env) : c.required;
    const v = (env[c.name] ?? "").trim();
    const present = v.length > 0;
    // 앞뒤 공백이 섞였으면 그것도 문제다 — 붙여 넣다 들어간 줄바꿈이 비교를 깨뜨린다
    const 공백 = present && v !== (env[c.name] ?? "") ? "앞뒤에 공백·줄바꿈이 섞여 있습니다" : null;
    return {
      name: c.name, purpose: c.purpose, present, required,
      problem: !present ? (required ? "비어 있습니다" : null) : (공백 ?? c.rule?.(v) ?? null),
    };
  });
}
