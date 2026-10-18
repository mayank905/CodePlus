# Incident Postmortem: INC-99

- **Incident ID:** INC-99
- **Severity:** SEV-1
- **Timestamp:** 1757408000
- **Root Cause Service:** `payments-api`
- **Impacted Services:** `payments-api`, `cart-service`, `checkout-web`

## Summary
Checkout cascading thread-pool exhaustion caused by a 5s synchronous retry storm in `payments-api` (`payments/retry_policy.py`). Resolved by rolling back the retry multiplier and ratifying `ADR-014` (<500ms fail-fast ceiling).
