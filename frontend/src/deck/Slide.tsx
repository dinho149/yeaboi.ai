/**
 * One slide.
 *
 * Six kinds, all sharing a head (the section mark and eyebrow, or a large
 * centred mark on the two bookend slides) and differing only in the body.
 *
 * Everything that came out of a tracker or an LLM — theme titles, bullets,
 * metric labels, the headline — is a React text child. There is no path from
 * the payload to `innerHTML`, so a ticket titled `<img onerror=…>` renders as
 * those literal characters and nothing else.
 */

import { Eyebrow, Wordmark } from '../design/primitives';
import { cx } from '../runtime/cx';
import type { DeckSlide } from './boot';
import styles from './deck.module.css';

/**
 * Longest thank-you title that still fits the block-glyph face.
 *
 * Each character is three mono cells wide, so a long string overflows a
 * projected slide rather than wrapping — the glyphs are two rows of a grid and
 * a line break through the middle of them is unreadable. Python hardcodes
 * "Thank you" (9), so this is a guard against a *future* exporter writing a
 * deck this bundle then has to open, not against anything shipping today.
 */
const WORDMARK_MAX = 14;

function pageLabel(page: [number, number] | undefined): string | undefined {
  return page && page[1] > 1 ? `${page[0]}/${page[1]}` : undefined;
}

/**
 * The thank-you heading, set in the block-glyph face when it fits.
 *
 * Empty renders nothing rather than an empty `<pre role="img">`: a graphic with
 * no accessible name is announced as an unlabelled image, which is worse than
 * the missing heading it would be standing in for.
 */
function ThanksTitle({ title }: { title: string }) {
  if (!title) return null;
  return title.length <= WORDMARK_MAX ? (
    <Wordmark text={title} className={styles['thanksMark']} />
  ) : (
    <h1 className={styles['h1']}>{title}</h1>
  );
}

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className={styles['items']}>
      {items.map((item, i) => (
        <li key={`${i}-${item}`}>{item}</li>
      ))}
    </ul>
  );
}

export interface SlideProps {
  slide: DeckSlide;
  /**
   * "Last month (~2 sprints) · 2026-06-15 to 2026-07-13 · Sprint 11, Sprint 12".
   *
   * A deck-level fact rather than a slide field — it belongs to the report, not
   * to any one slide — but the title slide is where a stakeholder reads it, and
   * a delivery report that never says which weeks it covers is not one.
   */
  period?: string;
}

export function Slide({ slide, period }: SlideProps) {
  const bookend = slide.type === 'title' || slide.type === 'thanks';
  const title = slide.title ?? '';

  const head = bookend ? (
    slide.emoji ? (
      <div className={styles['markLarge']} aria-hidden="true">
        {slide.emoji}
      </div>
    ) : null
  ) : (
    <div className={styles['head']}>
      {slide.emoji ? (
        <span className={styles['emoji']} aria-hidden="true">
          {slide.emoji}
        </span>
      ) : null}
      {/* Python guarantees the section is never the slide's own title, so this
          never restates the heading in smaller type. */}
      {slide.section ? (
        <Eyebrow accent value={pageLabel(slide.page)}>
          {slide.section}
        </Eyebrow>
      ) : null}
    </div>
  );

  return (
    <article className={cx(styles['slide'], bookend && styles['centered'])}>
      {head}
      {slide.type === 'title' ? (
        <>
          <h1 className={styles['h1']}>{title || 'Delivery Report'}</h1>
          {period ? <p className={styles['sub']}>{period}</p> : null}
          {slide.headline ? <p className={styles['headline']}>{slide.headline}</p> : null}
        </>
      ) : slide.type === 'thanks' ? (
        <>
          <ThanksTitle title={title} />
          {slide.subtitle ? <p className={styles['sub']}>{slide.subtitle}</p> : null}
        </>
      ) : (
        <>
          <h2 className={styles['h2']}>{title}</h2>
          {slide.type === 'summary' ? (
            <div className={styles['body']}>
              {(slide.points ?? (slide.body ? [slide.body] : [])).map((point, i) => (
                <p key={`${i}-${point.slice(0, 24)}`}>{point}</p>
              ))}
            </div>
          ) : null}
          {slide.type === 'metrics' ? (
            <>
              <div className={styles['metrics']}>
                {(slide.metrics ?? []).map(([label, value]) => (
                  <div className={styles['metric']} key={label}>
                    <div className={styles['metricValue']}>{value}</div>
                    <div className={styles['metricLabel']}>{label}</div>
                  </div>
                ))}
              </div>
              {slide.footnote ? <p className={styles['footnote']}>{slide.footnote}</p> : null}
            </>
          ) : null}
          {slide.type === 'cards' ? (
            <div className={cx(styles['cards'], slide.wide && styles['cardsWide'])}>
              {(slide.cards ?? []).map(([cardTitle, bullets]) => (
                <div className={styles['card']} key={cardTitle}>
                  <h3 className={styles['cardTitle']}>{cardTitle}</h3>
                  <Bullets items={bullets} />
                </div>
              ))}
            </div>
          ) : null}
          {slide.type === 'list' ? <Bullets items={slide.items ?? []} /> : null}
        </>
      )}
    </article>
  );
}
