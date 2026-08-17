/**
 * The board kit.
 *
 * What a live surface needs that is not about its subject: how its columns
 * behave as the screen narrows, how a set of views shares one strip of tabs,
 * how a region reads itself in, how a person moves from one place on the board
 * to another. Grown on the poker board and lifted here so the next surface
 * starts where that one finished.
 */

export { Announce, ANNOUNCE_MS, type AnnounceProps } from './Announce';
export { Panel, Panels, Tabs, tabIds, type PanelProps, type PanelsProps, type TabsProps } from './Tabs';
export { Room, type RoomProps } from './Room';
export { Written, type WrittenProps } from './Written';
export {
  boxOf,
  lastSeen,
  remember,
  travelFrom,
  TRAVEL_EASE,
  TRAVEL_MS,
  useArrival,
  useRowChoreography,
  type FaceBox,
} from './travel';
export { default as boardStyles } from './board.module.css';
