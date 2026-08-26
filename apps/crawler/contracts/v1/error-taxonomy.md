# Closed error taxonomy v1

Human messages and bounded `diagnostic_details` are diagnostics only and are
forbidden from driving policy. Runtime policy is selected
from this closed `ErrorCode -> ErrorDisposition` mapping, which both validators
enforce:

| Code | Required disposition |
|---|---|
| `TDM_RESERVED` | `DEFER_POLICY` |
| `PROVIDER_GONE` | `PROVIDER_GONE_POLICY` |
| `PERMANENT_GONE` | `PERMANENT_GONE_POLICY` |
| `INVALID_CONFIG` | `INVALID_CONFIG_POLICY` |
| `CANCELLED` | `CANCELLED_POLICY` |
| `AMBIGUOUS_ORIGIN`, `UNSUPPORTED_CAPABILITY` | `FAIL_CLOSED_POLICY` |
| `HTTP_STATUS`, `TIMEOUT`, `TRANSPORT`, `ANTI_BOT`, `EMPTY_RESULT`, `INTERNAL`, `TARGET_LOST`, `SESSION_LOST`, `RESOURCE_LIMIT`, `NAVIGATION` | `RETRY_POLICY` |

`HTTP_STATUS` requires a 100–599 status. `retry_after_ms` is independently
bounded to 604,800,000 ms (7 days); it is not constrained by the 15-minute
operation deadline and does not authorize the extractor to reschedule.
Gone/defer/circuit decisions remain with the queue-owning worker policy; the
execution service only reports the typed fact.
