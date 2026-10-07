import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { canSaveSettings } from "@/lib/settings";

describe("설정을 읽지 못한 화면에서는 저장하지 않는다 (docs/infra.md 25.351)", () => {
  it("읽기 실패면 저장 불가", () => {
    expect(canSaveSettings(true)).toBe(false);
    expect(canSaveSettings(false)).toBe(true);
  });

  it("화면이 읽기 실패를 폼에 넘기고, 폼이 저장 단추와 저장 함수 둘 다에서 막는다", () => {
    const page = readFileSync("app/settings/page.tsx", "utf8");
    expect(page).toContain("loadFailed={Boolean(error)}");
    const form = readFileSync("components/SettingsForm.tsx", "utf8");
    expect(form).toContain("if (!canSaveSettings(loadFailed)) return;");
    expect(form).toContain("!canSaveSettings(loadFailed)}");
  });
});
