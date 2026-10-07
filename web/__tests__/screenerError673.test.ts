/** 스크리너 조회 실패를 "조건에 맞는 종목이 없습니다" 로 보이지 않는다 (docs/infra.md 25.673, 감사). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("스크리너 실패 표시", () => {
  const 글 = readFileSync("components/ScreenerForm.tsx", "utf8");

  it("실패 두 갈래 모두 앞 조회의 결과·까닭·기준을 지운다", () => {
    expect(글.match(/비우기\(\);/g)?.length).toBe(2);
    for (const 지울것 of ["setRows([])", "setConditions([])", "setBasis(null)", "setNotes([])"]) {
      expect(글.slice(글.indexOf("const 비우기"), 글.indexOf("const search"))).toContain(지울것);
    }
  });

  it("오류가 있으면 빈 결과 문구 대신 조회 실패를 말한다", () => {
    expect(글).toContain("조회하지 못했습니다 — 위 오류를 보세요");
  });

  it("실패면 머리에 0종목이 아니라 조회 실패, 나라를 바꾸면 업종 목록을 지운다 (25.675)", () => {
    expect(글).toContain('errors.length > 0 ? "조회 실패"');
    const 바꾸기 = 글.slice(글.indexOf("function switchCountry"), 글.indexOf("function patch"));
    expect(바꾸기).toContain("setSectors([])");
  });
});
