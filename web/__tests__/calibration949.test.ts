/** 점수 보정표 (docs/signals.md 10.1, docs/infra.md 25.949) — 배치가 남긴 것을 읽기만 하고, 글은 배치와 같다. */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { calibrationLines, parseCalibration } from "@/lib/recommend";

const 기록 = JSON.stringify({
  signals: 40,
  calibration: [
    {
      window: 20,
      n: 5,
      rho: null,
      verdict: "아직 셀 수 없다 (표본 5건, 30건부터)",
      buckets: [{ lo: 70, hi: 80, n: 5, avg_ret: 0.016, win_rate: 0.8 }],
    },
    { window: 60, n: 0, rho: null, verdict: "아직 셀 수 없다 (표본 0건, 30건부터)", buckets: [] },
  ],
});

describe("parseCalibration", () => {
  it("실행 기록에서 창별 표를 꺼낸다", () => {
    const c = parseCalibration(기록);
    expect(c?.length).toBe(2);
    expect(c?.[0].buckets[0]).toEqual({ lo: 70, hi: 80, n: 5, avg_ret: 0.016, win_rate: 0.8 });
  });

  it("적어 두기 전 실행·깨진 기록은 null — 지어내지 않는다", () => {
    expect(parseCalibration(JSON.stringify({ signals: 3 }))).toBeNull();
    expect(parseCalibration("{broken")).toBeNull();
    expect(parseCalibration(null)).toBeNull();
    expect(parseCalibration(JSON.stringify({ calibration: [{ window: "20" }] }))).toBeNull();
  });
});

describe("calibrationLines", () => {
  it("배치 출력과 같은 줄", () => {
    const [c] = parseCalibration(기록)!;
    expect(calibrationLines(c)).toEqual(["20일 뒤: 아직 셀 수 없다 (표본 5건, 30건부터)", "70~80점: 평균 +1.6% · 이긴 80% (n=5)"]);
    expect(calibrationLines({ window: 5, n: 1, rho: null, verdict: "x", buckets: [{ lo: 0, hi: 10, n: 1, avg_ret: null, win_rate: null }] })[1]).toBe(
      "0~10점: 표본 1건 — 평균 안 냄",
    );
  });

  it("추천 화면이 성적표 아래에 그린다", () => {
    const src = readFileSync(join(process.cwd(), "components", "RecommendList.tsx"), "utf-8");
    expect(src).toContain("calibrationLines(");
    expect(src).toContain("점수 보정표");
  });
});
