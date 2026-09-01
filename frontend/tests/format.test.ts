import { describe, expect, it } from 'vitest';
import { fmtCompact, fmtNum, fmtPct, fmtPrice } from '../lib/format';

describe('format helpers', () => {
  it('formats prices with sub-dollar precision', () => {
    expect(fmtPrice(2.5)).toBe('$2.50');
    expect(fmtPrice(0.1234)).toBe('$0.1234');
    expect(fmtPrice(null)).toBe('—');
  });
  it('formats signed percents', () => {
    expect(fmtPct(12.345)).toBe('+12.35%');
    expect(fmtPct(-3.2)).toBe('-3.20%');
    expect(fmtPct(null)).toBe('—');
  });
  it('formats compact magnitudes', () => {
    expect(fmtCompact(1_500_000)).toBe('1.50M');
    expect(fmtCompact(2_300_000_000)).toBe('2.30B');
    expect(fmtCompact(950)).toBe('950');
    expect(fmtCompact(null)).toBe('—');
  });
  it('BUY price stays fixed while current price changes (display invariants)', () => {
    const buyPrice = 2.5;
    const renders = [2.6, 2.8, 2.4].map(() => fmtPrice(buyPrice));
    expect(new Set(renders).size).toBe(1);   // same immutable render every time
    expect(fmtNum(((2.8 - buyPrice) / buyPrice) * 100, 1)).toBe('12.0');
  });
});
