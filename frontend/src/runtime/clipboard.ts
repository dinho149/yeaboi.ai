/**
 * Copy text to the clipboard, on every surface these bundles ship to.
 *
 * ## Why there are two paths
 *
 * `navigator.clipboard` is the whole API — except that it is gated on a *secure
 * context*, and one of the five bundles is opened from disk. A static export is
 * a `file://` document, which browsers treat as opaque and not secure, so
 * `navigator.clipboard` is either missing or rejects there. `document.execCommand`
 * is deprecated and still the only thing that works in that case.
 *
 * The fallback is not eval and reaches no network, so it survives the tunnel CSP
 * (`script-src 'unsafe-inline'`, no `'unsafe-eval'`, `default-src 'none'`) and
 * the `test_sources_do_not_eval` guard unchanged.
 *
 * ## Why it returns a boolean rather than throwing
 *
 * Every caller has the same fallback — show the value and let the reader select
 * it — and none of them can do anything else useful. A rejected write is normal,
 * not exceptional: it is what happens when a browser decides the click that led
 * here was too long ago.
 */

/**
 * Write `text` to the clipboard. Resolves `true` if it landed.
 *
 * Must be called from a user gesture, or from within the activation window one
 * opened. Firefox and Safari refuse otherwise, and refusal is reported here as
 * `false` rather than as an error.
 */
export async function copyText(text: string): Promise<boolean> {
  if (!text) return false;

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Denied, or no secure context. Fall through — execCommand may still work,
    // and on a file:// export it is the only thing that will.
  }

  return legacyCopy(text);
}

/**
 * `document.execCommand('copy')` against an offscreen textarea.
 *
 * The element has to be in the document and selectable for the command to have
 * anything to act on, which is why it is not simply `display: none`. It is
 * positioned off-canvas instead, and `readOnly` keeps the mobile keyboard from
 * appearing for the frame it exists.
 */
function legacyCopy(text: string): boolean {
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.setAttribute('aria-hidden', 'true');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);

    area.select();
    area.setSelectionRange(0, text.length);
    const ok = document.execCommand('copy');

    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}
