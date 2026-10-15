import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// 크론 응답 본문에 종목을 싣지 않는다 (docs/infra.md 25.1422, 보안 감사 #3). 응답은 외부 크론 서비스의 실행 기록에 남을 수
// 있다 — 미국 뉴스 후보는 보유 → 관심 순이라 호출이 쌓이면 보유 목록이 드러났다. 종목은 호출 기록(cron_heartbeats)에만
describe("크론 응답", () => {
  it("미국 뉴스는 종목 없이 결과만", () => {
    const src = readFileSync("app/api/cron/news/route.ts", "utf8");
    expect(src).toContain(
      "return NextResponse.json({ status, items: items.length, added });",
    );
    expect(src).not.toMatch(/NextResponse\.json\(\{\s*symbol/);
  });
});
