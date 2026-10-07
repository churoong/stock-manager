import { describe, expect, it } from "vitest";
import { USAGE_LABEL, usageAmount, usageLabel } from "@/lib/limits";

/**
 * 게이지 한 줄이 읽히는가 (docs/infra.md 25.123).
 *
 * `api_usage` 는 뜻이 다른 셋을 한 표에 담는다 — 호출 횟수, 행 수, 그리고 **바이트**.
 * 마지막 것을 그대로 찍으면 `524,288,000 / 524,288,000` 이라 아무도 못 읽는다.
 * 용량 경고는 **리셋이 없는 한도**의 경고라 가장 읽혀야 하는 줄이다.
 */
describe("게이지 이름", () => {
  it("아는 이름은 사람 말로 바꾼다", () => {
    expect(usageLabel("d1_db_size")).toBe("D1 DB 용량 (리셋 없음)");
    expect(usageLabel("d1_reads")).toContain("훑은 행");
  });

  it("모르는 이름은 그대로 보여 준다", () => {
    // 새 카운터가 생겼을 때 화면에서 **사라지면** 안 된다. 낯선 이름이라도 보여야 알아챈다
    expect(usageLabel("새_카운터")).toBe("새_카운터");
  });
});

describe("게이지 숫자", () => {
  it("용량은 MB 로 찍는다", () => {
    expect(usageAmount("d1_db_size", 524_288_000)).toBe("500MB");
    expect(usageAmount("d1_db_size", 471_859_200)).toBe("450MB");
  });

  it("행 수와 호출 수는 그대로 센다", () => {
    // 훑은 행을 MB 로 찍으면 완전히 다른 뜻이 된다
    expect(usageAmount("d1_reads", 4_800_000)).toBe((4_800_000).toLocaleString());
    expect(usageAmount("dart_opendart", 1_234)).toBe((1_234).toLocaleString());
  });

  it("한도를 모르면 모른다고 적는다", () => {
    expect(usageAmount("yfinance", null)).toBe("한도 모름");
    expect(usageAmount("d1_db_size", null)).toBe("한도 모름");
  });
});

describe("이름표가 배치의 목록을 따라간다", () => {
  it("우리가 쓰는 카운터에는 전부 이름이 있다", () => {
    // `batch/core/db.API_NAMES` 가 정의처다. 여기 없는 이름은 화면에서 `d1_db_size` 꼴로 뜬다 —
    // 동작은 하지만 읽히지 않는다. 파이썬 목록과의 **글자** 대조는 tests/test_api_usage_names.py 가 한다
    for (const name of ["dart_opendart", "krx_openapi", "sec_edgar", "turso_writes", "turso_reads", "d1_writes", "d1_reads", "d1_db_size"]) {
      expect(USAGE_LABEL[name], `${name} 에 이름표가 없다`).toBeTruthy();
    }
  });
});
