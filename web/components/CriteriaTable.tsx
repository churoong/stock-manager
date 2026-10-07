import { criterionStatus, daysSince, type Criterion } from "@/lib/recommend";

/**
 * 근거표. 종목 추천과 ETF 장기 적립이 같은 모양으로 쓴다.
 *
 * 사용자 원칙: 모든 추천은 왜인지 분명해야 하고 사용자가 확인할 수 있어야 한다.
 * 한 행은 "이 기준을 이 값이 이렇게 통과했다" 다. 배치가 만든 것을 그대로 그린다.
 *
 * 기준일이 오래된 행은 눈에 띄게 한다. 배치가 며칠 안 돌았는데 화면은 멀쩡해
 * 보이는 상황을 막는다. "2025 사업보고서" 처럼 날짜가 아닌 기준일은 건너뛴다.
 */
export default function CriteriaTable({
  rows,
  caption,
  docPath,
  staleAfterDays,
}: {
  rows: Criterion[];
  /** 요약 줄 오른쪽. 예: "신호 계산일 2026-09-16" */
  caption: string;
  /** 문턱을 정의한 문서. 확인하려는 사람이 어디를 읽으면 되는지 알려 준다 */
  docPath: string;
  /** 이보다 오래된 기준일을 노랗게. 배치 주기에 따라 화면마다 다르다 */
  staleAfterDays: number;
}) {
  // **근거가 한 줄도 없으면 표가 아니라 경고를 그린다** (2026-09-21, docs/infra.md 25.71).
  //
  // CLAUDE.md 절대 규칙: "근거표를 만들 수 없는 추천은 표시하지 않는다." 그 규칙을 지키는
  // 자리는 배치다 — `services/signals.verifiable()` 이 근거를 못 만드는 신호를 내보내지
  // 않는다. 여기는 **마지막 줄**이다. 그런데도 0줄이 도착했다면 그 장치가 고장 났다는 뜻이다.
  //
  // 그때 조용히 숨기지 않는다. 숨기면 "추천이 왜 없지" 로만 보이고 고장은 그대로 남는다
  // (배치 쪽도 같은 이유로 버리는 대신 `unverifiable` 로 찍는다). 대신 **이 추천을 믿지
  // 말라고 크게 적는다.** 예전에는 "0개 기준" 이라고만 적힌 빈 표가 열렸다.
  if (rows.length === 0) {
    return (
      <p className="mt-2 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950/40 dark:text-red-300">
        <b>근거를 만들 수 없습니다 — 이 추천을 믿지 마세요.</b>{" "}
        저장된 근거가 비어 있거나 모양이 맞지 않습니다({caption}). 배치의 근거표 생성이
        고장 났을 수 있습니다. <code className="rounded bg-red-100 px-1 dark:bg-red-900">{docPath}</code>
      </p>
    );
  }

  return (
    <details className="mt-2 rounded-lg border border-slate-200 text-xs dark:border-slate-800">
      <summary className="flex min-h-11 cursor-pointer select-none flex-wrap items-center px-3 py-2 text-slate-600 hover:bg-slate-50 sm:block sm:min-h-0 dark:text-slate-300 dark:hover:bg-slate-900">
        근거 보기
        <span className="ml-2 text-slate-400">
          {rows.length}개 기준 · {caption}
        </span>
      </summary>
      {/*
        폰: 기준마다 두 줄 — 판정·기준·기준일, 그 아래 값·문턱·출처.
        여섯 칸 표는 폰에서 옆으로 넘쳤다(2026-09-18 "표가 옆으로 넘친다"). 보이는 값은 표와 같다
      */}
      <ul className="divide-y divide-slate-100 border-t border-slate-200 sm:hidden dark:divide-slate-800 dark:border-slate-800">
        {rows.map((r, i) => {
          const age = daysSince(r.as_of);
          const stale = age !== null && age > staleAfterDays;
          return (
            <li key={`${r.label}-${i}`} className={`px-3 py-2 ${stale ? "bg-amber-50 dark:bg-amber-950/40" : ""}`}>
              <div className="flex items-center gap-2">
                <StatusBadge passed={r.passed} />
                <span className="min-w-0 flex-1 font-medium">{r.label}</span>
                <span className={`shrink-0 ${stale ? "font-medium text-amber-700 dark:text-amber-300" : "text-slate-400"}`}>
                  {r.as_of ?? "-"}
                  {stale && age !== null ? ` (${age}일 전)` : ""}
                </span>
              </div>
              <p className="mt-0.5 text-slate-600 dark:text-slate-300">
                {r.display}
                <span className="text-slate-400">
                  {" "}· 문턱 {r.threshold} · {r.source}
                </span>
              </p>
            </li>
          );
        })}
      </ul>
      <div className="hidden overflow-x-auto sm:block">
        <table className="w-full border-t border-slate-200 dark:border-slate-800">
          <thead className="bg-slate-50 text-left text-slate-500 dark:bg-slate-900">
            <tr>
              <th className="px-3 py-1.5 font-medium">판정</th>
              <th className="px-3 py-1.5 font-medium">기준</th>
              <th className="px-3 py-1.5 font-medium">값</th>
              <th className="px-3 py-1.5 font-medium">문턱</th>
              <th className="px-3 py-1.5 font-medium">출처</th>
              <th className="px-3 py-1.5 font-medium">기준일</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const age = daysSince(r.as_of);
              const stale = age !== null && age > staleAfterDays;
              return (
                <tr
                  key={`${r.label}-${i}`}
                  className={`border-t border-slate-100 align-top dark:border-slate-800 ${
                    stale ? "bg-amber-50 dark:bg-amber-950/40" : ""
                  }`}
                >
                  <td className="whitespace-nowrap px-3 py-1.5">
                    <StatusBadge passed={r.passed} />
                  </td>
                  <td className="whitespace-nowrap px-3 py-1.5 font-medium">{r.label}</td>
                  <td className="px-3 py-1.5">{r.display}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-slate-500">{r.threshold}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-slate-500">{r.source}</td>
                  <td
                    className={`whitespace-nowrap px-3 py-1.5 ${
                      stale ? "font-medium text-amber-700 dark:text-amber-300" : "text-slate-500"
                    }`}
                  >
                    {r.as_of ?? "-"}
                    {stale && age !== null ? ` (${age}일 전)` : ""}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="px-3 py-2 text-slate-400">
        표의 모든 값은 저장된 행에서 왔습니다. 문턱은{" "}
        <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">{docPath}</code>가
        정의합니다. 노란 행은 기준일이 {staleAfterDays}일 넘게 지난 값입니다.
      </p>
    </details>
  );
}

/**
 * 판정 열. 통과·탈락·참고를 글자로 적는다.
 *
 * 참고 행은 판정에 쓰지 않은 정보다(예: 상위 보유 비중, 분배금 처리). 통과로 보이면
 * "이 기준을 넘었다" 로 오해하므로 따로 적는다.
 */
function StatusBadge({ passed }: { passed: boolean | null }) {
  const status = criterionStatus(passed);
  const tone =
    status.tone === "ok"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : status.tone === "fail"
        ? "bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-300"
        : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400";
  return <span className={`rounded px-1.5 py-0.5 text-[11px] ${tone}`}>{status.text}</span>;
}
