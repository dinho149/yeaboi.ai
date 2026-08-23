// Standup schedule — the terminal's six-step wizard as one form.
//
// Saving installs (or removes) the OS jobs: standup/schedule.py does both, so
// the two surfaces can never leave a machine with a config that says "10:00"
// and a launchd job that says something else.

import { Card, NoticeBlock } from '@design/primitives';
import { useEffect, useState } from 'react';
import { type ScheduleView, loadSchedule, loadStandup, saveSchedule, weekdaySpec } from '../dashboards';

const TIME_PRESETS = ['09:00', '09:30', '10:00', '10:30', '11:00'];
const LEAD_PRESETS = [5, 10, 15, 30];
const REMINDERS: [number, string][] = [
  [0, 'No reminder'],
  [30, '30 minutes after'],
  [60, '1 hour after'],
  [120, '2 hours after'],
];
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const CHANNEL_HINTS: Record<string, string> = {
  terminal: 'print in the terminal the run opens',
  desktop: 'system notification',
  slack: 'post to Slack (needs SLACK_WEBHOOK_URL)',
  email: 'send via SMTP (needs STANDUP_SMTP_* settings)',
};

/** "1-5" / "1,3,5" → the weekday numbers it names. */
function parseWeekdays(spec: string): number[] {
  const days = new Set<number>();
  for (const part of spec.split(',')) {
    const bounds = part.split('-').map((n) => Number.parseInt(n, 10));
    const from = bounds[0];
    if (from === undefined || Number.isNaN(from)) continue;
    const to = bounds[1];
    const last = to === undefined || Number.isNaN(to) ? from : to;
    for (let day = from; day <= last; day += 1) days.add(day);
  }
  return [...days].sort();
}

export function StandupSchedule() {
  const [view, setView] = useState<ScheduleView | null>(null);
  const [days, setDays] = useState<number[]>([1, 2, 3, 4, 5]);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    loadStandup()
      .then((dash) => {
        if (!dash.session_id) throw new Error('No project yet — plan one first.');
        return loadSchedule(dash.session_id);
      })
      .then(
        (saved) => {
          setView(saved);
          setDays(parseWeekdays(saved.weekdays));
        },
        (e: Error) => setError(e.message),
      );
  }, []);

  async function save() {
    if (!view || busy) return;
    setBusy(true);
    setError('');
    try {
      const result = await saveSchedule({ ...view, weekdays: weekdaySpec(days) });
      setMessage(result.message);
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  if (error && !view) return <NoticeBlock title="No schedule to set up" items={[error]} />;
  if (!view) return <p>Loading…</p>;

  const set = (patch: Partial<ScheduleView>) => setView({ ...view, ...patch });

  return (
    <div class="dash">
      <h1 class="page-title">Standup schedule</h1>
      <p class="dash-sub">
        The job runs {view.lead_minutes} minutes before the standup, so the summary is waiting when the meeting starts.
      </p>

      <Card title="When">
        <div class="field-row">
          <label for="standup-time">Standup at</label>
          <select id="standup-time" value={view.time} onChange={(e) => set({ time: (e.target as HTMLSelectElement).value })}>
            {[...new Set([...TIME_PRESETS, view.time])].sort().map((time) => (
              <option key={time} value={time}>
                {time}
              </option>
            ))}
          </select>
        </div>
        <div class="field-row">
          <label for="standup-lead">Deliver</label>
          <select
            id="standup-lead"
            value={String(view.lead_minutes)}
            onChange={(e) => set({ lead_minutes: Number((e.target as HTMLSelectElement).value) })}
          >
            {[...new Set([...LEAD_PRESETS, view.lead_minutes])]
              .sort((a, b) => a - b)
              .map((lead) => (
                <option key={lead} value={String(lead)}>
                  {lead} minutes before
                </option>
              ))}
          </select>
        </div>
        <div class="day-picker">
          {DAYS.map((label, index) => {
            const day = index + 1;
            return (
              <button
                key={label}
                type="button"
                class={days.includes(day) ? 'day active' : 'day'}
                onClick={() => setDays(days.includes(day) ? days.filter((d) => d !== day) : [...days, day])}
              >
                {label}
              </button>
            );
          })}
        </div>
      </Card>

      <Card title="Where it goes">
        {view.valid_channels.map((channel) => (
          <label key={channel} class="check-row">
            <input
              type="checkbox"
              checked={view.delivery_channels.includes(channel)}
              onChange={() =>
                set({
                  delivery_channels: view.delivery_channels.includes(channel)
                    ? view.delivery_channels.filter((c) => c !== channel)
                    : [...view.delivery_channels, channel],
                })
              }
            />
            <span>
              <strong>{channel}</strong> — {CHANNEL_HINTS[channel] ?? ''}
            </span>
          </label>
        ))}
      </Card>

      <Card title="Afterwards">
        <div class="field-row">
          <label for="standup-remind">Remind me to drop the transcript</label>
          <select
            id="standup-remind"
            value={String(view.remind_after)}
            onChange={(e) => set({ remind_after: Number((e.target as HTMLSelectElement).value) })}
          >
            {REMINDERS.map(([minutes, label]) => (
              <option key={minutes} value={String(minutes)}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <p class="dash-note">
          A reminder only fires alongside a scheduled standup — switching the schedule off removes both jobs.
        </p>
      </Card>

      <label class="check-row">
        <input type="checkbox" checked={view.enabled} onChange={() => set({ enabled: !view.enabled })} />
        <span>
          <strong>Run this standup on a schedule</strong>
        </span>
      </label>

      {error && <NoticeBlock title="Could not save" items={[error]} />}
      {message && <p class="dash-note">{message}</p>}

      <div class="dash-actions">
        <button type="button" class="primary" disabled={busy} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save schedule'}
        </button>
        <a href="#/humans/standup">Back to the standup</a>
      </div>
    </div>
  );
}
