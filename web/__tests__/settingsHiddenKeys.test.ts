import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import {
  DEFAULT_SETTINGS,
  HIDDEN_SETTING_KEYS,
  hiddenSettingNotices,
  mergeStoredSettings,
  validateSettings,
} from "@/lib/settings";

describe("화면에 칸이 없는 설정이 규칙을 어겨도 저장이 막히지 않는다 (docs/infra.md 25.355)", () => {
  const rows = [{ key: "sentiment_target_top_n", value: "1000" }];

  it("읽을 때 기본값으로 두고, 그래서 저장 검증을 통과한다", () => {
    const merged = mergeStoredSettings(rows);
    expect(merged.sentiment_target_top_n).toBe(
      DEFAULT_SETTINGS.sentiment_target_top_n,
    );
    expect(validateSettings(merged).ok).toBe(true);
  });

  it("조용히 삼키지 않고 알린다", () => {
    expect(hiddenSettingNotices(rows)[0]).toContain("sentiment_target_top_n");
    expect(
      hiddenSettingNotices([{ key: "sentiment_target_top_n", value: "150" }]),
    ).toEqual([]);
  });

  it("그 키들은 정말 화면에 칸이 없다 — 칸이 생기면 이 목록에서 빼야 한다", () => {
    const form = readFileSync("components/SettingsForm.tsx", "utf8");
    for (const key of HIDDEN_SETTING_KEYS) expect(form).not.toContain(key);
  });
});
