/** The shared component library. Import from here, not from the files. */

export {
  Button,
  buttonClass,
  type ButtonProps,
  type ButtonShape,
  type ButtonSize,
  type ButtonTone,
} from './Button';
export type { PageChrome } from './chrome';
export { CopyField, type CopyFieldProps } from './CopyField';
export { IconButton, type IconButtonProps } from './IconButton';
export { InviteQR, inviteText, type InviteQRProps } from './InviteQR';
export { JoinGate, normalizeCode, type JoinGateProps } from './JoinGate';
export { Modal, type ModalProps } from './Modal';
export { PageShell, type PageShellProps } from './PageShell';
export { MusicPlayer, type MusicPlayerProps } from './MusicPlayer';
export { Popover, PopoverGroup, type PopoverProps } from './Popover';
export {
  PresenceRow,
  Roster,
  TypingIndicator,
  type Participant,
  type PresenceRowProps,
  type RosterProps,
} from './Presence';
export { ProfileModal, type ProfileModalProps } from './ProfileModal';
export { ThemeSwitcher, type ThemeSwitcherProps } from './ThemeSwitcher';
export { ConfettiCanvas, TIMER_PRESETS, TimerControls, TimerReadout } from './Timer';
export { Toast, TOAST_MS, type ToastProps } from './Toast';
export { Toolbar, type ToolbarProps } from './Toolbar';
export { Visualizer, type VisualizerProps } from './Visualizer';
