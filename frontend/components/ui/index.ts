/** Component vocabulary barrel (spec §4). Every shared UI piece is a named export here. */
export { StatTile, type StatTileProps } from './StatTile';
export { StatusPill, pillFor, type StatusPillProps } from './StatusPill';
export { SectionHeader, type SectionHeaderProps } from './SectionHeader';
export { Drawer, type DrawerProps } from './Drawer';
export { Tip, Term, termText } from './Popover';
export { EmptyState, type EmptyStateProps } from './EmptyState';
export { EvidenceTag } from './EvidenceTag';
export { ScorePill } from './ScorePill';
export { WhatsMissing, type WhatsMissingProps } from './WhatsMissing';
export { DataTable, type Column, type DataTableProps } from './DataTable';
export { Details } from './Details';
export { Advanced, SimpleOnly } from './Advanced';
export { ModeToggle } from './ModeToggle';
export { GlossaryPanel } from './GlossaryPanel';
export { SignalTable, type SignalTableProps } from '../SignalTable';
export { StrategyScope, useScopeLabel, useProfiles, scopeLabelFor } from '../StrategyScope';
export type { Evidence, BacktestSplit, DrawdownBasis, SampleClass } from '../../lib/evidence';
export type { Tone, Label } from '../../lib/vocab';
