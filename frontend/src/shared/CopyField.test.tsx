/**
 * Copying the invite out of the browser.
 *
 * The property worth guarding is not "the button calls the API" — it is that a
 * *refused* copy never claims to have worked. A tunnel board can be opened from
 * a `file://` export, from an insecure context, or after the browser has decided
 * the click that led here was too long ago, and in every one of those the value
 * on screen is still the answer. A button that says "copied" over an empty
 * clipboard is worse than one that says nothing, because it stops the reader
 * reaching for the text that would have worked.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/preact';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { copyText } from '../runtime/clipboard';
import { CopyField } from './CopyField';
import { InviteQR, inviteText } from './InviteQR';

vi.mock('../runtime/clipboard', () => ({ copyText: vi.fn() }));
const mockCopy = vi.mocked(copyText);

beforeEach(() => mockCopy.mockReset());
afterEach(() => vi.restoreAllMocks());

describe('<CopyField>', () => {
  it('shows the value as selectable text, not only as a button', () => {
    render(<CopyField label="Code" value="K3P9-2QXA" />);
    expect(screen.getByText('K3P9-2QXA')).toBeTruthy();
  });

  it('copies the value and confirms on the control itself', async () => {
    mockCopy.mockResolvedValue(true);
    render(<CopyField label="Code" value="K3P9-2QXA" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy Code' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Code copied' })).toBeTruthy());
    expect(mockCopy).toHaveBeenCalledWith('K3P9-2QXA');
  });

  it('says so when the copy was refused', async () => {
    // The whole point: an insecure context or a stale gesture must not read as
    // success, because the fallback is for the reader to select the text.
    mockCopy.mockResolvedValue(false);
    render(<CopyField label="Link" value="https://x.trycloudflare.com/" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy Link' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Could not copy Link' })).toBeTruthy());
    expect(screen.getByText('https://x.trycloudflare.com/')).toBeTruthy();
  });

  it('returns to its label so a second copy is obviously possible', async () => {
    vi.useFakeTimers();
    mockCopy.mockResolvedValue(true);
    render(<CopyField label="Code" value="K3P9-2QXA" />);

    fireEvent.click(screen.getByRole('button', { name: 'Copy Code' }));
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Code copied' })).toBeTruthy());

    vi.advanceTimersByTime(2_000);
    await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Copy Code' })).toBeTruthy());
    vi.useRealTimers();
  });
});

describe('inviteText', () => {
  it('sends the link and the code, in the order they are used', () => {
    expect(inviteText('https://x.trycloudflare.com/', 'K3P9-2QXA')).toBe(
      'https://x.trycloudflare.com/\nAccess code: K3P9-2QXA'
    );
  });

  it('never invents a line for a value it does not have', () => {
    // The invite is copied automatically on open, possibly before the fetch has
    // landed. "Access code: undefined" pasted into a team chat is the failure.
    expect(inviteText('https://x/', '')).toBe('https://x/');
    expect(inviteText('', '')).toBe('');
  });
});

describe('<InviteQR>', () => {
  it('renders both copy fields once the values arrive', () => {
    render(<InviteQR qrSrc="/api/qr?token=t" shareUrl="https://x/" joinCode="K3P9-2QXA" />);
    expect(screen.getByRole('button', { name: 'Copy Link' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Copy Code' })).toBeTruthy();
  });

  it('shows the QR alone until they do', () => {
    // The first frame after the panel opens. A field with an empty value would
    // offer a copy button that puts nothing on the clipboard.
    render(<InviteQR qrSrc="/api/qr?token=t" />);
    expect(screen.queryByRole('button', { name: 'Copy Link' })).toBeNull();
    expect(screen.getByRole('img', { name: /QR code/ })).toBeTruthy();
  });
});
