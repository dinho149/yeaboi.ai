/**
 * The coverage dot. What is worth testing is the promise the component makes:
 * the word is always there, and an unknown state still renders.
 */

import { render, screen } from '@testing-library/preact';
import { describe, expect, it } from 'vitest';

import { CoverageDots, coverageTone } from './Coverage';

describe('coverageTone', () => {
  it('maps the four known states', () => {
    expect(coverageTone('covered')).toBe('ok');
    expect(coverageTone('partial')).toBe('warn');
    expect(coverageTone('failed')).toBe('danger');
    expect(coverageTone('not_configured')).toBe('low');
  });

  it('degrades an unknown state rather than failing', () => {
    // The engine produces these words; a new one must render muted, not crash.
    expect(coverageTone('sampled')).toBe('low');
  });
});

describe('CoverageDots', () => {
  it('renders nothing when there is no coverage to report', () => {
    const { container } = render(<CoverageDots items={[]} />);
    expect(container.textContent).toBe('');
  });

  it('always writes the status word beside the dot', () => {
    // Colour alone is not a signal every reader can receive.
    const { container } = render(<CoverageDots items={[{ label: 'retro', status: 'partial' }]} />);
    expect(container.textContent).toContain('retro');
    expect(container.textContent).toContain('partial');
    expect(container.querySelectorAll('i')).toHaveLength(1);
  });

  it('spells an underscored state as words', () => {
    const { container } = render(<CoverageDots items={[{ label: 'poker', status: 'not_configured' }]} />);
    expect(container.textContent).toContain('not configured');
  });

  it('renders an unknown state with its own word intact', () => {
    const { container } = render(<CoverageDots items={[{ label: 'code', status: 'sampled' }]} />);
    expect(container.textContent).toContain('sampled');
  });

  it('carries the honest sentence as the dot title when there is one', () => {
    render(<CoverageDots items={[{ label: 'standup', status: 'partial', detail: '3 runs, none named them.' }]} />);
    expect(screen.getByTitle('3 runs, none named them.')).toBeTruthy();
  });

  it('marks the dot itself decorative', () => {
    const { container } = render(<CoverageDots items={[{ label: 'jira', status: 'covered' }]} />);
    expect(container.querySelector('i')?.getAttribute('aria-hidden')).toBe('true');
  });
});
