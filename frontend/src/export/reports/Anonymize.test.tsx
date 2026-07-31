/**
 * The anonymize export.
 *
 * The link case is the one that matters. `test_export_xss.py` used to prove a
 * `javascript:` URL never reached an `href` by grepping the Python-built page;
 * the document is now data, so that proof lives here — this is the only code
 * that turns a Markdown link into an anchor.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { Anonymize } from './Anonymize';

describe('Anonymize', () => {
  it('renders the masked document, not a string of markup', () => {
    const { container } = render(
      <Anonymize markdown={'# Sprint 42\n\n- shipped auth\n- fixed CI'} warnings={[]} />
    );
    // Document headings start at <h2>: the page's own <h1> is the report title,
    // and a second <h1> inside it breaks the outline a screen reader walks.
    expect(screen.getByRole('heading', { level: 2 }).textContent).toBe('Sprint 42');
    expect(container.querySelectorAll('li')).toHaveLength(2);
  });

  it('never parses the document as HTML', () => {
    const hostile = '<img src=x onerror=alert(1)> and <b>bold</b>';
    const { container } = render(<Anonymize markdown={hostile} warnings={[]} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toContain(hostile);
  });

  it('drops a javascript: link to plain text, keeping the label', () => {
    const { container } = render(
      <Anonymize markdown="See [the ticket](javascript:alert1) for details." warnings={[]} />
    );
    expect(container.querySelector('a')).toBeNull();
    // The reader still gets the words. Dropping the whole run would silently
    // delete content from a document whose entire purpose is to be readable.
    expect(container.textContent).toContain('the ticket');
  });

  it('keeps a tracker link live', () => {
    const { container } = render(<Anonymize markdown="[PROJ-1](https://jira.test/PROJ-1)" warnings={[]} />);
    const link = container.querySelector('a');
    expect(link?.getAttribute('href')).toBe('https://jira.test/PROJ-1');
    expect(link?.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('renders a table as a table', () => {
    const { container } = render(<Anonymize markdown={'| A | B |\n|---|---|\n| 1 | 2 |'} warnings={[]} />);
    expect(container.querySelectorAll('th')).toHaveLength(2);
    expect(container.querySelectorAll('tbody td')).toHaveLength(2);
  });

  it('shows notices only when there are some', () => {
    const { container, rerender } = render(<Anonymize markdown="body" warnings={[]} />);
    expect(container.textContent).not.toContain('Notices');
    rerender(<Anonymize markdown="body" warnings={['masked 3 names']} />);
    expect(screen.getByText('masked 3 names')).toBeTruthy();
  });
});
