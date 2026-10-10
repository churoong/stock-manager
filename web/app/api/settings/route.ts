import { NextResponse } from "next/server";
import { PORTFOLIO_SETTING_KEYS, requestRecalc } from "@/lib/portfolio";
import { execute, rowsToObjects } from "@/lib/db";
import { changedSettingKeys, mergeStoredSettings, storedSettingNotices, validateSettings } from "@/lib/settings";

/**
 * 설정 읽기와 쓰기.
 *
 * 저장은 key-value 다. 한 화면에서 전부 보내지만 키별로 나눠 넣는다.
 * 나중에 항목이 늘어도 스키마를 바꾸지 않는다.
 *
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */

/**
 * 설정 읽기. 설정 화면은 서버 컴포넌트(`app/settings/page.tsx`)가 직접 읽고, 이 경로는 포트폴리오 화면이
 * 배당 원천징수 칸을 채우는 데 부른다(`PortfolioView`, 25.573 바로잡음 — "부르는 곳이 없다" 고 적혀 있었다). 그래도 PUT 이 있는 자리라 남겨 두되, 합치기와 "무엇을 띄울까" 를
 * 화면과 **같은 함수**로 맞춰 둔다 (docs/infra.md 25.73). 예전에는 여기만 검증 오류를
 * `invalid` 라는 아무도 안 읽는 칸에 넣어, 부르는 쪽이 생기면 조용히 삼킬 참이었다.
 */
export async function GET() {
  try {
    const rows = rowsToObjects<{ key: string; value: string }>(await execute("SELECT key, value FROM settings"));
    const settings = mergeStoredSettings(rows);
    return NextResponse.json({ settings, warnings: storedSettingNotices(rows, settings) });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "설정을 읽지 못했습니다" },
      { status: 500 },
    );
  }
}

export async function PUT(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ errors: ["요청 본문을 읽지 못했습니다"] }, { status: 400 });
  }

  const result = validateSettings(body);
  if (!result.ok) {
    return NextResponse.json({ errors: result.errors }, { status: 400 });
  }

  try {
    const now = new Date().toISOString();
    // 화면이 불러온 값과 다른 칸만 쓴다 (25.1115) — 다른 기기에서 고친 칸을 옛 값으로 덮지 않게
    const 기준 = (body as { _base?: unknown } | null)?._base;
    const { keys: 쓸칸, partial } = changedSettingKeys(result.value, 기준);
    const statements = 쓸칸.map((key) => ({
      sql:
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)" +
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value," +
        " updated_at = excluded.updated_at",
      args: [key as string, JSON.stringify(result.value[key]), now] as Array<
        string | number | null
      >,
    }));

    const { batch, execute } = await import("@/lib/db");
    // 저장 **전** 값 — 포트폴리오 계산에 쓰는 칸이 바뀌었는지 보려고 (25.629)
    const 전 = new Map<string, string>();
    const 저장됨 = new Map<string, string>();
    try {
      // 표가 작아 통째로 읽고 여기서 거른다 — 질의에 보간을 넣지 않는다(appSql 검사)
      const rs = await execute("SELECT key, value FROM settings");
      for (const row of rs.rows) {
        저장됨.set(String(row[0]), String(row[1]));
        if ((PORTFOLIO_SETTING_KEYS as readonly string[]).includes(String(row[0]))) 전.set(String(row[0]), String(row[1]));
      }
    } catch {
      // 못 읽으면 "바뀌었다" 로 본다 — 아래에서 다시 계산을 깨운다(한 번 더 도는 편이 옛 값을 보이는 편보다 낫다)
    }
    // 이 탭도 바꿨고 다른 곳도 그 사이 바꾼 칸 — 이 탭 값으로 덮었다고 말한다(나중 저장이 이긴다)
    const 겹침 = partial
      ? 쓸칸.filter((k) => {
          const 옛 = (기준 as Record<string, unknown>)[k as string];
          return 저장됨.has(k as string) && 저장됨.get(k as string) !== JSON.stringify(옛);
        })
      : [];
    if (statements.length) await batch(statements);

    // **포트폴리오에 쓰는 설정이 바뀌면 다시 계산을 깨운다** (docs/infra.md 25.629, 감사). 예전에는 매매 저장만 깨워,
    // 수수료·세율·목표/손절·섹터 상한·총액·무위험수익률을 바꿔도 다음 일일 배치 전까지 화면이 옛 값으로 계산된 결과를
    // "지금 설정" 인 것처럼 보였다. 바뀐 칸이 없으면 깨우지 않는다(Actions 분을 아낀다)
    const 바뀜 = PORTFOLIO_SETTING_KEYS.some((k) => 전.get(k) !== JSON.stringify(result.value[k]));
    const recalc = 바뀜 ? await requestRecalc() : null;

    // 목표·손절은 포트폴리오 재계산만으로는 **장중 감시·매도 플래그에 닿지 않는다** — 그 둘은 다음 일일 배치가 새로 만든다
    // (docs/infra.md 25.764, 설정 감사). 말하지 않으면 한 화면에서 복기는 새 값, 매도 플래그는 옛 값으로 판정한 것이 섞여 보였다
    const targets_changed = 전.get("horizon_targets") !== JSON.stringify(result.value["horizon_targets"]);
    return NextResponse.json({
      ok: true,
      warnings: [
        ...result.warnings,
        ...(겹침.length ? [`다른 곳에서 그 사이 바꾼 칸을 이 화면 값으로 덮었습니다: ${겹침.join(", ")}`] : []),
      ],
      saved_at: now,
      saved_keys: 쓸칸,
      recalc,
      targets_changed,
    });
  } catch (error) {
    return NextResponse.json(
      { errors: [error instanceof Error ? error.message : "저장에 실패했습니다"] },
      { status: 500 },
    );
  }
}
