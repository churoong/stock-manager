import { describe, expect, it } from "vitest";
import { heartbeatOutcome } from "@/lib/intraday";
import { intradayVerdict } from "@/lib/health";

describe("발송 실패는 호출 기록에 실패로 남는다 (docs/infra.md 25.338)", () => {
  it("보낸 수가 숫자면 원래 결과", () => {
    expect(heartbeatOutcome("checked", 3, 0)).toBe("checked");
    expect(heartbeatOutcome("skipped:장 밖", undefined)).toBe("skipped:장 밖");
  });

  it("어느 한쪽이라도 발송 실패면 error", () => {
    expect(heartbeatOutcome("checked", 0, "발송 실패: chat not found")).toBe(
      "error",
    );
    expect(heartbeatOutcome("skipped:장 밖", "발송 실패: 403")).toBe("error");
  });

  it("장중 판정은 제때 불렸어도 마지막 호출이 실패면 빨갛다", () => {
    const now = new Date("2026-09-28T02:00:00Z");
    const beat = { called_at: "2026-09-28T01:58:00Z", outcome: "error" };
    expect(intradayVerdict("KR", beat, true, now).tone).toBe("bad");
    expect(
      intradayVerdict("KR", { ...beat, outcome: "checked" }, true, now).tone,
    ).toBe("ok");
  });
});

describe("배선: 장중 경로의 호출 기록은 모두 발송 결과를 거친다", () => {
  it("recordHeartbeat 의 결과 자리에 heartbeatOutcome 을 쓴다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    const calls = src.match(/recordHeartbeat\("intraday",[^\n]*/g) ?? [];
    expect(calls.length).toBeGreaterThanOrEqual(2);
    // 25.725 — 본 판정은 `intradayOutcome`(안에서 heartbeatOutcome 을 부른다)을 거친 `결과` 를 쓴다
    for (const c of calls.filter((c) => !c.includes('"error"')))
      expect(c.includes("heartbeatOutcome(") || c.includes("now, 결과,")).toBe(true);
    expect(src).toContain("const 결과 = intradayOutcome(\n      [result.flushed");
    // 25.731 — 받은 시세 수가 아니라 판정한 수를 넘긴다
    expect(src).toContain("byStock.size, 판정수, errors");
  });
});
