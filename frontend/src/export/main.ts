/**
 * Entry for every *static* exported page (planning, standup, retro, poker,
 * performance, reporting, roadmap, analysis, anonymize, team profile).
 *
 * These pages have no interactivity beyond the theme toggle, so this bundle is
 * deliberately tiny and framework-free — it exists to prove the whole build
 * pipeline (Vite → committed IIFE → Python `read_asset` → inlined into one
 * self-contained document) against the ten exporters' existing test suites
 * before any React lands.
 */
import '../design/export.css';
import { installThemeSwitcher } from '../runtime/theme';

installThemeSwitcher();
