// Home — the desktop's welcome screen: the same card inventory the TUI's
// landing split renders (served verbatim from /api/meta/capabilities so it can
// never drift from _MODE_CARDS), plus the rotating tips ticker.

import { Lozenge } from '@design/primitives/Lozenge';
import { useEffect, useState } from 'react';
import { apiGet } from '../api';

interface ModeCard {
  key: string;
  title: string;
  description: string;
  available: boolean;
  color: string;
}

interface CategoryCard {
  key: string;
  title: string;
  verb: string;
  capabilities: string[];
  color: string;
}

interface Capabilities {
  categories: CategoryCard[];
  modes: ModeCard[];
  agents: ModeCard[];
}

interface Tip {
  key: string;
  text: string;
  mode_key: string | null;
  is_new: boolean;
  is_beta: boolean;
}

const TIP_ROTATE_MS = 6_000;

function CardGrid({ cards }: { cards: ModeCard[] }) {
  return (
    <div class="card-grid">
      {cards.map((card) => (
        <article key={card.key} class="mode-card">
          <h3>
            <span class="accent-dot" style={{ background: card.color, width: 8, height: 8, borderRadius: 99 }} />
            {card.title}
          </h3>
          <p>{card.description}</p>
        </article>
      ))}
    </div>
  );
}

export function Home() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [tips, setTips] = useState<Tip[]>([]);
  const [tipIndex, setTipIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<Capabilities>('/api/meta/capabilities').then(setCaps, (e: Error) => setError(e.message));
    apiGet<{ tips: Tip[] }>('/api/meta/tips').then(({ tips: loaded }) => setTips(loaded), () => undefined);
  }, []);

  useEffect(() => {
    if (tips.length === 0) return;
    const timer = setInterval(() => setTipIndex((i) => (i + 1) % tips.length), TIP_ROTATE_MS);
    return () => clearInterval(timer);
  }, [tips]);

  if (error) return <p>Could not load the mode inventory: {error}</p>;
  if (!caps) return <p>Loading…</p>;

  const humans = caps.categories.find((c) => c.key === 'humans');
  const agents = caps.categories.find((c) => c.key === 'agents');
  const tip = tips[tipIndex];

  return (
    <div>
      <h1 class="page-title">Home</h1>
      <div class="category-label" style={{ color: humans?.color }}>
        Humans — {humans?.verb}
      </div>
      <CardGrid cards={caps.modes} />
      <div class="category-label" style={{ color: agents?.color }}>
        Agents — {agents?.verb}
      </div>
      <CardGrid cards={caps.agents} />
      {tip && (
        <div class="tip-ticker" title="rotating tips — the same rotation as the TUI welcome screen">
          <span>{tip.text}</span>
          {tip.is_beta ? <Lozenge category="inprogress">beta</Lozenge> : null}
          {!tip.is_beta && tip.is_new ? <Lozenge category="done">new</Lozenge> : null}
        </div>
      )}
    </div>
  );
}
