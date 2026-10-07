/** 오늘 신호 계산을 건너뛰었거나 실패했으면 추천 화면이 말한다 (docs/infra.md 25.798, 추천 감사 #4) */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { signalRunNote } from "@/lib/recommend";

const 실행 = (over: Record<string, unknown>) => ({
  status: "skipped", started_at: "2026-09-30T23:40:00Z", trade_date: "2026-09-30", error_text: null,
  step_log: JSON.stringify({ reason: "2026-09-30 종가가 없는 종목이 400/880 입니다(휴장일이거나 시세 수집 실패)" }), ...over,
});

describe("신호 실행 안내", () => {
  it("마지막 성공 뒤에 건너뛴 실행이면 까닭과 함께 말한다", () => {
    const 말 = signalRunNote(실행({}), "2026-09-29T23:45:00Z", "2026-09-29");
    expect(말).toContain("2026-09-30 기준 신호 계산을 건너뛰었습니다");
    expect(말).toContain("종가가 없는 종목이 400/880");
    expect(말).toContain("아래는 2026-09-29 기준 신호입니다");
  });

  it("실패는 error_text 를 옮긴다", () => {
    expect(signalRunNote(실행({ status: "failed", step_log: null, error_text: "유니버스가 비어 있습니다" }), null, null))
      .toContain("실패했습니다 (유니버스가 비어 있습니다)");
  });

  it("성공했거나, 그 뒤에 성공이 있으면 조용하다", () => {
    expect(signalRunNote(실행({ status: "success" }), null, "2026-09-30")).toBeNull();
    expect(signalRunNote(실행({ started_at: "2026-09-29T23:40:00Z" }), "2026-09-29T23:45:00Z", "2026-09-29")).toBeNull();
    expect(signalRunNote(undefined, null, null)).toBeNull();
  });

  it("보이는 기준일보다 옛 날짜를 돌리다 멈춘 것은 말하지 않는다 (25.801)", () => {
    expect(signalRunNote(실행({ trade_date: "2026-09-01" }), "2026-09-29T23:45:00Z", "2026-09-29")).toBeNull();
    expect(signalRunNote(실행({}), null, null)).toContain("앞서 계산된 신호도 없습니다");
  });

  it("추천 경로가 안내 맨 앞에 싣는다", () => {
    const 글 = readFileSync("app/api/recommend/route.ts", "utf-8");
    expect(글).toContain("LATEST_SIGNAL_RUN, [parsed.data.country]");
    expect(글).toContain("notes.unshift(건너뜀);");
    expect(글).toContain("기준으로 조건을 만족한 종목이 없었습니다");
  });
});

describe("1부 과거 성과 요약 (25.803)", () => {
  it("배치가 실은 행을 텔레그램과 같은 글로", async () => {
    const { performanceLine } = await import("@/lib/recommend");
    const raw = JSON.stringify({ performance: { window: "3Y", as_of: "2026-09-29", cagr: 0.123, mdd: -0.31, sharpe: 0.84 } });
    expect(performanceLine(raw)).toBe("과거(3Y, 기준 2026-09-29) CAGR 12.3% · MDD -31.0% · 샤프 0.84");
    // 텔레그램과 같은 모양 — "-0.0%" 가 나오지 않는다 (25.805)
    expect(performanceLine(JSON.stringify({ performance: { cagr: -0.00003 } }))).toBe("과거 CAGR 0.0%");
    expect(performanceLine(JSON.stringify({ criteria: [] }))).toBeNull();
    expect(performanceLine("{망가짐")).toBeNull();
    expect(readFileSync("components/RecommendList.tsx", "utf-8")).toContain("performanceLine(row.rationale_data)");
  });
});
