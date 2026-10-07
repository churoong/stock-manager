import { NextResponse } from "next/server";
import { execute, rowsToObjects } from "@/lib/db";
import {
  ETF_LAST_FAILED, MARKETS, buildExcludedQuery, buildPickedQuery, buildTiltLeadersQuery, etfFailedNote, etfNameExcludedNote, etfRunNote, etfRunNotStored, type EtfRow,
} from "@/lib/etf";
import { userDateOf } from "@/lib/market";

/**
 * ETF 장기 적립 조회.
 *
 * 규칙이 고른 결과와, 순자산이 큰데 빠진 것("왜 없나")을 시장별로 돌려준다.
 * 미들웨어가 이 경로를 인증 뒤에 두므로 여기서 다시 검사하지 않는다.
 */
export async function POST() {
  try {
    const picked = buildPickedQuery();
    const pickedRows = rowsToObjects<EtfRow>(await execute(picked.sql, picked.args));

    // 우리 종목 집중 순위 (docs/etf.md 11.5, 25.968) — 핵심 통과가 아니어도 들어온다
    const leadersQ = buildTiltLeadersQuery();
    const tiltLeaders = rowsToObjects<EtfRow>(await execute(leadersQ.sql, leadersQ.args));
    // 국내 상장 — 미국 지수를 따르는 것만 대리 보유로 들어온다 (25.970)
    const leadersKrQ = buildTiltLeadersQuery(undefined, "KR");
    const tiltLeadersKr = rowsToObjects<EtfRow>(await execute(leadersKrQ.sql, leadersKrQ.args));

    const excluded: Record<string, EtfRow[]> = {};
    for (const { country } of MARKETS) {
      const q = buildExcludedQuery(country);
      excluded[country] = rowsToObjects<EtfRow>(await execute(q.sql, q.args));
    }

    // "이 화면이 언제 것인가". 예약 실행은 지연되므로 실제 실행 시각을 보여준다.
    //
    // **빈 화면의 이유를 가리는 데도 쓴다** (2026-09-21, docs/infra.md 25.86).
    // 예전에는 표가 비면 무조건 "아직 돌린 적이 없습니다. Actions 에서 돌리세요" 였다.
    // 그런데 이 질의가 바로 아래에 이미 있었다 — **가르는 데 필요한 값을 읽고도 안 썼다.**
    // 돌았는데 낼 것이 없었던 것이라면 돌리라고 시켜 봐야 소용이 없다.
    //
    // **시장마다 따로 읽는다** (docs/infra.md 25.354). 작업 이름 `etf` 하나로 국내·미국을 모두 기록하므로
    // 시장을 가리지 않으면 국내 판정이 실패하고 미국만 성공한 날 국내 탭에 미국 실행 시각이 찍혔다
    const lastRuns: Record<
      string,
      { finished_at: unknown; status: unknown; note: string | null; name_note?: string | null } | null
    > = {};
    for (const { country } of MARKETS) {
      // 시험 실행(`--only`)은 건너뛴다 — 저장하지 않은 시험 시각이 "마지막 배치" 로 찍혔다. 위성 경로와 같다 (25.594·25.662)
      const rs = await execute(
        "SELECT finished_at, status, step_log FROM batch_runs"
          + " WHERE job_name = 'etf' AND market = ? AND status IN ('success', 'partial')"
          + " ORDER BY finished_at DESC LIMIT 10",
        [country],
      );
      const row = rs.rows.find((r) => !String(r[2] ?? "").includes("--only"));
      // 이름으로 뺀 목록은 **그것을 적은 가장 새 실행**에서 읽는다 (25.722, 교차검증) — 재판정·차단으로 끝난 실행은 목록을 적지 않아
      // 안내 줄이 사라졌다
      const 이름_줄 = rs.rows.map((r) => etfNameExcludedNote(r[2])).find((n) => n !== null) ?? null;
      // 마지막 판정이 실패했으면 그 말이 먼저다 (25.826)
      const 실패 = rowsToObjects<{ started_at: unknown; error_text: unknown }>(await execute(ETF_LAST_FAILED, [country]))[0];
      const 실패말 = etfFailedNote(실패, row?.[0] ?? null);
      // 일부만 끝난 판정은 까닭을 함께 싣는다 (25.580)
      lastRuns[country] = row
        ? {
            finished_at: row[0], status: row[1], note: 실패말 ?? etfRunNote({ status: row[1], step_log: row[2] }),
            name_note: 이름_줄,  // 25.718·25.722
          }
        : 실패말 ? { finished_at: null, status: "failed", note: 실패말, name_note: null } : null;
    }
    // 실패만 있는 시장의 줄(끝난 시각 없음)은 "마지막 판정" 고르기에서 뺀다 — "null" 글자가 날짜보다 앞서 정렬돼 다른 시장이 성공한 날도
    // 날짜가 "-" 로 나왔다 (25.831, 교차검증)
    const lastAny = Object.values(lastRuns)
      .filter((r): r is { finished_at: unknown; status: unknown; note: string | null } => r !== null && Boolean(r.finished_at))
      .sort((a, b) => String(b.finished_at).localeCompare(String(a.finished_at)))[0];
    const last = lastAny ? [lastAny.finished_at, lastAny.status] : undefined;

    const notes: string[] = [];
    const anyExcluded = Object.values(excluded).some((rows) => rows.length > 0);
    if (pickedRows.length === 0 && !anyExcluded) {
      notes.push(
        last
          ? "ETF 판정은 돌았는데(" + (userDateOf(String(last[0] ?? "")) || "-") + ") 후보로 읽어 온 ETF 가 없었습니다."
            + " 종목 마스터에 ETF 가 아직 없을 수 있습니다. 고장이 아니라 재료가 없는 것입니다"
          // 실패만 있으면 "돌린 적이 없다" 가 아니다 — 시장 줄의 실패 사유를 가리킨다 (25.831)
          : Object.values(lastRuns).some((r) => r?.status === "failed")
            ? "ETF 판정이 아직 한 번도 성공하지 못했습니다 — 시장 줄의 실패 사유를 보세요"
            : "아직 ETF 판정을 돌린 적이 없습니다. Actions → ETF 장기 적립 판정을 돌리세요",
      );
    } else if (pickedRows.length === 0) {
      // 일부만 끝난 판정이면 "오류가 아니다" 라고 단정하지 않는다 (25.580) — 까닭은 시장별 줄(`last_runs.note`)이 말한다
      // 저장하지 않은 partial 이면 화면의 0개는 지난 온전한 판정의 결과다 — 그 실행을 탓하지 않는다 (25.583, 교차검증)
      // 실패 안내는 "일부만 끝남" 이 아니다 (25.831)
      const 일부 = Object.values(lastRuns).some((r) => r?.note && r.status !== "failed" && !etfRunNotStored(r.note) && !r.note.startsWith("가장 최근 ETF 판정이 실패"));
      notes.push(
        일부
          ? "이번 판정에서 조건을 모두 통과한 ETF 가 없습니다. 마지막 판정이 일부만 끝나 그 때문일 수 있습니다"
          : "이번 판정에서 조건을 모두 통과한 ETF 가 없습니다. 오류가 아니라 조건을 만족하지 못한 것입니다",
      );
    }

    return NextResponse.json({
      picked: pickedRows,
      excluded,
      tilt_leaders: tiltLeaders,
      tilt_leaders_kr: tiltLeadersKr,
      last_run: last ? { finished_at: last[0], status: last[1] } : null,
      last_runs: lastRuns,
      notes,
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "조회에 실패했습니다";
    // 마이그레이션 전이면 표나 열이 없다. 앱이 깨진 것이 아니라 아직 안 돌린 것이다.
    if (/no such (table|column)/i.test(message)) {
      return NextResponse.json({
        picked: [],
        excluded: {},
        last_run: null,
        notes: ["ETF 표가 아직 준비되지 않았습니다. ETF 장기 적립 판정 배치를 한 번 돌리면 만들어집니다"],
      });
    }
    return NextResponse.json({ errors: [message] }, { status: 500 });
  }
}
