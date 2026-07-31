/**
 * Scheme gating.
 *
 * The case that matters: `test_export_xss.py` never caught `javascript:` in an
 * href because its probe is markup-shaped, not scheme-shaped, and HTML escaping
 * touches none of the characters in `javascript:alert(1)`. React does not save
 * you either — it warns in development and renders the link in production, and
 * production is the only build that ships.
 */

import { describe, expect, it } from 'vitest';

import { safeImageSrc, safeUrl } from './url';

describe('safeUrl', () => {
  it.each(['https://jira.example/browse/AB-1', 'http://localhost:8080/x', 'mailto:someone@example.com'])(
    'allows %s',
    (url) => {
      expect(safeUrl(url)).toBe(url);
    }
  );

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox(1)',
    'file:///etc/passwd',
  ])('rejects %s', (url) => {
    expect(safeUrl(url)).toBe('');
  });

  it('rejects a scheme smuggled past the check with control characters', () => {
    // The URL parser strips TAB/LF/CR from anywhere in a URL *before* reading
    // the scheme, so this reaches the browser as `javascript:alert(1)`.
    expect(safeUrl('java\tscript:alert(1)')).toBe('');
    expect(safeUrl('java\nscript:alert(1)')).toBe('');
    expect(safeUrl('  \r\njavascript:alert(1)  ')).toBe('');
  });

  it('keeps interior spaces, which cannot smuggle a scheme', () => {
    // Browsers keep them too, so stripping here would diverge from reality —
    // and `java script:` is not a scheme in the first place.
    expect(safeUrl('java script:alert(1)')).toBe('java script:alert(1)');
  });

  it('allows relative references, which cannot execute', () => {
    expect(safeUrl('/browse/AB-1')).toBe('/browse/AB-1');
    expect(safeUrl('example.com/browse/AB-1')).toBe('example.com/browse/AB-1');
    expect(safeUrl('#anchor')).toBe('#anchor');
  });

  it('rejects protocol-relative URLs', () => {
    // Under file:// these resolve to a bogus origin, and no exporter means them.
    expect(safeUrl('//evil.example/x')).toBe('');
  });

  it.each([null, undefined, '', '   '])('returns empty for %s', (value) => {
    expect(safeUrl(value)).toBe('');
  });
});

describe('safeImageSrc', () => {
  it('allows a base64 data image', () => {
    const src = 'data:image/png;base64,iVBORw0KGgo=';
    expect(safeImageSrc(src)).toBe(src);
  });

  it('allows a same-origin path — how the invite QR is actually loaded', () => {
    expect(safeImageSrc('/api/qr?token=abc')).toBe('/api/qr?token=abc');
  });

  it('rejects a data: URI that is not an image', () => {
    // The reason this is a separate function from safeUrl: `data:` is safe in
    // an <img src> and emphatically not in an href.
    expect(safeImageSrc('data:text/html;base64,PHNjcmlwdD4=')).toBe('');
    expect(safeImageSrc('data:image/svg+xml,<svg onload=alert(1)>')).toBe('');
  });

  it('rejects a javascript: src', () => {
    expect(safeImageSrc('javascript:alert(1)')).toBe('');
  });
});
