import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, SETTINGS_KEYS, mergeStoredSettings, settingsNotices } from "@/lib/settings";

/**
 * 저장된 설정을 **합치는 규칙** (docs/infra.md 25.73).
 *
 * 2026-09-21 까지 이 합치기가 두 곳에 따로 적혀 있었다 —
 * `app/settings/page.tsx`(화면이 실제로 쓰는 길)와 `app/api/settings/route.ts`(부르는 곳 없음).
 * 글자는 달랐지만 하는 일은 같았고, **돌려주는 것은 이미 갈라져** 있었다.
 *
 * | | 검증에 실패한 설정을 |
 * |---|---|
 * | 화면 | `warnings` 에 담아 **사용자에게 보여 준다** |
 * | 경로 | `invalid` 라는 **아무도 안 읽는 칸**에 넣는다 |
 *
 * 즉 "저장된 설정이 규칙을 어겼다" 는 사실이 **어느 길로 읽었느냐에 따라** 보이기도 하고
 * 안 보이기도 했다. 부르는 곳이 생기는 순간 조용히 삼킬 참이었다.
 */

const 뿌리 = join(process.cwd(), "..");
const 읽기 = (경로: string) => readFileSync(join(뿌리, 경로), "utf-8");

describe("합치기", () => {
  it("저장된 것이 없으면 전부 기본값", () => {
    expect(mergeStoredSettings([])).toEqual(DEFAULT_SETTINGS);
  });

  it("저장된 값이 기본값을 덮는다", () => {
    const 합침 = mergeStoredSettings([{ key: "sentiment_target_top_n", value: "42" }]);

    expect(합침.sentiment_target_top_n).toBe(42);
  });

  it("모르는 키는 버린다", () => {
    // 옛 판에서 남은 키가 설정 객체에 섞여 들어가면 검증이 엉뚱한 말을 한다
    const 합침 = mergeStoredSettings([{ key: "universe_filters", value: '{"x":1}' }]);

    expect(합침).toEqual(DEFAULT_SETTINGS);
    expect(Object.keys(합침)).toEqual(Object.keys(DEFAULT_SETTINGS));
  });

  it("**깨진 JSON 은 기본값으로 둔다** — 화면이 무너지는 것보다 낫다", () => {
    const 합침 = mergeStoredSettings([{ key: "factor_weights", value: "{이건 JSON 이 아니다" }]);

    expect(합침.factor_weights).toEqual(DEFAULT_SETTINGS.factor_weights);
  });

  it("깨진 값 하나가 나머지를 막지 않는다", () => {
    const 합침 = mergeStoredSettings([
      { key: "factor_weights", value: "깨짐" },
      { key: "sentiment_target_top_n", value: "7" },
    ]);

    expect(합침.factor_weights).toEqual(DEFAULT_SETTINGS.factor_weights);
    expect(합침.sentiment_target_top_n).toBe(7);
  });

  it("원본을 건드리지 않는다", () => {
    const 앞 = JSON.stringify(DEFAULT_SETTINGS);
    mergeStoredSettings([{ key: "sentiment_target_top_n", value: "1" }]);

    expect(JSON.stringify(DEFAULT_SETTINGS)).toBe(앞);
  });

  it("키 목록이 기본값에서 나온다", () => {
    expect(SETTINGS_KEYS.sort()).toEqual(Object.keys(DEFAULT_SETTINGS).sort());
  });
});

describe("무엇을 띄우나", () => {
  it("기본값은 조용하거나 '비어 있는 값' 만 알린다", () => {
    const 줄 = settingsNotices(DEFAULT_SETTINGS);

    // 세율·수수료는 [확인필요] 라 기본이 null 이다 (CLAUDE.md 매매 규칙)
    for (const 한줄 of 줄) expect(한줄).toContain("비어 있는 값");
  });

  it("**규칙을 어긴 설정은 숨기지 않는다**", () => {
    // 종목 상한이 섹터 상한보다 클 수 없다
    const 어긴것 = { ...DEFAULT_SETTINGS, max_weight_per_stock: 50, max_weight_per_sector: 30 };

    expect(settingsNotices(어긴것).join(" ")).toContain("섹터");
  });

  it("모양이 아예 틀린 값도 말해 준다", () => {
    const 틀린것 = { ...DEFAULT_SETTINGS, sentiment_weight: -5 };

    expect(settingsNotices(틀린것).length).toBeGreaterThan(0);
  });
});

describe("두 길이 같은 함수를 쓴다", () => {
  const 길들 = ["web/app/settings/page.tsx", "web/app/api/settings/route.ts"];

  it.each(길들)("%s 가 mergeStoredSettings 를 쓴다", (경로) => {
    expect(읽기(경로)).toContain("mergeStoredSettings(");
  });

  it.each(길들)("%s 가 settingsNotices 를 쓴다", (경로) => {
    expect(읽기(경로)).toContain("storedSettingNotices(");
  });

  it.each(길들)("%s 가 제 합치기를 다시 쓰지 않는다", (경로) => {
    const 글 = 읽기(경로);

    expect(글, `${경로} 에 합치기 사본이 남아 있다`).not.toMatch(/JSON\.parse\(row\.value\)/);
  });

  it("검증 오류를 아무도 안 읽는 칸에 넣지 않는다", () => {
    expect(읽기("web/app/api/settings/route.ts")).not.toContain("invalid:");
  });
});
