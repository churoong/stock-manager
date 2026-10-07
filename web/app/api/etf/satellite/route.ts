import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  buildSatelliteExcludedQuery,
  buildSatellitePassedQuery,
  groupSatellites,
  type SatelliteRow,
} from "@/lib/etfSatellite";
import { userDateOf, type Country } from "@/lib/market";
import { ETF_LAST_FAILED, etfFailedNote, etfRunNote, etfRunNotStored } from "@/lib/etf";

/**
 * 위성 ETF 조회 (docs/etf.md 10장). 나라 하나씩 부른다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function POST(request: Request) {
  let country: Country = "KR";
  try {
    const body = await request.json();
    if (body?.country === "US") country = "US";
  } catch {
    // 본문이 없으면 국내
  }

  try {
    const passedQ = buildSatellitePassedQuery(country);
    const excludedQ = buildSatelliteExcludedQuery(country);
    const passed = rowsToObjects<SatelliteRow>(await execute(passedQ.sql, passedQ.args));
    const excluded = rowsToObjects<SatelliteRow>(await execute(excludedQ.sql, excludedQ.args));
    const notes: string[] = [];
    if (passed.length === 0 && excluded.length === 0) {
      // **돌았는지 보고 말한다** (docs/infra.md 25.581, 감사 — 핵심 ETF 의 25.86·25.486 과 같은 모양). 예전에는 돌았는데 범위에 든
      // ETF 가 0개인 시장에서도 "돌린 적이 없습니다 … 돌리세요" 라고 해 소용없는 실행을 시켰다
      const 기록 = (await execute(
        "SELECT finished_at FROM batch_runs WHERE job_name = 'etf_satellite' AND market = ?"
          + " AND status IN ('success', 'partial') ORDER BY finished_at DESC LIMIT 1",
        [country],
      )).rows[0];
      notes.push(
        기록
          ? `위성 판정은 돌았는데(${userDateOf(String(기록[0] ?? "")) || "-"}) 범위에 든 ETF 가 없었습니다. 고장이 아니라 재료가 없는 것입니다`
          : "아직 위성 판정을 돌린 적이 없습니다. Actions → ETF 장기 적립 판정 → satellite_only 로 판정을 돌리세요",
      );
    }
    // **핵심 판정이 저장을 건너뛴 달은 그 까닭을 여기서도 말한다** (docs/infra.md 25.588). 위성은 핵심 판정과 같은 프로필을 읽어,
    // 입력이 모자란 달(25.580·25.583)엔 지난달 기준일을 보여 주는데 이유가 없었다 — 핵심 탭만 `etfRunNote` 로 말했다
    // 시험 실행(`--only`)은 건너뛰고 **본 실행 가운데 가장 최근 것**을 본다 (25.594, 교차검증 — 가장 최근 하나만 보면 진짜 "저장하지 않았습니다"
    // 뒤에 시험 실행을 한 번 돌렸을 때 프로필이 여전히 지난 것인데 안내가 사라졌다)
    const 핵심들 = (await execute(
      "SELECT status, step_log FROM batch_runs WHERE job_name = 'etf' AND market = ?"
        + " AND status IN ('success', 'partial') ORDER BY finished_at DESC LIMIT 5",
      [country],
    )).rows;
    const 핵심말 = 핵심들
      .map((r) => ({ 말: etfRunNote({ status: r[0], step_log: r[1] }), 시험: String(r[1] ?? "").includes("--only") }))
      .find((x) => !x.시험)?.말 ?? null;
    if (etfRunNotStored(핵심말)) notes.push(`${핵심말} (위성 판정도 지난 프로필 기준입니다)`);
    // 핵심 판정이 실패로 끝난 달도 말한다 — 위성은 그 프로필을 읽는다 (25.826)
    const 마지막_성공 = (await execute(
      // 시험 실행(`--only`)의 성공은 세지 않는다 — 핵심 탭과 같은 기준 (25.831, 교차검증)
      "SELECT MAX(finished_at) FROM batch_runs WHERE job_name = 'etf' AND market = ? AND status IN ('success', 'partial')"
        + " AND COALESCE(step_log, '') NOT LIKE '%--only%'",
      [country],
    )).rows[0]?.[0] ?? null;
    const 실패말 = etfFailedNote(
      rowsToObjects<{ started_at: unknown; error_text: unknown }>(await execute(ETF_LAST_FAILED, [country]))[0], 마지막_성공,
    );
    if (실패말) notes.push(`${실패말} (위성 판정도 지난 프로필 기준입니다)`);
    return NextResponse.json({
      groups: groupSatellites(passed, excluded),
      as_of: passed[0]?.as_of_date ?? excluded[0]?.as_of_date ?? null,
      notes,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    if (/no such (table|column)/i.test(message)) {
      return NextResponse.json({
        groups: [],
        as_of: null,
        notes: ["위성 ETF 표가 아직 없습니다. 위성 판정 배치를 한 번 돌리면 만들어집니다"],
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
