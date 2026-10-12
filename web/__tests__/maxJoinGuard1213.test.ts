/**
 * **`MAX(날짜) … JOIN stocks|etfs` 를 새로 쓰지 않는다** (docs/infra.md 25.1204·25.1212·25.1213).
 * 통계(ANALYZE)가 있으면 SQLite 는 종목 쪽부터 돌아 그 나라 행을 **전부** 읽는다. 날짜 색인을 내림차순으로 따라가
 * `CROSS JOIN … ORDER BY 날 DESC LIMIT 1` 로 쓴다. 날짜 색인이 없는 표·옛 DB 되돌이(FALLBACK)는 사유와 함께 뺀다
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const 면제: Record<string, string> = {
  etf_profiles: "날짜로 시작하는 색인이 없다(UNIQUE 가 etf_id 먼저) — 바꿔도 이득이 없고 표가 작다(ETF 수 × 달)",
  "LAST_CALC_DATE_FALLBACK|PREVIOUS_AS_OF_FALLBACK": "판정표가 없는 옛 DB(0034 전)에서만 부른다",
  "COUNT(*) 옆 MAX": "집계 질의의 기준일 열 — 최신 판정으로 이미 거른 행을 센다(적립 퍼널)",
};

function 파일들(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? 파일들(p) : p.endsWith(".ts") ? [p] : [];
  });
}

describe("MAX + JOIN 그물 (25.1213)", () => {
  it("lib·app 에 새로 생기지 않는다", () => {
    const 걸림: string[] = [];
    for (const f of [...파일들(join(process.cwd(), "lib")), ...파일들(join(process.cwd(), "app"))]) {
      const 글 = readFileSync(f, "utf8");
      const re = /MAX\(\w+\.(?:as_of_date|date|snapshot_date)\)(?:\s+AS\s+\w+)?\s+FROM\s+(\w+)\s+\w+\s+JOIN\s+(?:stocks|etfs)\b/g;
      for (const m of 글.matchAll(re)) {
        const 앞 = 글.slice(Math.max(0, (m.index ?? 0) - 200), m.index);
        if (m[1] === "etf_profiles") continue;
        // 집계 질의의 한 열(기준일 표시) — 최신 날 찾기가 아니라 이미 최신 판정으로 거른 행을 센다
        if (/COUNT\(\*\)\s+AS\s+\w+,\s*$/.test(앞)) continue;
        if (/(LAST_CALC_DATE_FALLBACK|PREVIOUS_AS_OF_FALLBACK)\s*=\s*`[^`]*$/.test(앞)) continue;
        걸림.push(`${f.replace(process.cwd(), "web")}: ${m[0]}`);
      }
    }
    expect(걸림, `날짜 색인 내림차순(CROSS JOIN … ORDER BY … DESC LIMIT 1)으로 쓰거나 면제에 사유를 적어라:\n${걸림.join("\n")}`).toEqual([]);
    expect(Object.keys(면제).length).toBe(3);
  });
});
