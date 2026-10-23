/**
 * 거래내역 쪽 나누기 (2026-10-23 사용자 지시 "거래내역 탭이 점점 길어져. 페이징화해야할듯해", docs/infra.md 25.1767).
 *
 * 쪽은 **화면에서만** 나눈다 — 서버는 지금처럼 기간(KIS 90일)·건수(가상 계좌마다 300건) 안을 한 번에 준다. 쪽마다 DB 를 다시
 * 읽으면 Turso 읽기만 늘고(쪽 넘길 때마다 같은 표를 훑는다) 이미 받은 줄로 충분하다.
 */

/** 한 쪽의 줄 수 — 폰 한두 화면 */
export const PAGE_SIZE = 20;

export interface Page<T> {
  rows: T[];
  /** 0 부터. 범위를 넘으면 마지막 쪽으로 당긴다(줄이 줄어든 뒤 다시 그릴 때) */
  page: number;
  pages: number;
  total: number;
}

export function pageOf<T>(rows: T[], page: number, size: number = PAGE_SIZE): Page<T> {
  const pages = Math.max(1, Math.ceil(rows.length / size));
  const p = Math.min(Math.max(0, Math.floor(page) || 0), pages - 1);
  return { rows: rows.slice(p * size, (p + 1) * size), page: p, pages, total: rows.length };
}

/** "21–40 / 57건" — 몇 번째 줄을 보고 있는지 */
export function pageRangeText(p: Page<unknown>, size: number = PAGE_SIZE): string {
  if (!p.total) return "0건";
  const 첫 = p.page * size + 1;
  return `${첫}–${첫 + p.rows.length - 1} / ${p.total.toLocaleString("ko-KR")}건`;
}
