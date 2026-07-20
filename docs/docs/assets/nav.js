// Single source of truth for the docs page tree. Sidebar, active-page
// highlighting, and the title search all read from this array — adding a
// docs page means adding one entry here, not editing every page.
const NAV = [
  { title: "Docs Home", path: "/docs/docs/index.html" },
  { title: "Getting Started", path: "/docs/docs/getting-started.html" },
  { title: "CLI Reference", path: "/docs/docs/cli-reference.html" },
  {
    title: "Modes",
    path: "/docs/docs/modes/index.html",
    children: [
      { title: "Planning", path: "/docs/docs/modes/planning.html" },
      { title: "Daily Standup", path: "/docs/docs/modes/standup.html" },
      { title: "Retro", path: "/docs/docs/modes/retro.html" },
      { title: "Performance", path: "/docs/docs/modes/performance.html" },
      { title: "Reporting", path: "/docs/docs/modes/reporting.html" },
      { title: "Team Analysis", path: "/docs/docs/modes/team-analysis.html" },
    ],
  },
  { title: "Integrations & Exports", path: "/docs/docs/integrations-exports.html" },
  { title: "Session Management", path: "/docs/docs/session-management.html" },
  { title: "Tools", path: "/docs/docs/tools.html" },
  { title: "Architecture & Concepts", path: "/docs/docs/architecture.html" },
  { title: "Scrum Standards", path: "/docs/docs/scrum-standards.html" },
  { title: "Deployment", path: "/docs/docs/deployment.html" },
  { title: "Development", path: "/docs/docs/development.html" },
];

function _flatNav() {
  const out = [];
  NAV.forEach((entry) => {
    out.push(entry);
    (entry.children || []).forEach((c) => out.push(c));
  });
  return out;
}

function _renderTree(entries, currentPath) {
  const ul = document.createElement("ul");
  ul.className = "docs-tree";
  entries.forEach((entry) => {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = entry.path;
    a.textContent = entry.title;
    if (entry.path === currentPath) a.classList.add("current");
    li.appendChild(a);
    if (entry.children && entry.children.length) {
      li.appendChild(_renderTree(entry.children, currentPath));
    }
    ul.appendChild(li);
  });
  return ul;
}

function _renderSidebar(currentPath) {
  const root = document.getElementById("docs-sidebar");
  if (!root) return;
  root.innerHTML = "";

  const search = document.createElement("input");
  search.type = "search";
  search.id = "docs-search";
  search.placeholder = "Search docs…";
  search.setAttribute("aria-label", "Search docs");
  root.appendChild(search);

  const treeWrap = document.createElement("div");
  treeWrap.id = "docs-tree-wrap";
  treeWrap.appendChild(_renderTree(NAV, currentPath));
  root.appendChild(treeWrap);

  search.addEventListener("input", () => {
    const q = search.value.trim().toLowerCase();
    if (!q) {
      treeWrap.innerHTML = "";
      treeWrap.appendChild(_renderTree(NAV, currentPath));
      return;
    }
    const matches = _flatNav().filter((e) => e.title.toLowerCase().includes(q));
    treeWrap.innerHTML = "";
    treeWrap.appendChild(_renderTree(matches, currentPath));
  });
}

function _renderTOC() {
  const tocRoot = document.getElementById("docs-toc");
  const article = document.querySelector("article");
  if (!tocRoot || !article) return;
  const headings = article.querySelectorAll("h2[id], h3[id]");
  if (!headings.length) {
    tocRoot.remove();
    return;
  }
  const ul = document.createElement("ul");
  headings.forEach((h) => {
    const li = document.createElement("li");
    li.className = h.tagName === "H3" ? "toc-sub" : "";
    const a = document.createElement("a");
    a.href = "#" + h.id;
    a.textContent = h.textContent;
    li.appendChild(a);
    ul.appendChild(li);
  });
  const label = document.createElement("p");
  label.className = "toc-label";
  label.textContent = "On this page";
  tocRoot.appendChild(label);
  tocRoot.appendChild(ul);
}

function renderDocsShell() {
  const currentPath = window.location.pathname;
  _renderSidebar(currentPath);
  _renderTOC();
}

document.addEventListener("DOMContentLoaded", renderDocsShell);
