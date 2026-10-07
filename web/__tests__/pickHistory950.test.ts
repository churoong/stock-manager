/** 1부 추천의 추천 이력 표시 (docs/reports.md 3.3, docs/infra.md 25.950). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { HISTORY_RATIO_BAND, historyLabel } from "@/lib/reports";

describe("historyLabel", () => {
  it("배치 글과 같은 말 — 횟수·연속·첫 추천 종가 대비", () => {
    expect(historyLabel({ history: { times: 3, first_date: "2026-09-29", first_close: 70000, streak: 3 }, close: 72170 })).toBe(
      "4번째 추천 (연속 4일) — 첫 추천 09-29 대비 +3.1%",
    );
    expect(historyLabel({ history: { times: 2, first_date: "2026-09-29", first_close: 70000, streak: 0 } })).toBe(
      "3번째 추천 — 첫 추천 09-29",
    );
    expect(historyLabel({ history: { times: 0, first_date: null, first_close: null, streak: 0 } })).toBe("첫 추천");
  });

  it("분할·병합으로 보이는 비율이면 대비를 적지 않는다 (25.963)", () => {
    const h = { times: 3, first_date: "2026-09-29", first_close: 70000, streak: 3 };
    expect(historyLabel({ history: h, close: 14280 })).toBe(
      "4번째 추천 (연속 4일) — 첫 추천 09-29 (가격 단위가 바뀐 듯해 대비는 적지 않음 — 분할·병합)",
    );
    expect(historyLabel({ history: h, close: 35000 })).toBe("4번째 추천 (연속 4일) — 첫 추천 09-29 대비 -50.0%");
  });

  it("띠는 배치와 같다", () => {
    const src = readFileSync(join(process.cwd(), "..", "batch", "services", "pick_history.py"), "utf-8");
    expect(src).toContain(`RATIO_BAND = (${HISTORY_RATIO_BAND[0].toFixed(1)}, ${HISTORY_RATIO_BAND[1].toFixed(1)})`);
  });

  it("싣기 전 리포트·깨진 값은 null — 지어내지 않는다", () => {
    expect(historyLabel({})).toBeNull();
    expect(historyLabel({ history: "x" })).toBeNull();
    expect(historyLabel({ history: { times: "3" } })).toBeNull();
  });

  it("리포트 화면이 추천 줄에 그린다", () => {
    const src = readFileSync(join(process.cwd(), "components", "ReportView.tsx"), "utf-8");
    expect(src).toContain("historyLabel(item.payload)");
  });
});
