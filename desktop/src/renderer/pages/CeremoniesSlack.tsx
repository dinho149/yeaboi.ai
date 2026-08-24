// Ceremonies · Slack — the inbound half of the clock.
//
// A team reacting or replying in Slack, read back on a schedule and applied to
// the run the post was about. Linking is offered here because this is a machine
// its owner is sitting at: the binding decides whose name goes on somebody
// else's report, which is the one thing Slack's servers did not attest.

import { Card, DataTable, NoticeBlock, StatGrid, StatTile } from '@design/primitives';
import { Duck } from '@design/primitives/Duck';
import { useEffect, useState } from 'react';
import { type SlackPage, linkSlackMember, loadSlack, pollSlack } from '../ops';

export function CeremoniesSlack() {
  const [page, setPage] = useState<SlackPage | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [slackUser, setSlackUser] = useState('');
  const [member, setMember] = useState('');
  const [busy, setBusy] = useState(false);

  function refresh() {
    return loadSlack().then(setPage, (e: Error) => setError(e.message));
  }

  useEffect(() => {
    void refresh();
  }, []);

  if (error && !page) return <NoticeBlock title="Could not read the Slack lane" items={[error]} />;
  if (!page) return <p>Loading…</p>;

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await work();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div class="dash">
      <header class="dash-head">
        <div>
          <h1 class="page-title">Ceremonies · Slack</h1>
          <p class="dash-sub">Reactions and replies, read back and applied to the run they answered.</p>
        </div>
        <div class="dash-actions">
          <a class="button" href="#/ceremonies">
            Back to the schedule
          </a>
        </div>
      </header>

      {error && <NoticeBlock title="That did not work" items={[error]} />}
      {notice && <NoticeBlock title="Poll" items={[notice]} />}

      {!page.two_way ? (
        <Card title="Write-only">
          <p>
            <Duck state="idle" size={28} /> {page.empty_message}
          </p>
          <p class="dash-note">{page.why}</p>
        </Card>
      ) : (
        <>
          <Card title="The lane">
            <StatGrid>
              <StatTile label="Linked people" value={String(page.linked)} />
              <StatTile
                label="Polls every"
                value={page.interval_min ? `${page.interval_min} min` : 'not installed'}
              />
            </StatGrid>
            <div class="dash-actions">
              <button
                type="button"
                class="primary"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    const result = await pollSlack();
                    // A poll that declines is not a failure: it read a fixed
                    // window, found nothing it was allowed to act on, and said so.
                    setNotice(
                      result.declined
                        ? `Declined: ${result.outcome}`
                        : `${result.events_applied} applied of ${result.events_seen} seen.`,
                    );
                  })
                }
              >
                Poll now
              </button>
            </div>
          </Card>

          <Card title="Who is who">
            <p class="dash-note">{page.link_hint}</p>
            <div class="field-row">
              <label for="slack-user">Slack id</label>
              <input
                id="slack-user"
                type="text"
                value={slackUser}
                placeholder="U0123456789"
                onInput={(e) => setSlackUser((e.target as HTMLInputElement).value)}
              />
            </div>
            <div class="field-row">
              <label for="slack-member">Roster name</label>
              <input
                id="slack-member"
                type="text"
                value={member}
                onInput={(e) => setMember((e.target as HTMLInputElement).value)}
              />
            </div>
            <div class="dash-actions">
              <button
                type="button"
                disabled={busy || !slackUser || !member}
                onClick={() =>
                  void act(async () => {
                    await linkSlackMember(slackUser, member);
                    setSlackUser('');
                    setMember('');
                  })
                }
              >
                Link
              </button>
            </div>
            <DataTable
              rows={page.identities}
              rowKey={(row) => row.slack_user}
              empty="Nobody linked yet."
              columns={[
                { key: 'slack_user', header: 'Slack id', cell: (row) => row.slack_user },
                { key: 'member', header: 'Roster name', cell: (row) => row.member },
                {
                  key: 'unlink',
                  header: '',
                  cell: (row) => (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void act(() => linkSlackMember(row.slack_user, '', true))}
                    >
                      Unlink
                    </button>
                  ),
                },
              ]}
            />
          </Card>

          <Card title="What Slack asked for">
            <p class="dash-note">
              Every event the lane considered, including the refused ones — "you are not on the list", "I
              could not tell what you meant" and "the write said no" are different problems.
            </p>
            <DataTable
              rows={page.events}
              empty="Nothing inbound yet."
              columns={[
                { key: 'at', header: 'When', cell: (row) => String(row['created_at'] ?? '') },
                { key: 'who', header: 'Who', cell: (row) => String(row['slack_user'] ?? '') },
                { key: 'verb', header: 'Asked for', cell: (row) => String(row['verb'] ?? '') },
                { key: 'outcome', header: 'Outcome', cell: (row) => String(row['outcome'] ?? '') },
                { key: 'detail', header: 'Detail', cell: (row) => String(row['detail'] ?? '') },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  );
}
