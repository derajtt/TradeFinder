'use client';
import { useMemo, useState } from 'react';
import type { BacktestSplit, Evidence } from '../../lib/evidence';
import { useMode } from '../../lib/mode';
import { EmptyState } from './EmptyState';
import { EvidenceTag } from './EvidenceTag';
import { Term } from './Popover';

export interface Column<T> {
  key: string; header: React.ReactNode; term?: string; align?: 'l' | 'r';
  simple?: boolean;                    // shown in Simple; default false (Advanced only)
  cell: (row: T) => React.ReactNode;
  isEmpty?: (row: T) => boolean;       // suppression test; default: cell returns null/undefined/''/'—'
  sortValue?: (row: T) => number | string | null;
  width?: number;
}

export interface DataTableProps<T> {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  defaultSort?: { key: string; dir: 'asc' | 'desc' };
  cap?: number;                        // Simple-only row cap → footer "Show all {n}"
  suppressEmptyAbove?: number;         // default 0.8 — Simple only
  suppressedNote?: (hidden: Column<T>[]) => React.ReactNode;
  note?: React.ReactNode; evidence?: Evidence; split?: BacktestSplit;
  empty?: React.ReactNode; loaded?: boolean; minWidth?: number; dense?: boolean;
  rowClassName?: (row: T) => string | undefined;
}

function defaultEmpty(v: React.ReactNode): boolean {
  if (v === null || v === undefined || v === false) return true;
  if (typeof v === 'string') { const s = v.trim(); return s === '' || s === '—'; }
  return false;
}
function colName<T>(c: Column<T>): string {
  return typeof c.header === 'string' || typeof c.header === 'number' ? String(c.header) : c.key;
}

/** One table implementation: mode-aware columns, the >80%-empty suppression rule
 *  (Simple), row cap with "Show all", click sorting, skeleton until `loaded`,
 *  and never a `title=` on a header. */
export function DataTable<T>(props: DataTableProps<T>) {
  const { rows, columns, rowKey, onRowClick, defaultSort, cap, suppressEmptyAbove = 0.8, suppressedNote,
    note, evidence, split, empty, loaded = true, minWidth, dense, rowClassName } = props;
  const { advanced } = useMode();
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(defaultSort ?? null);
  const [showAll, setShowAll] = useState(false);

  const anySimple = columns.some((c) => c.simple);
  const modeCols = useMemo(
    () => (advanced || !anySimple ? columns : columns.filter((c) => c.simple)),
    [advanced, anySimple, columns]);

  // Suppression runs over ALL rows (not the capped subset), Simple only.
  const { visible, hidden } = useMemo(() => {
    if (advanced || !rows.length || suppressEmptyAbove >= 1) return { visible: modeCols, hidden: [] as Column<T>[] };
    const vis: Column<T>[] = []; const hid: Column<T>[] = [];
    for (const c of modeCols) {
      const test = c.isEmpty ?? ((r: T) => defaultEmpty(c.cell(r)));
      let e = 0;
      for (const r of rows) if (test(r)) e++;
      (e / rows.length > suppressEmptyAbove ? hid : vis).push(c);
    }
    return vis.length ? { visible: vis, hidden: hid } : { visible: modeCols, hidden: [] as Column<T>[] };
  }, [advanced, rows, modeCols, suppressEmptyAbove]);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col?.sortValue) return rows;
    const sv = col.sortValue;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = sv(a), bv = sv(b);
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, sort, columns]);

  const capped = !advanced && cap && !showAll ? sorted.slice(0, cap) : sorted;
  const clickSort = (c: Column<T>) => {
    if (!c.sortValue) return;
    setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 'asc' ? 'desc' : 'asc' } : { key: c.key, dir: 'desc' }));
  };
  const hiddenNote = hidden.length
    ? (suppressedNote ? suppressedNote(hidden) : `${hidden.map(colName).join(' / ')} hidden — empty for most rows`)
    : null;
  const colCount = Math.max(1, visible.length);

  return (
    <div className="dt">
      {(note || evidence || hiddenNote) ? (
        <div className="tbl-note">
          {evidence ? <EvidenceTag evidence={evidence} split={split} /> : null}
          {note ? <span>{note}</span> : null}
          {hiddenNote ? <span className="dim">{hiddenNote}</span> : null}
        </div>
      ) : null}
      <div className="tbl-wrap">
        <table className={`tbl${dense ? ' tbl--dense' : ''}`} style={minWidth ? { minWidth } : undefined}>
          <thead>
            <tr>
              {visible.map((c) => {
                const active = sort?.key === c.key;
                const sortable = !!c.sortValue;
                return (
                  <th key={c.key} className={`${c.align === 'l' ? 'l' : ''}${sortable ? ' sortable' : ''}`}
                    style={c.width ? { width: c.width } : undefined}
                    aria-sort={active ? (sort!.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                    onClick={sortable ? () => clickSort(c) : undefined}>
                    {c.term ? <Term k={c.term}>{c.header}</Term> : c.header}
                    {active ? <span className="sort-arrow" aria-hidden>{sort!.dir === 'asc' ? ' ▲' : ' ▼'}</span> : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {!loaded ? [0, 1, 2].map((i) => (
              <tr key={`skel-${i}`} className="dt-skel" aria-busy="true">
                <td className="l" colSpan={colCount}><div className="skel dt-skel-bar">&nbsp;</div></td>
              </tr>
            )) : null}
            {loaded && rows.length === 0 ? (
              <tr className="dt-empty-row">
                <td className="l" colSpan={colCount}>
                  {empty ?? <EmptyState compact headline="Nothing to show" reason="Nothing has been recorded here yet." />}
                </td>
              </tr>
            ) : null}
            {loaded ? capped.map((r) => {
              const extra = rowClassName?.(r);
              return (
                <tr key={rowKey(r)} className={extra || undefined}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  tabIndex={onRowClick ? 0 : undefined} role={onRowClick ? 'button' : undefined}
                  onKeyDown={onRowClick ? (e) => { if (e.key === 'Enter') onRowClick(r); } : undefined}>
                  {visible.map((c) => <td key={c.key} className={c.align === 'l' ? 'l' : undefined}>{c.cell(r)}</td>)}
                </tr>
              );
            }) : null}
          </tbody>
        </table>
      </div>
      {loaded && capped.length < sorted.length ? (
        <button type="button" className="showall" onClick={() => setShowAll(true)}>Show all {sorted.length}</button>
      ) : null}
    </div>
  );
}
