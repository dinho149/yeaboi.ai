/**
 * Surfaces: a bordered panel, and a titled page region.
 *
 * Ports of `html_theme`'s `.card` markup and `html_theme.section`.
 */

import type { ReactNode } from 'react';

import { cx } from '../../runtime/cx';
import styles from './primitives.module.css';

export interface CardProps {
  children: ReactNode;
  title?: ReactNode;
  /** Rendered on the right of the title row — chips, a menu, a count. */
  actions?: ReactNode;
  /** Highlight the border on hover. Off by default: only do it if it is clickable. */
  interactive?: boolean;
  className?: string | undefined;
}

export function Card({ children, title, actions, interactive, className }: CardProps) {
  return (
    <div className={cx(styles['card'], interactive && styles['cardHover'], className)}>
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
