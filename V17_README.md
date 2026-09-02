# V17 — Full-Scale Trader Policy Replica

**Base:** exact uploaded V16 repository.

## Empirical changes
Source: 778,116 trades / 76,154 markets.

- Entry-count sizing direction corrected using the full-scale result:
  - CHEAP: $0.58 -> $0.42 -> $0.22
  - MID: $2.02 -> $2.02 -> $1.90
  - CORE: $7.80 -> $7.06 -> $4.40
  - HIGH: $22.20 -> $24.00 -> $14.99
- The implementation preserves V16's fine-price-band empirical sizing and
  applies the full-scale regime entry-position ratios, so the correction does
  not throw away the price-band information.
- Side persistence: 88.06%.
- Trajectory threshold: +/-0.005.
- Trajectory shares:
  CHEAP 13.76/53.99/32.25,
  MID 34.14/42.24/23.61,
  CORE 49.97/28.08/21.95,
  HIGH 58.65/13.30/28.05 (rising/falling/flat).
- Current four-asset benchmark: 48.4/30.6/12.2/8.8 by regime.
- Optional six-asset mode: 59.3/24.2/9.3/7.2 and adds DOGE/HYPE discovery.
- V16 global scheduler, cumulative quota, durable state restoration,
  passive-bid paper execution, hard cutoff and paper safety lock are preserved.

## Six-asset mode
Leave `V17_SIX_ASSET_MODE=false` (default) unless you intentionally want DOGE
and Hyperliquid. When enabled, the discovery layer includes DOGE/HYPE and the
scheduler switches to the six-asset regime benchmark.

## Verification
The complete repository test suite passes locally. No live orders are enabled.


## Execution-speed fixes
- Order-book requests for all active market tokens are fetched concurrently
  instead of sequentially.
- Default main-loop polling is 0.25s, while the trader-derived intertrade
  distribution remains the actual cadence clock.
- Market discovery refreshes every 5s by default so 5-minute market rollovers
  do not create avoidable idle periods.
- The strict cumulative fine-band quota remains the default. If a cadence tick
  stays due for 2s with no quota-eligible available band, an opt-in emergency
  selection is used to avoid artificial long gaps; the least overrepresented
  available band is preferred.
- Environment overrides: `LOOP_SECONDS`, `DISCOVERY_INTERVAL_SECONDS`,
  `BOOK_WORKERS`, and `CADENCE_FALLBACK_SECONDS`.


## $10 paper experiment

This experiment is configured by default for a $10 paper bankroll. It scales the
existing trader-derived target curve by `PAPER_TARGET_SCALE=0.10`, limits each
bet to 10% of bankroll, and limits aggregate open exposure to 50% of bankroll
(subject also to the absolute `MAX_TOTAL_EXPOSURE` cap). This changes only the
experimental dollar scale; it does not alter the trader-derived band, regime,
entry-count, trajectory, or side-persistence logic.

A 30% marked-equity drawdown pauses new entries. Existing positions remain
eligible for normal settlement. Each accepted bet logs BET #, model target,
scaled target, actual spend, cumulative spend, cash, open exposure, and equity.
`CUMULATIVE_SPEND` is lifetime paper turnover, not bankroll usage.

For a brand-new experiment, start with an empty persistent `DATA_DIR` (or set
`FRESH_START=true` for the first launch only). Then leave `FRESH_START=false`
for subsequent restarts so the $10 paper ledger is preserved.
