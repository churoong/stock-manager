/** 웹 /kis·운용 숫자 — 반올림이 문턱과 같아 보이지 않게, 못 읽은 것을 없는 것으로 말하지 않게 (docs/infra.md 25.1637) */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apartDigits } from "../lib/apart";
import { gateNowText } from "../lib/desk";
import { qualityLine } from "../lib/kisQuality";
import { brokerLines } from "../lib/paper";

const execute = vi.fn();
vi.mock("@/lib/db", async (orig) => ({
  ...(await orig<typeof import("@/lib/db")>()),
  execute,
}));

describe("문턱과 구별되는 자릿수", () => {
  it("문턱과 같아 보이면 자리를 늘리고, 멀면 그대로", () => {
    expect(apartDigits(49.96, 50, 1)).toBe(2);
    expect(apartDigits(48.3, 50, 1)).toBe(1);
    expect(apartDigits(50, 50, 1)).toBe(1); // 정확히 같으면 그대로
    expect(apartDigits(0.004, 0, 2)).toBe(3);
  });

  it("관문 칸", () => {
    const 칸 = { label: "", ok: false };
    expect(
      gateNowText({
        ...칸,
        key: "selection",
        now: 49.96,
        target: 50,
        mean: 55,
        n: 12,
      }),
    ).toBe("하한 49.96분위(평균 55.0, 28일 묶음 12) / 50 초과");
    expect(
      gateNowText({ ...칸, key: "kis", now: 89.95, target: 90, n: 209 }),
    ).toBe("89.95% (표본 209) / 90% 이상");
    expect(gateNowText({ ...칸, key: "vs_r", now: -0.004, target: 0 })).toBe(
      "-0.004%p / 0 초과",
    );
    expect(
      gateNowText({ ...칸, key: "ratio", now: 0.1234, target: 0.1231 }),
    ).toBe("0.1234 / R 평균 0.1231");
    expect(
      gateNowText({ ...칸, key: "kis", now: 92.3, target: 90, n: 50 }),
    ).toBe("92.3% (표본 50) / 90% 이상");
  });

  it("실행 품질·비교 줄", () => {
    const r = {
      as_of: "2026-10-21",
      n: 30,
      match_pct: 79.6,
      kept_pct: 84.6,
      unknown: 0,
      rejected: 0,
    };
    expect(qualityLine(r as never)).toContain(
      "체결 일치 79.6% · 실현율 84.6%(",
    );
    const 줄 = brokerLines({
      as_of: "2026-10-21",
      diff: {},
      summary: {
        days: 28,
        n: 30,
        match_pct: 79.6,
        both: 1,
        internal_only: 1,
        kis_only: 1,
        neither: 1,
        partial: 0,
        slip_buy: { n: 12, avg: 0.503 },
        slip_sell: { n: 0, avg: null },
      } as never,
    });
    expect(줄[0]).toContain("체결 일치 79.6%");
    expect(줄[1]).toContain("매수 +0.503% (n=12)");
  });
});

describe("/api/paper/kis — 한도·인증 실패를 기록 없음으로 말하지 않는다", () => {
  beforeEach(() => {
    execute.mockReset();
  });

  it("표가 없으면 빈 보기, 다른 오류는 까닭과 500", async () => {
    const { GET } = await import("@/app/api/paper/kis/route");
    execute.mockImplementation(async () => {
      throw new Error("no such table: paper_broker_daily");
    });
    const 빈 = await GET();
    expect(빈.status).toBe(200);
    expect((await 빈.json()).markets.KR.orders).toEqual([]);
    execute.mockImplementation(async () => {
      throw new Error("SQLITE_BLOCKED: reads are blocked");
    });
    const 막힘 = await GET();
    expect(막힘.status).toBe(500);
    expect((await 막힘.json()).errors?.length).toBe(1);
  });
});
