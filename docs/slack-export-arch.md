# Slack Export: `#arch-payments`

- **Thread ID:** SLACK-ARCH-881
- **Channel:** `#arch-payments`
- **Timestamp:** 1757580800
- **Discusses ADR:** `ADR-014`
- **Discusses Service:** `payments-api`
- **URL:** `https://fintech.slack.com/archives/C01ARCHPAY/p1757400000`

## Thread Summary
Post-INC-99 consensus between `@sarah-staff-eng` and `@priya-sre-oncall`: never allow >500ms sync retry in `payments/retry_policy.py` or `checkout-web` worker pools saturate within 12 seconds.

---

- **Thread ID:** SLACK-ARCH-892
- **Channel:** `#arch-payments`
- **Timestamp:** 1758000000
- **Discusses ADR:** `ADR-015`
- **Discusses Service:** `ledger-core`
- **URL:** `https://fintech.slack.com/archives/C01ARCHPAY/p1758000000`

## Thread Summary
`@sarah-staff-eng` confirmed `ledger-core` maintains both sync gRPC and async Kafka edges to `payments-api` under `ADR-015`.
