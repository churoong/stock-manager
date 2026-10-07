/** 설정 입력: 꺼 둔 조용시간, 숫자 칸의 "-"·빈 칸 (docs/infra.md 25.573, 감사). */

import { describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, numberFieldInput, settingsSchema } from "@/lib/settings";

describe("조용시간", () => {
  it("꺼 두면 시작=해제여도 받는다 — 기본값(켜짐)으로 되살아나지 않게", () => {
    const 설정 = { ...DEFAULT_SETTINGS, quiet_hours: { enabled: false, start: "22:00", end: "22:00", deliver_on_release: true } };
    expect(settingsSchema.safeParse(설정).success).toBe(true);
    const 켬 = { ...설정, quiet_hours: { ...설정.quiet_hours, enabled: true } };
    expect(settingsSchema.safeParse(켬).success).toBe(false);
  });
});

describe("숫자 칸", () => {
  it("\"-\" 와 빈 칸은 부모 값을 바꾸지 않는다 — 손절 -10 을 칠 수 있고, 비워도 0 이 되지 않는다", () => {
    expect(numberFieldInput("-", false)).toEqual({ commit: false, value: null });
    expect(numberFieldInput("", false)).toEqual({ commit: false, value: null });
    expect(numberFieldInput("-10", false)).toEqual({ commit: true, value: -10 });
    expect(numberFieldInput("", true)).toEqual({ commit: true, value: null });
    expect(numberFieldInput("abc", true)).toEqual({ commit: false, value: null });
  });
});

describe("칸이 있는 설정을 읽지 못하면", () => {
  it("모양이 다른 값은 기본값으로 두고 알린다 — null 로 화면이 죽거나 기본값이 저장값처럼 보였다 (25.573)", async () => {
    const { mergeStoredSettings, unreadableSettingNotices } = await import("@/lib/settings");
    const 행 = [
      { key: "horizon_targets", value: "null" },
      { key: "total_investable_amount", value: "{깨짐" },
      { key: "max_weight_per_stock", value: "12" },
    ];
    const 설정 = mergeStoredSettings(행);
    expect(설정.horizon_targets).toEqual(DEFAULT_SETTINGS.horizon_targets);
    expect(설정.max_weight_per_stock).toBe(12);
    const 말 = unreadableSettingNotices(행);
    expect(말).toHaveLength(2);
    expect(말.join(" ")).toContain("horizon_targets");
  });
});

describe("속 객체까지 본다 (25.575, 교차검증)", () => {
  it("속 null·빠진 키는 기본값으로 두고 알린다 — 맨 위만 봐서 화면이 여전히 죽었다", async () => {
    const { mergeStoredSettings, unreadableSettingNotices } = await import("@/lib/settings");
    const 행 = [{ key: "horizon_targets", value: JSON.stringify({ short: null, mid: { target_pct: 25, stop_pct: -15 } }) }];
    expect(mergeStoredSettings(행).horizon_targets).toEqual(DEFAULT_SETTINGS.horizon_targets);
    expect(unreadableSettingNotices(행)).toHaveLength(1);
  });
  it("API 도 화면과 같은 알림을 쓴다", async () => {
    const { readFileSync } = await import("node:fs");
    expect(readFileSync("app/api/settings/route.ts", "utf8")).toContain("storedSettingNotices(rows, settings)");
  });
});

describe("칸마다 맞춘다 (25.576, 교차검증)", () => {
  it("한 칸이 어긋나도 나머지 칸은 저장값 그대로 — 배치(기간마다)와 같게", async () => {
    const { mergeStoredSettings } = await import("@/lib/settings");
    const 설정 = mergeStoredSettings([
      { key: "horizon_targets", value: JSON.stringify({ short: null, mid: { target_pct: 30, stop_pct: -10 }, long: { target_pct: 60, stop_pct: -30 } }) },
      { key: "trend_filter", value: JSON.stringify({ enabled: false }) },
      { key: "fees", value: JSON.stringify({ kr_buy_pct: "0.015", kr_sell_pct: 0.2 }) },
    ]);
    expect(설정.horizon_targets.short).toEqual(DEFAULT_SETTINGS.horizon_targets.short);
    expect(설정.horizon_targets.mid).toEqual({ target_pct: 30, stop_pct: -10 });
    expect(설정.horizon_targets.long.target_pct).toBe(60);
    expect(설정.trend_filter).toEqual({ enabled: false, bear_factor: 0.5 }); // 켜짐으로 뒤집히지 않는다
    expect(설정.fees).toEqual({ kr_buy_pct: null, kr_sell_pct: 0.2, us_buy_pct: null, us_sell_pct: null });
  });

  it("horizon_targets 는 기간 단위로 버린다 — 잎 하나만 틀려도 그 기간은 기본값 (25.579, 배치 기간별_목표 와 같게)", async () => {
    const { mergeStoredSettings } = await import("@/lib/settings");
    const 설정 = mergeStoredSettings([
      { key: "horizon_targets", value: JSON.stringify({ short: { target_pct: 12, stop_pct: -6 }, mid: { target_pct: 30, stop_pct: "x" }, long: { target_pct: 60, stop_pct: -30 } }) },
    ]);
    expect(설정.horizon_targets.mid, "30/−15 는 배치가 한 번도 쓴 적 없는 짝이다").toEqual(DEFAULT_SETTINGS.horizon_targets.mid);
    expect(설정.horizon_targets.short).toEqual({ target_pct: 12, stop_pct: -6 });
  });
});

