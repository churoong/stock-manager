/** 환경변수 점검 — 값은 절대 내보내지 않는다 (docs/infra.md 25.842) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MIN_PASSWORD_LENGTH } from "@/lib/auth";
import { envReport } from "@/lib/envCheck";

const 좋은 = {
  APP_PASSWORD: "correct-horse-battery", AUTH_SECRET: "a".repeat(64), CRON_SECRET: "b".repeat(40),
  DART_API_KEY: "0123456789abcdef0123456789abcdef01234567", TELEGRAM_BOT_TOKEN: "123456:ABCdefGhIJKlmNoPQRsTUVwxyZ0123456789",
  TELEGRAM_CHAT_ID: "-100123456", TURSO_DATABASE_URL: "libsql://x.turso.io", TURSO_AUTH_TOKEN: "t", DB_BACKEND: "auto", GH_REPO: "churoong/stock-manager",
  D1_ACCOUNT_ID: "acct-0001", D1_DATABASE_ID: "dbid-0001", D1_API_TOKEN: "d1-token-0001",
};

describe("환경변수 점검", () => {
  it("모두 맞으면 문제가 없다", () => {
    expect(envReport(좋은).filter((r) => r.problem)).toEqual([]);
  });

  it("짧은 비밀·빈 필수·모양 틀림·공백을 가린다", () => {
    const r = Object.fromEntries(envReport({ ...좋은, AUTH_SECRET: "short", DART_API_KEY: "", TELEGRAM_CHAT_ID: "@me", CRON_SECRET: `${"c".repeat(40)}\n` }).map((x) => [x.name, x.problem]));
    expect(r.AUTH_SECRET).toContain("32자 미만");
    expect(r.DART_API_KEY).toBe("비어 있습니다");
    expect(r.TELEGRAM_CHAT_ID).toContain("숫자");
    expect(r.CRON_SECRET).toContain("공백");
    expect(r.D1_API_TOKEN).toBeNull(); // 선택 항목이 비어도 문제가 아니다
  });

  it("비밀번호 기준은 로그인과 같은 길이다 — 4자는 통과, 3자는 문제 (25.849)", () => {
    const 문제 = (v: string) => envReport({ ...좋은, APP_PASSWORD: v }).find((x) => x.name === "APP_PASSWORD")!.problem;
    expect(문제("1234")).toBeNull();
    expect(문제("123")).toContain(`${MIN_PASSWORD_LENGTH}자 미만`);
  });

  it("쓰는 DB 에 따라 필수가 바뀐다 (25.849)", () => {
    const 필수 = (env: Record<string, string>) =>
      Object.fromEntries(envReport(env).map((x) => [x.name, x.required]));
    const { TURSO_DATABASE_URL: _u, TURSO_AUTH_TOKEN: _t, D1_API_TOKEN: _d, ...튜소없음 } = 좋은;
    // d1 이면 Turso 가 비어도 문제가 아니고 D1 이 비면 문제다
    const d1 = envReport({ ...튜소없음, DB_BACKEND: "d1" });
    expect(d1.find((x) => x.name === "TURSO_AUTH_TOKEN")!.problem).toBeNull();
    expect(d1.find((x) => x.name === "D1_API_TOKEN")!.problem).toBe("비어 있습니다");
    expect(필수({ ...좋은, DB_BACKEND: "auto" })).toMatchObject({ TURSO_AUTH_TOKEN: true, D1_API_TOKEN: true });
    expect(필수({ ...좋은, DB_BACKEND: "turso" })).toMatchObject({ TURSO_AUTH_TOKEN: true, D1_API_TOKEN: false });
    // 비어 있으면 turso 다 (lib/db.ts dbBackend 와 같다)
    const { DB_BACKEND: _b, ...기본 } = 좋은;
    expect(필수(기본)).toMatchObject({ TURSO_AUTH_TOKEN: true, D1_API_TOKEN: false });
  });

  it("모양이 틀린 값을 잡는다 — 정규식이 무엇이든 통과시키지 않는다 (25.849)", () => {
    const 문제 = (k: string, v: string) => envReport({ ...좋은, [k]: v }).find((x) => x.name === k)!.problem;
    expect(문제("DART_API_KEY", "g".repeat(40))).toContain("16진수");
    expect(문제("DART_API_KEY", "a".repeat(39))).toContain("16진수");
    expect(문제("TELEGRAM_BOT_TOKEN", "abc")).not.toBeNull();
    expect(문제("TURSO_DATABASE_URL", "http://x")).not.toBeNull();
    expect(문제("GH_REPO", "stock-manager")).not.toBeNull();
    expect(문제("DB_BACKEND", "sqlite")).not.toBeNull();
    // 대소문자는 lib/db.ts dbBackend 처럼 가리지 않는다 — 앱이 받아들이는 값에 빨간불을 켜지 않는다 (25.853)
    expect(문제("DB_BACKEND", "D1")).toBeNull();
  });

  it("응답 어디에도 값이 없다", () => {
    const 글 = JSON.stringify(envReport(좋은));
    for (const v of Object.values(좋은)) if (v.length > 4) expect(글).not.toContain(v);
  });

  it("상태 화면이 따로 읽고, 경로는 DB 를 읽지 않는다", () => {
    expect(readFileSync("app/status/page.tsx", "utf-8")).toContain("<EnvCheck />");
    const 경로 = readFileSync("app/api/status/env/route.ts", "utf-8");
    expect(경로).toContain("envReport()");
    expect(경로).not.toContain("execute(");
  });
});
