# Incident Postmortem: INC-84

- **Incident ID:** INC-84
- **Severity:** SEV-2
- **Timestamp:** 1756000000
- **Root Cause Service:** `ledger-core`
- **Impacted Services:** `ledger-core`, `refunds-worker`, `status-page`

## Summary
Transient duplicate refund webhook replay between `refunds-worker` and `ledger-core` delayed status updates on `status-page`. Resolved by enforcing idempotency keys under `ADR-015`.
