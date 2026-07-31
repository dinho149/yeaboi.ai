/**
 * Surfaces: a bordered panel, and a titled page region.
 *
 * Ports of `html_theme`'s `.card` markup and `html_theme.section`.
 */

import type { CSSProperties, ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface CardProps {
  children: ReactNode;
  title?: ReactNode;
  /** Rendered on the right of the title row — chips, a menu, a count. */
  actions?: ReactNode;
  /** Highlight the border on hover. Off by default: only do it if it is clickable. */
  interactive?: boolean;
  /**
   * For passing a custom property the stylesheet reads, e.g. a per-card tone.
   *
   * Deliberately not a general escape hatch: the plan export colours a story
   * card by its priority, and the alternative was four hand-written CSS rules
   * duplicating a vocabulary that already exists as a typed `Record`. Reach for
   * `className` for anything a stylesheet can express on its own.
   */
  style?: CSSProperties | undefined;
  className?: string | undefined;
}

export function Card({ children, title, actions, interactive, style, className }: CardProps) {
  return (
    <div className={cx(styles['card'], interactive && styles['cardHover'], className)} style={style}>
      {title || actions ? (
        <div className={styles['cardHeader']}>
          <div className={styles['cardTitle']}>{title}</div>
          {actions}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export interface SectionProps {
  id?: string;
  title: string;
  children: ReactNode;
  className?: string | undefined;
}

/**
 * A titled region with a real `<section>` + `<h2>`.
 *
 * The heading is a heading, not a styled div, so the document has a navigable
 * outline — screen reader users jump between sections by heading, which is the
 * primary way a long report gets read at all.
 */
export function Section({ id, title, children, className }: SectionProps) {
  return (
    <section id={id} className={cx(styles['section'], className)}>
      <h2 className={styles['sectionTitle']}>{title}</h2>
      {children}
    </section>
  );
}
