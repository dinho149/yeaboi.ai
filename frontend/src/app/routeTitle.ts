/**
 * What page is this, in words?
 *
 * A pure function because it is the thing three separate mechanisms have to
 * agree on — the document title, the live-region announcement, and (later) any
 * breadcrumb. Deriving it three times is how they drift apart.
 */

export const APP_NAME = 'yeaboi';

/** The page's own name, without the product name. */
export function pageName(pattern: string | null, path: string): string {
  switch (pattern) {
    case '/':
    case '/projects':
      return 'Projects';
    case '/projects/{id}':
      return 'Project';
    case '/projects/{id}/artifacts/{artifactId}':
      return 'Document';
    case '/settings':
      return 'Settings';
    default:
      // A path that matched nothing still needs a name, and saying which path
      // is more useful than "Not found" on its own.
      return `Not found: ${path}`;
  }
}

/** The full `<title>`. Page first: a tab strip truncates from the right. */
export function documentTitle(pattern: string | null, path: string): string {
  return `${pageName(pattern, path)} · ${APP_NAME}`;
}
