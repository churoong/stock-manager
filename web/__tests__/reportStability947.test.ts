/** 1부 추천의 가중치 흔들기 표시 (docs/reports.md 3.2, docs/infra.md 25.947). */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { stabilityLabel } from "@/lib/reports";

describe("stabilityLabel", () => {
  it("남은 수와 빠지는 세계를 글과 같은 말로", () => {
    const r = stabilityLabel({ stability: { kept: 13, total: 16, dropped_by: ["밸류 −10%p", "센티먼트 끔"], base_matches: true } });
    expect(r).toEqual({ text: "흔들기 13/16", title: "밸류 −10%p · 센티먼트 끔이면 상위에서 빠진다" });
    expect(stabilityLabel({ stability: { kept: 16, total: 16, dropped_by: [] } })?.title).toContain("어떻게 흔들어도");
  });

  it("가중치가 점수 뒤 바뀐 경우를 표시한다", () => {
    const r = stabilityLabel({ stability: { kept: 9, total: 16, dropped_by: ["성장 빼기"], base_matches: false } });
    expect(r?.text).toBe("흔들기 9/16 (가중치 바뀜)");
    expect(r?.title).toContain("점수 계산 뒤 바뀌어");
  });

  it("옛 리포트와 깨진 값은 null — 지어내지 않는다", () => {
    expect(stabilityLabel({})).toBeNull();
    expect(stabilityLabel({ stability: "x" })).toBeNull();
    expect(stabilityLabel({ stability: { kept: "13", total: 16 } })).toBeNull();
    expect(stabilityLabel({ stability: { kept: 1, total: 0 } })).toBeNull();
  });

  it("리포트 화면이 추천 줄에 그린다", () => {
    const src = readFileSync(join(process.cwd(), "components", "ReportView.tsx"), "utf-8");
    expect(src).toContain("stabilityLabel(item.payload)");
  });
});
