import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * 주간 운영 요약의 "마지막 발송" 은 부분 성공도 센다 (docs/infra.md 25.1373, 주간 요약 감사) — 배치의 다시 보내기 막기
 * (`weekly_summary.ALREADY_SENT_SQL`)와 같은 기준이어야 한다
 */
describe("주간 운영 요약 신선도", () => {
  it("success 와 partial 을 함께 센다", () => {
    const 글 = readFileSync("lib/health.ts", "utf8");
    expect(글).toMatch(/job_name = 'weekly_summary' AND status IN \('success', 'partial'\)/);
    const 배치 = readFileSync("../batch/jobs/weekly_summary.py", "utf8");
    expect(배치).toMatch(/job_name = 'weekly_summary' AND status IN \('success', 'partial'\)/);
  });
});
