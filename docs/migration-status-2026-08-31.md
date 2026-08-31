# 2026-08-31 Pure-Core Migration Evidence

- Ingestion boundary RED: Core CI run 33366228339 failed only because `trading_core.ingestion` did not yet exist; 63 prior tests passed.
- Ingestion implementation GREEN: Core CI run 33366341362 passed.
- Market-feature boundary RED: Core CI run 33366366619 failed only because `trading_core.market_features` did not yet exist; 65 prior tests passed.
- Market-feature implementation GREEN: Core CI run 33366571366 passed.
- Preserved public regression coverage for both domains; latest exact-head Core CI after regression migration is green.
- Azure table/queue/resource mappings are intentionally not copied into public core; private runtime adapters remain the composition boundary.
