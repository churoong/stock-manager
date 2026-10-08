/** 종목 분석 탭 레이더 (docs/analysis.md 20장, docs/infra.md 25.1043) */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FACTOR_NAME, RADAR_KEYS, RADAR_SQL, parseRadars } from "@/lib/analysis";

const PY = readFileSync(join(process.cwd(), "..", "batch", "jobs", "verdicts.py"), "utf-8");
const VD = readFileSync(join(process.cwd(), "..", "batch", "services", "verdict.py"), "utf-8");

describe("레이더", () => {
  it("배치가 쓰는 설정 열쇠·팩터 이름과 같다", () => {
    expect(PY).toContain('return f"analysis_radar_{country}"');
    for (const k of RADAR_KEYS) expect(RADAR_SQL).toContain(`'${k}'`);
    for (const [k, v] of Object.entries(FACTOR_NAME)) expect(VD).toContain(`"${k}": "${v}"`);
  });

  it("깨진 값은 빼고 시장별로 나눈다", () => {
    const ok = JSON.stringify({ as_of: "d", computed_at: "t", near: [], rising: [], eyes: [] });
    const r = parseRadars([{ key: "analysis_radar_KR", value: ok }, { key: "analysis_radar_US", value: "{깨짐" }]);
    expect(Object.keys(r)).toEqual(["KR"]);
  });
});
