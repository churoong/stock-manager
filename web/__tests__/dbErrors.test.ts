/**
 * DB 오류 문장 테스트 (docs/infra.md 23절).
 *
 * 2026-09-17 에 Turso 월 한도를 넘겨 읽기·쓰기가 막혔다. 화면에는 영어 원문만 떴다.
 * 원인을 모르면 사용자는 앱이 고장 난 줄 안다. 무엇을 해야 하는지까지 적는다.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { beforeAll, describe, expect, it } from "vitest";
import { explain } from "@/lib/db";
import { API_USAGE, monthWindow } from "@/lib/health";

describe("한도에 걸렸을 때", () => {
  it("읽기가 막히면 언제 풀리는지까지 말한다", () => {
    const raw = "Operation was blocked: SQL read operations are forbidden (reads are blocked, do you need to upgrade your plan?)";
    const out = explain(raw);
    expect(out).toContain("월 읽기 한도");
    expect(out).toContain("다음 결제 주기");
    expect(out).toContain(raw); // 원문도 남긴다. 다른 이유로 막혔을 때 구별해야 한다
  });

  it("쓰기가 막히면 조회는 된다고 알린다", () => {
    const out = explain("Operation was blocked: SQL write operations are forbidden (writes are blocked)");
    expect(out).toContain("월 쓰기 한도");
    expect(out).toContain("조회만");
  });

  it("인증 실패는 토큰이 잘렸는지 보라고 한다", () => {
    // 실제로 겪은 일이다 (docs/infra.md 4절): 348자 토큰이 194자만 등록돼 있었다
    expect(explain("401 unauthorized")).toContain("TURSO_AUTH_TOKEN");
  });

  it("모르는 오류는 원문 그대로", () => {
    expect(explain("no such table: prices")).toBe("SQL 실패: no such table: prices");
  });
});

describe("한도 게이지 질의", () => {
  let db: DatabaseSync;

  beforeAll(() => {
    db = new DatabaseSync(":memory:");
    const dir = join(process.cwd(), "..", "migrations");
    for (const file of readdirSync(dir).filter((f) => f.endsWith(".sql")).sort()) {
      db.exec(readFileSync(join(dir, file), "utf-8"));
    }
    db.exec(`
      INSERT INTO api_usage (api_name, window_type, window_start, call_count, limit_value, warn_at_pct, state, updated_at)
      VALUES ('krx_openapi', 'day', '2026-09-17', 1200, 10000, 80, 'ok', 't'),
             ('turso_writes', 'month', '2026-09', 9800000, 10000000, 80, 'warn', 't')
    `);
  });

  it("월 단위 행이 날짜 조건에 걸려 빠지지 않는다", () => {
    // '2026-09' 는 '2026-09-16' 보다 작다(짧은 쪽이 앞선다). 날짜 하나로 자르면
    // 정작 봐야 할 쓰기 예산 게이지가 화면에서 사라진다
    const rows = db.prepare(API_USAGE).all("2026-09-16", "2026-08") as Array<{ api_name: string }>;
    expect(rows.map((r) => r.api_name)).toContain("turso_writes");
    expect(rows.map((r) => r.api_name)).toContain("krx_openapi");
    // 월 단위가 먼저 보인다. 앱을 멈추는 쪽이 그것이다
    expect(rows[0].api_name).toBe("turso_writes");
  });

  it("월 창 기준은 지난달까지 본다", () => {
    expect(monthWindow(new Date("2026-09-17T00:00:00Z"))).toBe("2026-08");
    expect(monthWindow(new Date("2026-01-05T00:00:00Z"))).toBe("2025-12");
  });
});

describe("한도는 고장이 아니다 (docs/infra.md 25.6)", () => {
  it("D1 하루 쓰기 한도를 알아본다", async () => {
    const { quotaReason } = await import("@/lib/db");
    const raw =
      "D1 실패(HTTP 400): 7500 Your account has exceeded D1's free tier daily row write limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC) to continue.";
    expect(quotaReason(raw)).toContain("09:00 KST");
  });

  it("Turso 월 한도도 알아본다", async () => {
    const { quotaReason } = await import("@/lib/db");
    expect(quotaReason("Operation was blocked: SQL read operations are forbidden (reads are blocked)")).toContain("Turso");
  });

  it("다른 오류는 한도로 보지 않는다", async () => {
    const { quotaReason } = await import("@/lib/db");
    expect(quotaReason("no such table: prices")).toBeNull();
    expect(quotaReason("D1 인증 실패")).toBeNull();
  });
});
