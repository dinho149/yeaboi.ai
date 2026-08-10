/**
 * Client-side routing.
 *
 * Hand-rolled and about eighty lines, rather than a router dependency, for a
 * constraint the rest of the front end already lives under: these bundles are
 * classic IIFE with no dynamic `import()`, they are inlined whole into the
 * document, and every kilobyte is paid on every copy. A router library brings a
 * module graph and code-splitting assumptions that this build deliberately does
 * not have.
 *
 * The History API rather than a hash: the server serves the shell on every
 * unmatched path already, so real URLs work on a hard refresh and a shared link
 * lands where it says it does.
 */

export interface RouteMatch {
  /** The matched pattern, e.g. '/projects/{id}'. */
  pattern: string;
  params: Record<string, string>;
}

const PARAM = /\{([a-z_][a-z0-9_]*)\}/gi;

function toRegExp(pattern: string): { re: RegExp; keys: string[] } {
  const keys: string[] = [];
  const source = pattern.replace(/[.*+?^${}()|[\]\\]/g, (c) => (c === '{' || c === '}' ? c : `\\${c}`));
  const body = source.replace(PARAM, (_, key: string) => {
    keys.push(key);
    // One segment. `.+` here would let /projects/a/b match /projects/{id}.
    return '([^/]+)';
  });
  return { re: new RegExp(`^${body}$`), keys };
}

export class Routes {
  private compiled: { pattern: string; re: RegExp; keys: string[] }[] = [];

  constructor(patterns: string[]) {
    this.compiled = patterns.map((pattern) => ({ pattern, ...toRegExp(pattern) }));
  }

  match(path: string): RouteMatch | null {
    const clean = path.replace(/\/+$/, '') || '/';
    for (const { pattern, re, keys } of this.compiled) {
      const found = re.exec(clean);
      if (!found) continue;
      const params: Record<string, string> = {};
      keys.forEach((key, index) => {
        // Every key has a group by construction, but the compiler cannot know
        // that; `?? ''` is the honest way to say so without a non-null assert.
        params[key] = decodeURIComponent(found[index + 1] ?? '');
      });
      return { pattern, params };
    }
    return null;
  }
}

/** Push a new path and tell subscribers. `replace` avoids a history entry. */
export function navigate(path: string, { replace = false } = {}): void {
  if (replace) window.history.replaceState({}, '', path);
  else window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

/**
 * Intercept in-app link clicks so an `<a href>` is a real link *and* a
 * client-side navigation. Modified clicks, new tabs, downloads, and anything
 * off-origin are left to the browser — the usual bug here is swallowing a
 * cmd-click and breaking "open in new tab".
 */
export function interceptLinks(onNavigate: () => void): () => void {
  const handler = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element | null)?.closest?.('a');
    if (!anchor) return;
    const href = anchor.getAttribute('href');
    if (!href || href.startsWith('#')) return;
    if (anchor.hasAttribute('download') || anchor.getAttribute('target') === '_blank') return;
    const url = new URL(href, window.location.origin);
    if (url.origin !== window.location.origin) return;
    event.preventDefault();
    navigate(url.pathname + url.search);
    onNavigate();
  };
  document.addEventListener('click', handler);
  return () => document.removeEventListener('click', handler);
}
