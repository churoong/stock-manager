"use client";

import { useCallback, useEffect, useState } from "react";
import { bodyOf, readJson } from "@/lib/http";
import { accountName } from "@/lib/accountNames";

/**
 * KIS모의투자 도전자 고르기 (docs/paper.md 15.10, docs/infra.md 25.1515) — 둘째 모의 계좌가 베낄 가상 계좌.
 * 둘째 모의 계좌 키(`KIS_VTS2_*`)를 넣어야 실제로 주문이 나간다. 고르는 것은 설정만 바꾸고 주문은 다음 일일 배치가 낸다.
 */
interface State { account: string | null; champion: string; accounts: Array<{ id: string; name: string | null }> }

export default function KisChallenger() {
  const [s, setS] = useState<State | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const r = await readJson<State>("/api/kis/challenger");
    const body = bodyOf<State>(r);
    if (!r.ok || body.errors?.length) setNotice(body.errors?.join(" · ") || "도전자 설정을 읽지 못했습니다");
    else setS(body);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function save(account: string | null) {
    const 물음 = account
      ? `둘째 모의 계좌가 ${accountName(account)} 계좌의 주문을 베끼게 합니다. 지금 도전자가 산 종목은 다음 배치에서 모의 계좌가 정리 주문으로 팝니다(가상 돈). 바꿀까요?`
      : "둘째 모의 계좌 베끼기를 끕니다. 이미 산 종목은 그대로 둡니다. 끌까요?";
    if (!confirm(물음)) return;
    setBusy(true);
    const r = await readJson<{ ok?: boolean }>("/api/kis/challenger", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ account }),
    });
    const body = bodyOf<{ ok?: boolean }>(r);
    setBusy(false);
    setNotice(body.errors?.length ? body.errors.join(" · ") : account ? `도전자를 ${accountName(account)}로 — 다음 배치부터` : "도전자를 껐습니다 — 다음 배치부터");
    await load();
  }

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-base font-semibold">도전자 — 둘째 모의 계좌</h2>
      <p className="mt-1 text-xs text-slate-500">
        챔피언 {s?.champion ?? "H"} 말고 한 계좌를 더 한국투자증권 모의 계좌에 베껴, 챔피언 교체 후보를 실제 주문 경로로 견주고 챔피언이 안 쓰는 주문(시장가 매수 등)의 체결도 잽니다.
        둘째 모의 계좌를 열고 그 앱키·계좌를 저장소 시크릿 <code>KIS_VTS2_APP_KEY</code>·<code>KIS_VTS2_APP_SECRET</code>·<code>KIS_VTS2_ACCOUNT</code> 에 넣어야 주문이 나갑니다(첫째와 다른 계좌).
      </p>
      {s ? (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
          <span>지금: {s.account ? accountName(s.account) : "없음(꺼짐)"}</span>
          <select
            className="rounded border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-800"
            value={s.account ?? ""}
            disabled={busy}
            onChange={(e) => void save(e.target.value || null)}
            aria-label="도전자 계좌"
          >
            <option value="">끄기</option>
            {s.accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {accountName(a.id)}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {notice ? <p className="mt-2 text-xs text-slate-600 dark:text-slate-400">{notice}</p> : null}
    </section>
  );
}
