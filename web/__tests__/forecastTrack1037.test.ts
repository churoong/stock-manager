/** 예측 성적표 (docs/analysis.md 11장, docs/infra.md 25.1037) — 배치가 센 누계를 표 줄로 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { MODEL_LABEL, TRACK_FIELDS, TRACK_MIN_SAMPLE, trackRows } from "@/lib/analysis";

const PY = readFileSync(join(process.cwd(), "..", "batch", "services", "forecast_track.py"), "utf-8");

describe("예측 성적표", () => {
  it("누계 자리·모델 이름·최소 표본이 배치와 같다", () => {
    const fields = PY.match(/^FIELDS = \(([^)]*)\)/m)![1].match(/"(\w+)"/g)!.map((s) => s.replace(/"/g, ""));
    expect(fields).toEqual([...TRACK_FIELDS]);
    for (const [k, v] of Object.entries(MODEL_LABEL)) expect(PY).toContain(`"${k}": "${v}"`);
    expect(PY).toMatch(new RegExp(`^MIN_SAMPLE = ${TRACK_MIN_SAMPLE}$`, "m"));
  });

  it("표본이 모자라면 비율을 믿지 않는다고 표시한다", () => {
    const rows = trackRows({ capm: { "3": [5, 5, 4, 5, 5, 3, 0.2], "1": [25, 25, 17, 23, 24, 15, 1.0] }, consensus: { "12": [3, 0, 0, 0, 3, 2, 0.6] } });
    expect(rows.map((r) => [r.model, r.months])).toEqual([["capm", 1], ["capm", 3], ["consensus", 12]]);
    expect(rows[0]).toMatchObject({ n: 25, in68: 17 / 25, dir: 15 / 24, enough: true });
    expect(rows[1].enough).toBe(false);
    expect(rows[2].in68).toBeNull(); // 증권사 목표가는 범위가 없다
  });
});
