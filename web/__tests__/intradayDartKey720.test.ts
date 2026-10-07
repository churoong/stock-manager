import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** DART 키가 없으면 트리거 e 가 꺼졌는데 기록은 초록이었다 (docs/infra.md 25.720) */
describe("DART 키 없음", () => {
  it("조용히 넘어가지 않고 오류로 남긴다", () => {
    const 원본 = readFileSync(join(__dirname, "..", "app", "api", "cron", "intraday", "route.ts"), "utf8");
    // 25.731 — 앞머리는 `DART_KEY_MISSING`("DART_API_KEY 없음") 상수로 묶였다
    expect(원본).toContain("`${DART_KEY_MISSING} — 보유 종목 공시 알림(트리거 e)이 꺼져 있습니다");
    expect(원본).not.toContain("if (!key) return { hits: [], errors: [] };");
  });
  it("배포 문서와 .env.example 이 Vercel 에 넣으라고 적는다", () => {
    const 뿌리 = join(__dirname, "..", "..");
    expect(readFileSync(join(뿌리, ".env.example"), "utf8")).toContain("DART 전자공시  [ACTIONS, LOCAL, VERCEL]");
    expect(readFileSync(join(뿌리, "docs", "deploy.md"), "utf8")).toContain("`DART_API_KEY` 도 함께 넣는다");
  });
});
