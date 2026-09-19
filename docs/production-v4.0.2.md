# Version 4.0.2: complete strategy views

The strategy dropdown previously changed only two analysis sections. The controls, purchase settings, data panels and histories stayed visible, making the selection appear ineffective. This release scopes the whole workspace to the selected strategy.

- **Socrates:** stock strategy control, purchase target, Nasdaq analysis, session review, data connections, rules and stock results.
- **4H Range Reversal:** crypto strategy control, purchase target and markets, range analysis, crypto positions, execution and outcomes. Its workspace uses the full width without an empty stock sidebar.
- **All strategies:** both workspaces and their controls.

Account balance, global Live, a compact summary of both saved strategy permissions and amounts, broker holdings, app activity and urgent incidents remain visible in every view. A crypto incident remains visible even when viewing Socrates. Changing the view saves a browser preference only; it never changes strategy permissions or sends a settings request.

## Verification

- 113 interface tests passed. New regression checks parse the shipped HTML and verify visible descendants across all three selections, repeated switching and reload persistence. Existing handler tests verify unchanged permissions and no update requests.
- 1,657 Python tests passed across app, deployment and research suites; production UI build passed.
- Desktop and 390-pixel mobile browser checks used the actual app service and current UI with isolated fake brokers, both families enabled, and a synthetic crypto incident. All three views showed the correct sections. The preview audit recorded zero mutation requests, broker submissions or worker starts.

This is a presentation fix. Broker execution and strategy rules are unchanged.
