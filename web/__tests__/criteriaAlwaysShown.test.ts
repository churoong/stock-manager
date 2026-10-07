import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * **마지막 줄을 아무도 부르지 않았다** (docs/infra.md 25.174).
 *
 * `CriteriaTable` 은 근거가 0줄일 때 빨간 경고를 그린다 — "근거를 만들 수 없습니다,
 * 이 추천을 믿지 마세요"(25.71). 그 자리에 이렇게 적혀 있다.
 *
 * > 그때 조용히 숨기지 않는다. 숨기면 "추천이 왜 없지" 로만 보이고 고장은 그대로 남는다
 *
 * 그런데 **부르는 쪽 넷이 모두** `criteria.length > 0 ? <CriteriaTable/> : null` 로
 * 감싸고 있었다. 0줄이면 표를 안 그리는 것이 아니라 **경고까지 안 그린다.** 남는 것은
 * 근거 없는 추천 카드 하나다 — 확인할 수 있는 추천과 똑같은 모습으로.
 *
 * CLAUDE.md 절대 규칙: "근거표를 만들 수 없는 추천은 표시하지 않는다".
 *
 * 그물을 **글자로** 친다. 조건을 다시 씌우면 여기서 걸린다.
 */

const 컴포넌트 = join(process.cwd(), "components");

function 부르는_파일들(): string[] {
  return readdirSync(컴포넌트)
    .filter((name) => name.endsWith(".tsx") && name !== "CriteriaTable.tsx")
    .filter((name) => readFileSync(join(컴포넌트, name), "utf-8").includes("<CriteriaTable"));
}

describe("근거표는 0줄이어도 그린다", () => {
  it("부르는 곳을 실제로 찾아 냈다", () => {
    // 훑기가 조용히 비면 아래가 공짜로 통과한다
    expect(부르는_파일들().length).toBeGreaterThanOrEqual(5);
  });

  it.each(부르는_파일들())("%s 가 길이로 감추지 않는다", (이름) => {
    const 글 = readFileSync(join(컴포넌트, 이름), "utf-8");
    // **주석을 먼저 걷어낸다.** 안 걷으면 "예전에는 이렇게 감쌌다" 고 적은 설명이 걸린다 —
    // 그물이 제 설명을 물어 고칠 수가 없게 된다. 블록 주석은 여러 줄이라 줄 단위로는 못 지운다
    const 코드 = 글
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");

    expect(코드, "근거가 0줄일 때 경고까지 사라진다").not.toMatch(
      /criteria\.length\s*(>|===|!==|<)/,
    );
    expect(코드, "0줄이면 CriteriaTable 이 경고를 그린다. 먼저 돌려보내면 안 된다").not.toMatch(
      /rows\.length === 0\)\s*return null/,
    );
  });

  it("경고 자리가 아직 있다", () => {
    // 위 그물은 "감추지 않는다" 만 본다. 경고 자체가 사라지면 그물이 지킬 것이 없어진다
    const 글 = readFileSync(join(컴포넌트, "CriteriaTable.tsx"), "utf-8");

    expect(글).toContain("rows.length === 0");
    expect(글).toContain("이 추천을 믿지 마세요");
  });

  it("배치도 같은 규칙을 지킨다", () => {
    // 화면은 마지막 줄이다. 먼저 막는 곳은 배치다 (25.174)
    const 글 = readFileSync(join(process.cwd(), "..", "batch", "services", "criteria.py"), "utf-8");

    expect(글).toContain("drop_unverifiable");
    expect(글).toContain("NO_CRITERIA_REASON");
  });
});
