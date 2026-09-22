# Version 4.3.2 — lost broker requests, skipped shorts, submission budget, immediate-limit buffer, logs

Audit of September 21, 2026 (60-day replays of both engines plus a code review of every entry and exit branch) found four execution defects that could leave money unmanaged or block valid entries. None changes a strategy rule, a purchase amount, a market selection or a saved permission. No database migration is required.

## Lost broker requests no longer strand a position

A `POST /v2/orders` that fails in transit (connection reset, timeout on send, gateway error) leaves an operation durably `attempted` with no broker order. Both executors then looked the order up by client identifier every tick, received 404 forever, and waited. A filled QQQ or crypto position whose **stop** request was lost therefore had no stop and no exit indefinitely, including through the closing window; a lost **exit** request blocked every later sale; a lost **entry** request blocked the engine (Socrates) or the market (crypto) permanently. Reproduced offline with fake brokers that drop the request without booking it.

Each executor now records the first 404 (`not_found_since`) and, after a **60-second confirmation window**, checks the broker's open orders and positions once more. If no order with that identifier exists, no unknown order exists for the instrument, and nothing reserves the position (crypto `qty_available` equals `qty`; no unexplained QQQ exposure for an entry), the operation is recorded as terminal `expired` with evidence `not_found_at_broker`, an `order_not_found` / `crypto_order_not_found` event is written, and management continues through the existing paths: an entry finishes without a fill (the event and the session-entry allowance stay consumed, Live stays on because nothing was rejected), a missing stop closes the position through the protection-failure exit, and a missing exit is followed by the next exit child. An order that appears later under the same identifier is adopted (`order_late_arrival`), never duplicated. Alpaca indexes `client_order_id` on acceptance, so a request that reached the venue is always found.

## A short that cannot be sized is a skipped setup, not invalid data

On September 18 the analyzer produced a fully qualified previous-day-sweep **short** (leaders 0/6, VIX opposing). The executor raised a sizing `ValueError`, which the diagnostics labelled `purchase_size · invalid_data` 58 times. The account cannot short at all (cash account, `shorting_enabled` false), and a $15 target cannot form one whole QQQ share. The executor now checks shorting permission first and reports both cases as ordinary waits with explicit reasons (“cannot short QQQ … short setups are skipped” / “Short setups need whole QQQ shares within 1% of the $15 purchase target”). Nothing about short execution changed; the record now says why.

## Submission budget starts at quote receipt

The entry deadline was `quote timestamp + 15 s`. The quote may already be up to 15 s old when read, and the broker reads that follow (VIX quote confirmation, durable reservation, account, positions, orders, clock) can spend the remainder, so an admissible entry could expire before submission and be replanned every tick. The 15-second budget now starts at quote **receipt**; the quote-age rule at the read, the 1% drift rule and the geometry rules are unchanged.

## Immediate crypto limit absorbs a one-tick uptick

The IOC limit was set exactly at the observed ask, so any uptick during the reads between preflight and the claim retired the plan, and an uptick before the venue match expired the order with zero fill while consuming the event and one of the two session attempts. The limit is now the ask plus **0.03%**, capped at the confirmed entry plus the existing 0.1% drift rule. An immediate limit still fills at the best available ask, so the buffer costs nothing when the book is unchanged.

## Pending immediate orders get a five-second settle grace

An immediate-or-cancel buy can still report `pending_new`/`accepted` in the POST response while the venue is matching it. The executor cancelled such an entry in the same tick, so a cancellation that beat the match turned a valid signal into “ended without a fill” while consuming the event and one of the two session attempts. The executor now waits up to five seconds after the claim (one worker cycle) before cancelling a still-pending immediate entry; a fill that lands in that window is protected normally. Turning Live off still cancels immediately.

## Container logs

Worker-cycle failures and unexpected execution-tick failures are now logged to standard error with tracebacks (previously `docker logs pivot-video` was empty). No credentials or order payloads are logged.

## Verification

New fixtures: `pivot/tests/test_lost_order_resolution.py`, `pivot/tests/test_crypto_lost_order_resolution.py` and `pivot/tests/test_admission_fixes_audit.py` cover lost stop, exit and entry requests in both engines, late arrival, reservations and foreign orders blocking resolution, skipped shorts, the receipt-anchored budget with slow broker reads, and the immediate-limit buffer. Three existing fixtures that pinned the old behaviour (an unknown entry stayed uncertain forever; a prepared entry expired on the quote's exchange timestamp; a one-cent uptick retired a plan) were updated to the new semantics with moves beyond the buffer. These are offline fake-broker checks, not live fills.
