"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import { resultLabel, searchUrl, type SearchHit } from "@/lib/stockSearch";

/**
 * 메뉴의 종목 검색 (2026-09-18 사용자 확인 중 발견).
 *
 * 종목을 이름으로 찾아 상세로 가는 길이 없었다. 검색 칸은 알림(관심 종목 추가)과
 * 포트폴리오(매매 입력) 안에만 있어, "삼성전자 차트를 보자" 가 메뉴 세 번을 거쳐야 했다.
 * 검색 API 는 원래 있던 `/api/stocks/search` 를 쓴다(티커·야후 심볼·한글·영문 이름).
 */
export default function StockSearch({ compact = false, autoFocus = false }: { compact?: boolean; autoFocus?: boolean }) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);

  // 입력이 멈춘 뒤에 묻는다. 글자마다 부르면 DB 읽기를 낭비한다 (docs/infra.md 24절)
  useEffect(() => {
    const url = searchUrl(query);
    if (!url) {
      setHits([]);
      return;
    }
    let alive = true;
    const timer = setTimeout(() => {
      fetch(url)
        .then((r) => (r.ok ? r.json() : { stocks: [] }))
        .then((body) => {
          if (!alive) return;
          setHits(Array.isArray(body?.stocks) ? (body.stocks as SearchHit[]).slice(0, 8) : []);
          setActive(0);
          setOpen(true);
        })
        .catch(() => alive && setHits([]));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [query]);

  // 바깥을 누르면 닫는다
  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const go = (hit: SearchHit | undefined) => {
    if (!hit) return;
    setOpen(false);
    setQuery("");
    router.push(`/stocks/${hit.id}`);
  };

  return (
    <div ref={box} className={`relative ${compact ? "w-full" : "w-52"}`}>
      <input
        type="search"
        value={query}
        autoFocus={autoFocus}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => hits.length > 0 && setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((i) => Math.min(i + 1, hits.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((i) => Math.max(i - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            go(hits[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
        placeholder="종목 검색 (이름·코드)"
        aria-label="종목 검색"
        className="w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
      />
      {open && query.trim() && (
        <ul className="absolute right-0 z-40 mt-1 max-h-72 w-full min-w-64 overflow-auto rounded-lg border border-slate-200 bg-white text-sm shadow-lg dark:border-slate-700 dark:bg-slate-900">
          {hits.length === 0 ? (
            <li className="px-3 py-2 text-slate-400">찾은 종목이 없습니다</li>
          ) : (
            hits.map((hit, i) => (
              <li key={hit.id}>
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => go(hit)}
                  className={`block w-full px-3 py-2 text-left ${i === active ? "bg-slate-100 dark:bg-slate-800" : ""}`}
                >
                  {resultLabel(hit)}
                </button>
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

/** 폰 위쪽 제목줄의 돋보기. 누르면 검색 칸이 한 줄로 펼쳐진다 (좁은 화면에 늘 펼쳐 두면 제목이 가린다) */
export function PhoneSearch() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={open ? "검색 닫기" : "종목 검색"}
        aria-expanded={open}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-slate-600 active:bg-slate-100 dark:text-slate-300 dark:active:bg-slate-800"
      >
        <Icon name={open ? "close" : "search"} className="h-6 w-6" />
      </button>
      {open && (
        <div className="absolute inset-x-0 top-full border-b border-slate-200 bg-white px-4 py-2 dark:border-slate-800 dark:bg-slate-900">
          <StockSearch compact autoFocus />
        </div>
      )}
    </>
  );
}
