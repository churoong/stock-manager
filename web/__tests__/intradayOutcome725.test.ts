import { describe, expect, it } from "vitest";
import { intradayOutcome, withinOpenGrace } from "@/lib/intraday";

/** 시세 0건·DART 키 없음도 초록이던 것 (docs/infra.md 25.725, 감사) */
describe("장중 호출 결과", () => {
  it("대상이 있는데 시세를 한 건도 못 받으면 error", () => {
    expect(intradayOutcome([0, 0], 5, 0, ["야후 HTTP 429"])).toBe("error");
    expect(intradayOutcome([0, 0], 0, 0, [])).toBe("checked"); // 대상이 없으면 시세도 없다
  });
  it("DART 키가 없으면 error", () => {
    expect(intradayOutcome([0, 0], 5, 5, ["DART_API_KEY 없음 — 보유 종목 공시 알림(트리거 e)이 꺼져 있습니다"])).toBe("error");
  });
  it("발송 실패는 예전처럼 error, 나머지는 checked", () => {
    expect(intradayOutcome([0, "발송 실패: 401"], 5, 5, [])).toBe("error");
    expect(intradayOutcome([0, 2], 5, 5, ["DART status 013"])).toBe("checked");
  });
});

/** 받은 시세 수로 판정해 지난 장 시세만·일부만 받은 날도 초록이던 것 (docs/infra.md 25.731, 교차검증) */
describe("판정한 수로 본다", () => {
  it("시세를 받았어도 판정한 종목이 0이면 error", () => {
    expect(intradayOutcome([0, 0], 3, 0, [])).toBe("error");
  });
  it("야후 호출 실패로 빠진 대상이 있으면 error", () => {
    expect(intradayOutcome([0, 0], 40, 20, ["야후 HTTP 429"], 20)).toBe("error");
  });
  it("야후 실패 없이 빠진 종목(이전상장 등)만 있으면 checked", () => {
    expect(intradayOutcome([0, 0], 40, 39, [], 0)).toBe("checked");
  });
  it("라우트의 DART 키 오류 문구가 판정 앞머리와 같다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(src).toContain("errors: [`${DART_KEY_MISSING} — 보유");
  });
});

/** 개장 직후 지연 시세로 매일 빨개지던 것 (docs/infra.md 25.735, 25.730~733 교차검증) */
describe("개장 직후 유예", () => {
  it("받은 시세가 모두 지난 장 것이어도 개장 유예 안이면 checked", () => {
    expect(intradayOutcome([0, 0], 10, 0, [], 0, true)).toBe("checked");
    expect(intradayOutcome([0, 0], 10, 0, [], 0, false)).toBe("error");
  });
  it("유예 창은 개장부터 30분", () => {
    const open = "2026-09-30T00:00:00Z";
    expect(withinOpenGrace(open, new Date("2026-09-30T00:05:00Z"))).toBe(true);
    expect(withinOpenGrace(open, new Date("2026-09-30T00:30:00Z"))).toBe(false);
    expect(withinOpenGrace(open, new Date("2026-09-29T23:59:00Z"))).toBe(false);
  });
  it("라우트는 야후 실패가 없고 지난 장 시세가 있을 때만 유예를 준다", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync("app/api/cron/intraday/route.ts", "utf8");
    expect(src).toContain("withinOpenGrace(session.open_utc, now) && !quoteCallFailed && staleQuotes.length > 0");
  });
});
