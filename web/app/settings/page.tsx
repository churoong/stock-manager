import Footer from "@/components/Footer";
import Nav from "@/components/Nav";
import SettingsForm from "@/components/SettingsForm";
import { execute, rowsToObjects } from "@/lib/db";
import {
  DEFAULT_SETTINGS, mergeStoredSettings, storedSettingNotices, type Settings,
} from "@/lib/settings";

export const dynamic = "force-dynamic";

async function loadSettings(): Promise<{
  settings: Settings;
  warnings: string[];
  error?: string;
}> {
  try {
    const rs = await execute("SELECT key, value FROM settings");
    const stored = rowsToObjects<{ key: string; value: string }>(rs);

    // 합치기와 "무엇을 띄울까" 는 lib/settings.ts 한 곳에 있다 (docs/infra.md 25.73)
    const settings = mergeStoredSettings(stored);
    return {
      settings,
      warnings: storedSettingNotices(stored, settings),
    };
  } catch (error) {
    return {
      settings: DEFAULT_SETTINGS,
      warnings: [],
      error: error instanceof Error ? error.message : "설정을 읽지 못했습니다",
    };
  }
}

export default async function SettingsPage() {
  const { settings, warnings, error } = await loadSettings();

  return (
    <div className="flex min-h-screen flex-col">
      <Nav current="/settings" />

      <main className="mx-auto w-full max-w-2xl flex-1 px-4 py-4">
        {error ? (
          <div className="rounded-lg bg-red-50 px-3 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
            <p className="font-medium">데이터베이스에 연결하지 못했습니다</p>
            <p className="mt-1">{error}</p>
            <p className="mt-2 text-xs">
              아래 값은 기본값이며 저장되지 않은 상태입니다. 저장하면 저장해 둔 값이 기본값으로 덮이므로
              저장 단추를 막았습니다 — 새로고침해 주세요.
            </p>
          </div>
        ) : null}
        <div className={error ? "mt-4" : ""}>
          <SettingsForm initial={settings} initialWarnings={warnings} loadFailed={Boolean(error)} />
        </div>
      </main>

      <Footer />
    </div>
  );
}
