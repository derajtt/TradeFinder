export default function Score({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="dim">—</span>;
  const cls = v >= 75 ? 'score-hi' : v >= 55 ? 'score-mid' : 'score-lo';
  return <span className={`score-pill ${cls}`}>{v.toFixed(0)}</span>;
}
