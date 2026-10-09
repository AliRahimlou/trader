# Version 4.6.1 · Trading audit repairs

An eligible previous-day sweep could disappear at the next session's open even though its event expiry included that session. The analyzer now reconstructs each surviving sweep against the previous-day level that applied when it began. Its identity, original expiry and invalidation remain intact; today's hourly, leader, VIX, price and target checks must still pass. Yesterday's obsolete levels cannot create new sweeps today.

Prepared stock entries now expire at the earlier of the reviewed noon entry deadline and the broker's close-minus-30-minute cutoff. Recovery and the final pre-submission check enforce the entry window, including when a slow durable claim crosses noon. Existing position protection and exits continue outside entry hours.

The full-input archive stored repeated large observation documents without compression and counted already-stored frames against every append. Once its 512 MiB cap was reached, new full observations could no longer be saved even with available host disk. New documents are compressed; bounded batches of legacy documents are losslessly compressed when necessary, allowing SQLite to reuse freed pages. No observation, timestamp or frame is deleted. The reader accepts old and new storage formats, bounds decompression and retains integrity/replay checks. Earlier missing observations cannot be recovered by this repair. Older release replay tools cannot read compressed documents; use this release's reader when inspecting mixed archives.

Readiness now states that shorts are disabled under the default policy and identifies an allowed long when it follows a disallowed short. A set containing no allowed candidates is no longer incorrectly labeled already traded. The timing explanation shows that the first completed hourly signal arrives at 10:30 ET, within the existing 10:00–12:00 policy window.

The [4.6.0 note](production-v4.6.0.md) now separates its unverified replay claims from documented source review. These repairs establish software behavior, not profitability. Broadening hours, lowering confirmation requirements or changing the target economics requires a separately specified and measured strategy experiment.

Validation covers production-default sweep-to-order lifecycle, restart and cutoff boundaries, stale/missing confirmation, preservation of archived bytes and replay, production readiness, the full offline suite and the frontend build. Broker fixtures do not establish live fills or net returns. Release installation must use the existing hold, flat-account and permission-preservation checks.

The execution policy version and saved permission are unchanged: this patch repairs the declared lifetime and deadlines without altering hours, trade amounts, permitted directions, entry allowances or stop-distance limits.
