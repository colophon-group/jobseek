# Native B0 producer control client

`NewClient()` uses the existing Go producer at
`/run/jobseek-lightpanda-producer/control.sock`, UID 10001. Every exchange checks
the actual 0700 directory, 0600 socket, kernel peer UID on Linux and unchanged
device/inode after connection. There is no environment-controlled route or peer
override. Other platforms cannot grant production peer authority.

`Manifest`, `Prepare`, `Activate` and `Enqueue` preserve the installed protocol,
canonical ASCII JSON, exact integer millisecond scheduling and optional decimal
source-score CAS. The producer owns assignment, payload, lifetime occupancy and
atomic legacy transfer. Responses require closed canonical fields, bounded counts,
fixed decisions and exact operation/cohort/approved-digest correlation. One
prefix-inclusive 256 KiB frame must end in exact EOF, within three seconds or the
caller's shorter deadline. Errors never echo request or remote error text.

Each mutation sends once. A disconnect, cancellation or timeout can follow a
committed activation: the caller must retain its approved manifest before the
first mutation, re-attest current authority and recover using the exact approved
task and digest. The client does not allocate epochs, select ownership, retain
PostgreSQL intent, SAVE Redis or start services. Completing those operations is
part of the full migration's native forward cutover coordinator.

`testdata/collect.py` captures the actual Python client's request serialization
and response decisions without network access. Regeneration and native socket
race tests run on both Linux architectures. The existing root-to-UID-10001
installed producer/private Redis integration uses this client for manifest,
prepare, activate, already-activated retry and terminal reactivation, retaining
its actual queue/fencing checks. Darwin socket tests use only an internal test
peer callback; Linux tests use actual SO_PEERCRED.
