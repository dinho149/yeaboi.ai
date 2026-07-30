/**
 * The drift guard between `types/board.ts` and what the server actually sends.
 *
 * The JSON beside this file is not hand-written. `tests/unit/test_web_wire_shapes.py`
 * drives a real `RetroBoard` and a real `PokerBoard` through a real round, and
 * builds a real slide deck from a real `DeliveryReport`, then writes the results
 * here and fails if what it wrote differs from what is committed. This file is
 * the other half: each fixture `satisfies` the interface the bundle is coded
 * against, so `npm run typecheck` fails when Python stops sending a field the
 * TypeScript promises.
 *
 * The deck is here for the same reason the boards are, with the failure delayed
 * rather than softened: an exported deck is a *file*. A field that quietly stops
 * being written renders as a blank slide on someone's projector months later,
 * with no server, no log and no way to tell it apart from an empty report.
 *
 * ## What it catches
 *
 * A **removed or renamed** field. The fixture no longer has what the interface
 * requires, and `satisfies` fails at build time rather than rendering
 * `undefined` to a teammate on a tunnel.
 *
 * ## What it does not
 *
 * A field the server **adds**. TypeScript excess-property-checks fresh object
 * literals only, and an imported JSON module is not one. That asymmetry is the
 * right way round: adding a field breaks nothing, removing one breaks a board.
 *
 * A **string-literal union narrowing**, either. TypeScript types every string in
 * an imported JSON as `string`, never as its literal, so `phase: "voting"` reads
 * as `string` and cannot satisfy `PokerPhase`. Hence {@link Widened}, which
 * relaxes exactly that and nothing else. The unions lose nothing by it: every
 * one of them is a server-validated tuple that `scripts/gen_web_types.py` emits
 * into `types/enums.ts` with a `--check` in CI, so they already have a guard,
 * and it is a stronger one than this.
 *
 * Three poker fixtures, not one, because the payload is genuinely phase-shaped.
 * `votes[].value` does not exist while the round is open — vote secrecy is
 * enforced by the field's absence, not by blanking it — and `duel` is null until
 * the floor opens. One fixture would pin one phase and leave the others free.
 *
 * These are also the fixtures every board test should build its state from,
 * rather than hand-rolling a snapshot that can quietly describe a server that
 * does not exist. That is how the join-code bug shipped: two tests, each
 * asserting its own invented contract, agreeing with each other and with
 * nothing else.
 */

import type { DeckBoot } from '../../deck/boot';
import type { PokerState, RetroState, TicketView } from '../../types/board';

import deckJson from './deck.json';
import pokerDuelJson from './poker.duel.json';
import pokerRevealedJson from './poker.revealed.json';
import pokerVotingJson from './poker.voting.json';
import retroJson from './retro.json';
import ticketPeekJson from './ticket.peek.json';

/** A full deck: every slide kind, a custom style, and all four palettes. */
export const DECK_WIRE = deckJson as DeckBoot;

/** A retro mid-ceremony: two people, a reaction, a carried item, a running timer. */
export const RETRO_WIRE = retroJson as RetroState;

/** Round open. No values on the wire, no distribution, no median. */
export const POKER_VOTING_WIRE = pokerVotingJson as PokerState;

/** Votes are public, the AI has weighed in, the floor has not opened. */
export const POKER_REVEALED_WIRE = pokerRevealedJson as PokerState;

/** The floor is open, viewed as the low voter (`duel.mine_role === 'low'`). */
export const POKER_DUEL_WIRE = pokerDuelJson as PokerState;

/** What `GET /api/ticket` answers with — display fields only. */
export const TICKET_PEEK_WIRE = ticketPeekJson as TicketView;

/**
 * `T` with every string-literal union relaxed to `string`.
 *
 * Structure — which keys exist, whether each is optional, and whether a value is
 * a string, a number, a null, an array or an object — is preserved exactly, so
 * the removed-field guard is untouched. Only the literal narrowing goes, because
 * a JSON import cannot express one. See the note above on why that costs nothing.
 */
type Widened<T> = T extends string
  ? string
  : T extends number | boolean | null | undefined
    ? T
    : T extends readonly (infer U)[]
      ? Widened<U>[]
      : { [K in keyof T]: Widened<T[K]> };

// The assertions themselves — the lines that fail when the wire moves.
void (retroJson satisfies Widened<RetroState>);
void (pokerVotingJson satisfies Widened<PokerState>);
void (pokerRevealedJson satisfies Widened<PokerState>);
void (pokerDuelJson satisfies Widened<PokerState>);
void (ticketPeekJson satisfies Widened<TicketView>);
void (deckJson satisfies Widened<DeckBoot>);