describe("장중 경로도 화면과 같은 값을 쓴다 (25.579, 교차검증)", () => {
  it("deliver_on_release 가 빠진 꺼진 조용시간은 장중에도 꺼짐", async () => {
    const { mergeStoredSettings, readSetting } = await import("@/lib/settings");
    const 저장 = { enabled: false, start: "22:00", end: "07:00" };
    const 장중 = readSetting("quiet_hours", 저장);
    expect(장중.value.enabled).toBe(false);
    expect(장중.value).toEqual(mergeStoredSettings([{ key: "quiet_hours", value: JSON.stringify(저장) }]).quiet_hours);
    expect(장중.warning).toContain("일부 칸");
  });

  it("문턱 한 칸만 있으면 그 칸은 저장값", async () => {
    const { readSetting } = await import("@/lib/settings");
    expect(readSetting("alert_thresholds", { spike_pct: 3 }).value).toEqual({ spike_pct: 3, volume_multiple: DEFAULT_SETTINGS.alert_thresholds.volume_multiple });
  });
});

describe("규칙을 벗어난 칸만 기본값 (25.582, 교차검증)", () => {
  it("문턱 한 칸이 범위 밖이면 그 칸만 — 멀쩡한 3 을 버리지 않는다", async () => {
    const { readSetting } = await import("@/lib/settings");
    const r = readSetting("alert_thresholds", { spike_pct: 3, volume_multiple: 0.5 });
    expect(r.value).toEqual({ spike_pct: 3, volume_multiple: DEFAULT_SETTINGS.alert_thresholds.volume_multiple });
    expect(r.warning).toContain("그 칸만");
  });
  it("목표·손절은 기간 단위로 되돌린다", async () => {
    const { readSetting } = await import("@/lib/settings");
    const r = readSetting("horizon_targets", { short: { target_pct: 12, stop_pct: -6 }, mid: { target_pct: 30, stop_pct: 5 }, long: { target_pct: 60, stop_pct: -30 } });
    expect(r.value.mid).toEqual(DEFAULT_SETTINGS.horizon_targets.mid);
    expect(r.value.short).toEqual({ target_pct: 12, stop_pct: -6 });
  });
  it("숫자 하나짜리 설정에는 '일부 칸' 이라고 하지 않는다", async () => {
    const { readSetting } = await import("@/lib/settings");
    expect(readSetting("max_weight_per_sector", "30").warning).not.toContain("일부 칸");
    expect(readSetting("alert_thresholds", [3, 3]).warning).not.toContain("일부 칸");
  });
  it("범위 밖 기간은 배치가 기본값을 쓴다고 화면에 알린다", async () => {
    const { unreadableSettingNotices } = await import("@/lib/settings");
    const 알림 = unreadableSettingNotices([{ key: "horizon_targets", value: JSON.stringify({ short: { target_pct: 12, stop_pct: -6 }, mid: { target_pct: 30, stop_pct: 5 }, long: { target_pct: 60, stop_pct: -30 } }) }]);
    expect(알림).toEqual([expect.stringContaining("horizon_targets.mid 가 규칙을 벗어나 배치는")]);
  });
});

describe("두 칸 규칙은 지어내지 않는다 (25.583, 교차검증)", () => {
  it("조용시간 시작=해제는 통째 기본값 — 22:00–07:00 같은 없는 값을 만들지 않는다", async () => {
    const { readSetting } = await import("@/lib/settings");
    const r = readSetting("quiet_hours", { enabled: true, start: "22:00", end: "22:00", deliver_on_release: true });
    expect(r.value).toEqual(DEFAULT_SETTINGS.quiet_hours);
    expect(r.warning).not.toContain("일부 칸");
  });
});

describe("목표·손절 규칙은 그 기간만 되돌린다 (25.587, 교차검증)", () => {
  it("short 목표 0 이면 short 만 기본값 — mid·long 은 사용자 값", async () => {
    const { readSetting } = await import("@/lib/settings");
    const r = readSetting("horizon_targets", { short: { target_pct: 0, stop_pct: -5 }, mid: { target_pct: 33, stop_pct: -11 }, long: { target_pct: 77, stop_pct: -33 } });
    expect(r.value.short).toEqual(DEFAULT_SETTINGS.horizon_targets.short);
    expect(r.value.mid).toEqual({ target_pct: 33, stop_pct: -11 });
    expect(r.value.long).toEqual({ target_pct: 77, stop_pct: -33 });
  });
});

