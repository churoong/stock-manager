import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, readSetting } from "@/lib/settings";

/**
 * **검증이 쓸 때만 돌고 있었다** (docs/infra.md 25.172).
 *
 * `web/lib/settings.ts` 머리말은 "설정은 웹앱만 쓴다 … 따라서 검증은 여기 한 곳에 둔다"
 * 고 적는다. 그 검증은 **`POST /api/settings` 에서만** 돈다.
 * `scripts/restore_backup.py` 와 `scripts/move_user_data.py` 는 `settings` 행을 직접
 * 써 넣고, 옛 백업에는 지금 범위를 벗어난 값이 들어 있을 수 있다.
 *
 * 파이썬 쪽은 25.170·25.171 에서 막았다. **웹에 같은 구멍이 남아 있었다.**
 *
 * 가장 나쁜 값은 `spike_pct` 다. 0.001 이면 **모든 종목이 매 5분 급등락으로 걸리고**,
 * 텔레그램 429 로 이어진다 — 2026-09-20 에 실제로 겪었고 Actions 예산까지 먹었다(25.38).
 */

describe("읽을 때도 스키마로 본다", () => {
  it("멀쩡한 값은 그대로", () => {
    const { value, warning } = readSetting("alert_thresholds", { spike_pct: 7, volume_multiple: 4 });

    expect(value).toEqual({ spike_pct: 7, volume_multiple: 4 });
    expect(warning).toBeNull();
  });

  it("안 넣은 값은 기본값이고 조용하다", () => {
    // 아직 한 번도 저장 안 한 설정이다. 그때마다 경고하면 매일 시끄럽다
    expect(readSetting("alert_thresholds", undefined)).toEqual({
      value: DEFAULT_SETTINGS.alert_thresholds,
      warning: null,
    });
    expect(readSetting("alert_thresholds", null).warning).toBeNull();
  });

  it("범위 밖이면 기본값으로 되돌리고 말한다", () => {
    const { value, warning } = readSetting("alert_thresholds", { spike_pct: 0.001, volume_multiple: 3 });

    expect(value, "0.001% 면 모든 종목이 매 5분 걸린다").toEqual(DEFAULT_SETTINGS.alert_thresholds);
    expect(warning).toContain("alert_thresholds");
    expect(warning, "무엇을 해야 하는지 말해야 한다").toContain("설정 화면에서 고치세요");
  });

  it("칸이 모자라도 되돌린다", () => {
    const { value, warning } = readSetting("alert_thresholds", { spike_pct: 5 });

    expect(value).toEqual(DEFAULT_SETTINGS.alert_thresholds);
    expect(warning).not.toBeNull();
  });

  it("조용시간이 깨져도 되돌린다", () => {
    // **조용시간이 깨지면 알림이 하루 종일 안 나갈 수 있다.** 그것도 조용히
    const { value, warning } = readSetting("quiet_hours", { enabled: "네", start: 9 });

    expect(value).toEqual(DEFAULT_SETTINGS.quiet_hours);
    expect(warning).not.toBeNull();
  });

  it("경계는 통과한다", () => {
    expect(readSetting("alert_thresholds", { spike_pct: 0.1, volume_multiple: 1 }).warning).toBeNull();
    expect(readSetting("alert_thresholds", { spike_pct: 50, volume_multiple: 50 }).warning).toBeNull();
  });

  it("되돌릴 곳은 DEFAULT_SETTINGS 하나다", () => {
    // 부르는 쪽이 제 기본값을 적으면 두 곳이 갈라진다 (25.0 「한 규칙이 두 곳에 있다」)
    const 글 = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");

    expect(글, "손으로 적은 기본값이 남아 있다").not.toContain("{ spike_pct: 5, volume_multiple: 3 }");
    expect(글).toContain('readSetting("alert_thresholds"');
    expect(글).toContain('readSetting("quiet_hours"');
  });

  it("장중 경로가 그 경고를 남기고 알린다", () => {
    const 글 = readFileSync(join(process.cwd(), "app", "api", "cron", "intraday", "route.ts"), "utf-8");

    expect(글, "호출 기록에 없으면 나중에 알 길이 없다").toContain("setting_warnings");
    expect(글, "텔레그램 묶음 머리에도 붙어야 사람이 본다").toContain("settings.warnings");
  });

  it("파이썬과 같은 자리를 지킨다", () => {
    // 두 언어가 같은 설정을 다르게 믿으면 안 된다 (25.170·25.171)
    const 표 = readFileSync(join(process.cwd(), "..", "batch", "core", "settings_range.py"), "utf-8");

    expect(표).toContain("spike_pct");
    expect(표).toContain("volume_multiple");
  });
});
