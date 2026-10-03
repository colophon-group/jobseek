# Account lookup recovery

`AppBootstrapProvider` distinguishes a resolved account response from a temporary
lookup failure. A rejected request or four-second wait timeout sets the account
status to `unavailable`; it does not prove that the viewer is logged out or clear
the login hint. A successful anonymous response still clears a stale hint. The
existing initial no-hint path remains anonymous without a request.

When no identity is confirmed, `isPending` remains true even after the loading
wait ends. Consumers must use it to block anonymous watchlist staging, account
imports, and account-dependent preference writes. The header shows **Retry
account** instead of login. Retry uses the existing action, coalesces concurrent
attempts, and allows at most one manual attempt every five seconds. Neither
failure nor an identity-change notification starts an automatic retry. Local
theme and language choices remain usable independently of account persistence.

A failed refresh can retain a previously confirmed signed-in snapshot only
within its current identity context. Logout invalidates before the auth request.
Hint changes, the auth SDK's local mutation signal, and data-free cross-tab auth
notifications invalidate prior identity and pending client results. A request
generation and hint comparison prevent late responses from restoring an older
context. Initial confirmation preserves mounted page state and imports pending
watchlist intent once; changing an established identity resets its saved/starred
provider state. Hints and notifications never establish identity or permissions.
Server authorization of account and owner mutations remains authoritative.

The four-second bound limits the client wait. `Promise.race` and invalidation do
**not** cancel an already dispatched Server Action, roll back its work, or stop
its existing authenticated anonymous-language preference inheritance. A manual
retry may overlap server work from a timed-out attempt; it adds no automatic
replay or separate write path. Late results from that attempt are ignored by the
client. This bound preserves the previous loading deadline and does not assert
that every deployed lookup finishes within it.

Cross-tab notification uses `BroadcastChannel` without identity, cookie, or
session data, and closes its listeners on unmount. If that browser mechanism is
unavailable, local auth notifications and lifecycle hint checks still apply;
an external session switch with an unchanged hint cannot be inferred locally.
The listeners add no session GETs or rate-limit exemption.

Before promotion, verify recovery in an authenticated browser through the normal
account retry and owner controls, plus anonymous watchlist clone/import handoff.
Unit tests establish state transitions and race handling, not production auth
transport, deployed action latency, or owner-write behavior.
