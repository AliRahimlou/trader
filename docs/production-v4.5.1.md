# Version 4.5.1 — Socrates-only focus, order-path readiness and a cleaner interface

The owner asked on September 22, 2026 to pause the crypto strategy, focus on Socrates, make sure orders can go through, and make the interface clean and easy to read. No trading rule, policy version or saved permission changes in this release, so the owner does not need to accept anything again and the installed updater deploys it normally.

## Crypto paused, code kept

- `pivot/feature_flags.py`: crypto is paused by default. Setting `PIVOT_CRYPTO_PAUSED=0` in the private environment file on the host turns it back on without a code change.
- While paused the crypto engine sends no entries and polls no crypto market data. With no crypto position it makes no broker calls at all, which leaves the whole Alpaca request budget to Socrates. A crypto position that already exists would keep its stop, target and exit management.
- Saved crypto settings are never written or cleared by the pause. The one crypto change accepted while paused is turning it Off; any other crypto change is refused with "Crypto is paused in this release; Socrates-only focus".
- The deployment permission fingerprint and readiness output are unchanged, so rollouts proceed as before.

## "Ready to trade" checklist for Socrates

Every snapshot now carries `socrates_readiness`: one status (Ready, Waiting or Needs your action), a one-sentence headline, the next step for the owner when something is blocking, and fifteen checks in plain language: rules accepted, Socrates on, account active, buying power, QQQ and PSQ tradable and fractionable, market hours, QQQ, leader and VIX data freshness with the VIX request budget, entries left today, no update hold, no foreign position or order, workers running, and the current setup. The only new broker reads are one asset lookup each for QQQ and PSQ, at most every ten minutes. Nothing in the checklist can place, cancel or change an order.

How the checklist reads:

- **Needs your action** appears only for something the owner can fix: rules not accepted, Live money Off, Socrates Off, not enough cash, an account Alpaca restricts, a position or order Pivot did not place, a stopped worker. The next step names what to do, and the button beside it opens the same review dialog as the matching control. The button only offers Live money when an enabled strategy can trade, which is the header switch's own rule. When Socrates is Off, turning it back on comes first because the Live money review lists the enabled strategies' rules.
- When Pivot paused Socrates itself (a rejected, replaced or unconfirmed order), the next step says to check the QQQ and PSQ positions and orders in Alpaca first. No one-click button is offered; the Socrates card's own switch and review remain.
- Conditions that clear by themselves still block entries but read **Waiting** with a paused headline and no owner step: late QQQ, leader or VIX data during the session, a used-up monthly VIX allowance, and QQQ reported untradable. A restart is suggested only when a background worker has actually stopped.
- While nothing is failing, the headline says why no entry can happen when that is the case: both attempts used today, the final 30 minutes, or an app update being installed. A ready setup whose event was already traded or attempted is shown as handled, because the executor never enters the same event twice. A PSQ restriction does not hold back a ready long QQQ setup.
- The card is rebuilt only when something on it changes; the check time updates in place, so a focused button keeps focus. Screen readers hear the status and headline only when they change.

## Order path audited against Alpaca's rules

`docs/order-path-audit-4.5.1.md` lists every request Socrates can send and the Alpaca rule each one must meet. Two latent problems were fixed: an entry whose protective stop is not in whole cents is now skipped before it is sent (the stop would otherwise have been rejected after the fill, leaving shares unprotected), and stop and exit quantities are written as plain decimals (a tiny remaining quantity was written in scientific notation, which Alpaca rejects). The entry payload is checked against the rules just before it is reserved; stops and exits are never withheld by that check.

## Interface

- Socrates comes first: one status card at the top with the checklist, the next step and an "Accept updated rules" button when the rules need review.
- The Live money dialog shows the key points and then the full rule list, open, with the Socrates rules version; the accept box covers the list the owner can see.
- The account, controls and status line are compact; the status line turns amber when Live money is off or needs review.
- Crypto sections stay in the page but sit below Socrates, grayed out with a Paused badge and disabled controls. A crypto position or incident that still needs managing is never grayed out, and its status stays visible after the pause note. Lifting the pause re-enables the crypto amount and market inputs without a reload.
- The Look-left charts scroll inside their own box; the page no longer scrolls sideways on a phone. Chart labels no longer overlap or hide under candles.
- An open PSQ position shows its own stop and target and explains the short proxy. Completed proxy trades read "Socrates short via PSQ (inverse QQQ)".
- Rule text matches 4.5 (4 of 7 leaders, 60-minute reaction window, entry at the level within 0.4%, events live to the end of the next session, 1R minimum target, PSQ for shorts); long text sits in collapsible sections.
- `scripts/ui_fixture_server.py` renders the page from in-memory fixtures for design checks without any broker connection.

## Verification

Full Python suite, 197 interface tests and the production build pass offline. Six fixture states (waiting, rules not accepted, setup ready, open PSQ position, rejection pause, market closed) were rendered at 1280 and 390 pixels before and after. No live order was sent; the first live entry remains the first end-to-end test of the broker path.
