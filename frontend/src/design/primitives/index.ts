/** The primitive vocabulary. Import from here, not from the individual files.
 *
 * Button and Modal are re-exported from `shared/` rather than defined here.
 * They already existed, and `shared/Button.tsx` is itself the result of merging
 * four drifted copies into one — adding a fifth for the app was reproducing the
 * exact problem that file was written to end. The re-export keeps one import
 * site for app code without a second implementation behind it.
 */
export { Button, type ButtonProps, type ButtonShape, type ButtonSize, type ButtonTone } from '../../shared/Button';
export { Modal, type ModalProps } from '../../shared/Modal';

export { Avatar, avatarTone, type AvatarProps } from './Avatar';
export { SegmentBar, StatBar, type Segment, type SegmentBarProps, type StatBarProps } from './Bars';
export { Card, Section, type CardProps, type SectionProps } from './Card';
export { Chip, type ChipProps } from './Chip';
export { DataTable, type Column, type DataTableProps } from './DataTable';
export { Duck, useDuckPulse, type DuckProps, type DuckPulse, type DuckRest, type DuckState } from './Duck';
export { Eyebrow, type EyebrowProps } from './Eyebrow';
export { Field, Input, Select, type FieldProps, type InputProps, type SelectProps } from './Field';
export { countedSegments, Legend, type LegendItem } from './Legend';
export { Lozenge, LOZENGE_CATEGORIES, type LozengeCategory, type LozengeProps } from './Lozenge';
export { NoticeBlock, type NoticeBlockProps } from './NoticeBlock';
export { Prose, ProseBullets, proseBullets, RichText, splitSentences, type Run } from './Prose';
export { Skeleton, SkeletonLines, type SkeletonProps } from './Skeleton';
export { Sparkline, sparklineDomain, type SparklineProps } from './Sparkline';
export { StatGrid, StatTile, type StatTileProps } from './Stat';
export { TabPanel, Tabs, type TabItem, type TabsProps } from './Tabs';
export { ToastRegion, TOAST_TTL_MS, useToasts, type Toast } from './Toast';
export { TerminalFrame, type TerminalFrameProps } from './TerminalFrame';
export { renderWordmark, Wordmark, type WordmarkProps } from './Wordmark';
