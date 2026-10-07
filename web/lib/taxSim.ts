/**
 * 계좌별 세후 적립 시뮬레이션 (docs/etf.md 11.7, docs/infra.md 25.1003).
 * 계산은 포트폴리오 재계산(`batch/services/tax_sim.py`)이 한다 — 여기서는 저장된 값을 글로만 바꾼다(CLAUDE.md).
 */

export interface TaxSimRow {
  key: string;
  label: string;
  years: number;
  contributed: number;
  after_tax: number | null;
  tax: number | null;
  missing: string[];
}

export interface TaxSim {
  assumption: { monthly_krw: number; price_return_pct: number; dist_yield_pct: number };
  rows: TaxSimRow[];
}

export const TAX_SIM_SQL = "SELECT tax_sim_json FROM portfolio_summary WHERE id = 1";

/** 설정 잎 → 화면 이름 (설정 화면의 칸 이름과 같다) */
const 잎_이름: Record<string, string> = {
  kr_dividend_pct: "국내 배당소득세",
  us_dividend_pct: "미국 배당원천징수",
  us_capital_gains_pct: "해외 양도소득세",
  pension_income_pct: "연금소득세",
  pension_credit_pct: "연금저축 세액공제율",
};

function 만원(x: number): string {
  return `${Math.round(x / 10_000).toLocaleString("ko-KR")}만원`;
}

/** 저장된 값 → 머리 한 줄 + 기간마다 줄. 세율이 빈 줄은 무엇을 넣어야 하는지 적는다 */
export function taxSimLines(sim: TaxSim | null | undefined): { head: string; groups: Array<{ years: number; lines: string[] }> } | null {
  if (!sim || !sim.rows?.length) return null;
  const a = sim.assumption;
  const head =
    `매달 ${만원(a.monthly_krw)}씩, 연 가격 ${a.price_return_pct}% · 분배 ${a.dist_yield_pct}% 를 가정했을 때 ` +
    "(예측이 아닙니다 — 모든 줄이 같은 가정이라 차이는 세금에서만 납니다)";
  const years = [...new Set(sim.rows.map((r) => r.years))];
  return {
    head,
    groups: years.map((y) => {
      const rows = sim.rows.filter((r) => r.years === y);
      const best = Math.max(...rows.map((r) => r.after_tax ?? -Infinity));
      return {
        years: y,
        lines: rows.map((r) =>
          r.after_tax == null
            ? `${r.label}: 설정 세율 미입력 (${r.missing.map((k) => 잎_이름[k] ?? k).join(", ")})`
            : `${r.label}: 세후 ${만원(r.after_tax)} (넣은 돈 ${만원(r.contributed)}, ` +
              `${(r.tax ?? 0) < 0 ? `세액공제가 세금보다 ${만원(-(r.tax ?? 0))} 많음` : `세금 ${만원(r.tax ?? 0)}`})` +
              `${r.after_tax === best && rows.filter((x) => x.after_tax != null).length > 1 ? " ← 가장 큼" : ""}`,
        ),
      };
    }),
  };
}
