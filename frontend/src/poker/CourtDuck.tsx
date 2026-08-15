/** The court duck for a card face. Renders nothing for an ordinary pip. */

import { COURT_BY_VALUE, SPRITE_H, SPRITE_W, spritePaths } from './duckDeck';
import styles from './poker.module.css';

export interface CourtDuckProps {
  value: string;
}

export function CourtDuck({ value }: CourtDuckProps) {
  const persona = COURT_BY_VALUE.get(value);
  if (!persona) return null;

  return (
    <svg
      className={styles['court']}
      viewBox={`0 0 ${SPRITE_W} ${SPRITE_H}`}
      shapeRendering="crispEdges"
      aria-hidden="true"
      focusable="false"
    >
      {spritePaths(persona).map((layer) => (
        <path key={layer.fill} fill={layer.fill} d={layer.d} />
      ))}
    </svg>
  );
}

export function courtName(value: string): string | undefined {
  return COURT_BY_VALUE.get(value)?.name;
}
