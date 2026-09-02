# Polymarket Bot B — V17 $10 Scaled Paper Experiment

Paper-only behavioral research engine. It does **not** place live orders and does not read/copy the reference trader's private activity.

## What changed in V13

V13 removes several unsupported strategy assumptions from the previous branch:

- No synthetic composite alpha score.
- No arbitrary `+2.5c`/`35c` momentum or state-reset thresholds.
- No synthetic "remaining target" capital ladder.
- No arbitrary 1.02/1.05/1.10 entry multipliers.
- No 2-second minimum enforced as trader behavior.
- Entry size is conditioned on price band + observed entry-state medians.
- Side persistence is retained as a preference because ~89.3% of consecutive same-market pairs stayed on the same side.
- The measured weakness/strength gradient is used only as an empirical directional likelihood.
- The final 60-second cutoff remains hard because violations were ~0.04%.
- Research capture defaults to 1-second snapshots and retains a real time-based 60-second history.
- Research records explicit trade-candidate vs non-trade observations plus depth imbalance and movement features.

## Evidence boundary

The following remain **UNKNOWN** and are not hardcoded:

- the reference trader's exact private trigger;
- whether price movement is causal or consequential;
- exact passive-order placement distance;
- exact side-reset mechanism;
- exact market/asset-specific hidden alpha.

## Railway variables

```text
PAPER_TRADING=true
STARTING_CAPITAL=10
MAX_TOTAL_EXPOSURE=10
PAPER_TARGET_SCALE=0.10
MAX_BET_BANKROLL_PCT=0.10
MAX_OPEN_EXPOSURE_PCT=0.50
PAPER_MAX_DRAWDOWN_PCT=0.30
MIN_PAPER_FILL_USD=0.01

START_TRADING_SECOND=0
STOP_TRADING_SECOND=240
HARD_CUTOFF_SECONDS=60

MAX_DEPTH_PARTICIPATION=0.25
MIN_BID_DEPTH=1

MIN_TRADE_GAP_SECONDS=0
LOOP_SECONDS=0.25
REPORT_INTERVAL_SECONDS=60

DECISION_SAMPLE_SECONDS=1
ORDERBOOK_SAMPLE_SECONDS=1

DATA_DIR=/app/data
DATA_MAINTENANCE_SECONDS=3600
BURST_GAP_SECONDS=18
CADENCE_FALLBACK_SECONDS=2
DISCOVERY_INTERVAL_SECONDS=5
BOOK_WORKERS=8
FRESH_START=true
```

For the first clean experiment use `FRESH_START=true`; after that switch it to `false` on the persistent Railway volume.

`PAPER_TARGET_SCALE` scales the trader-derived model target without changing its relative entry-count/band shape. The default 0.10 makes the $10 experiment substantially smaller than the full-scale trader targets. `MAX_BET_BANKROLL_PCT=0.10` caps any single paper bet at 10% of bankroll, and `MAX_OPEN_EXPOSURE_PCT=0.50` caps total open paper exposure at 50% of bankroll.

`PAPER_MAX_DRAWDOWN_PCT=0.30` is an experiment safety stop: once marked equity falls to 70% of starting equity, new paper entries halt. Existing positions can still resolve normally.

## Research files

Permanent:

- `trades.csv`
- `trade_details.csv`
- `markets.csv`
- `resolutions.csv`
- `settlement_details.csv`
- `pnl_1min.csv`
- `regime_1min.csv`
- `paper_state.json`

High-volume:

- `decisions.jsonl`
- `orderbooks.jsonl`

The research stream records both candidate events and non-trade observations. Book snapshots include bid/ask, bid/ask depth, spread, and depth imbalance. Decision records additionally include 1/3/5/10/30-second movement features, entry state, burst position, and time since the previous paper entry.

## Accounting

Realized P&L is derived from settlement records and reconciled on load/save. Each bet records model target, scaled target, actual spend, cumulative spend, cash after the bet, and open exposure. `CUMULATIVE_SPEND` is turnover, not current bankroll usage.

## Validation

Run:

```bash
pytest -q
python -m py_compile strategy.py bot.py paper_ledger.py market_discovery.py research_logger.py
```

The tests cover fine-band boundaries, entry-state sizing, side persistence, empirical trajectory preference, final-minute cutoff, depth limits, resolution accounting, and research logging.


## V13.1 controlled experiment — $300 total exposure

V13.1 changes one strategy variable only: the minimum depth gate for CORE and
HIGH is loosened to 1.0 for BTC, ETH, SOL, and BNB. Spread gates, sizing,
trajectory logic, side persistence, cutoff, and accounting are unchanged.
The purpose is to test whether the verified CORE/HIGH starvation was caused
by the regime-scaled depth gate. If CORE/HIGH share does not recover, the
next experiment should change spread only—not both together.


### $300 paper exposure setting

The previous `$35 per asset × 4 assets = $140` practical ceiling is removed.
For this experiment, the paper governor is configured so market, asset and
individual order limits can each reach $300, while the total open paper
exposure remains capped at $300. These are paper-account controls, not claims
about the reference trader's actual maximum exposure.


## V14.1 selector correction

The V14 selector no longer multiplies historical fine-band share by trajectory
share as a synthetic alpha score. Fine-band share and trajectory share remain
separate empirical measurements. Side persistence remains a preference when a
thesis already exists.


## V14.2 cadence correction

V14.2 keeps the 40% empirical sizing model but corrects execution pacing:
accepted trades now advance one global trade clock sampled from the trader's
exact 701,962 observed intertrade gaps. This prevents one trade from being
placed in every active market on every one-second loop.

The cadence sampler is descriptive pacing only. It is not treated as a trader
trigger or a hard minimum intertrade rule. Market scan order rotates after each
accepted trade to avoid a fixed asset-order bias.


## V14.2 final cadence correction

V14.2 uses the complete trader intertrade-gap histogram from 701,962 observed
gaps as a global execution-pacing distribution. One accepted paper trade
advances the global trade clock, preventing all four active markets from being
entered on every loop. A zero-second observed gap is represented at the bot's
one-second loop resolution by allowing the next trade on the next loop, rather
than imposing a multi-second hard minimum.

The 40% sizing data remains stored in `trader_behavior.json`; the `$300`
portfolio limit is an experiment-level paper control, not a claim about the
trader's actual exposure limit.


## V15.0 empirical trader process

This version uses the verified trader distributions as the process model:
global intertrade cadence, fine price-band frequency, 89.3% same-side
continuation, and fine-band/entry-number notional medians scaled to 40%.
The unknown private trigger is not claimed to be known. One trade is accepted
per outer loop, with a global empirical intertrade clock; zero-second observed
gaps are handled on the next loop iteration.

## V16 Trader Policy Replica

V16 is based on the V15.2 Exact Trader Distribution runtime. It preserves the
40% empirical entry sizing, 60-second hard cutoff, buy-only paper ledger,
empirical global intertrade cadence, side-persistence observation, and 13
fine price bands. The decision layer now evaluates all eligible active-market
candidates globally, uses the trader's measured fine-band trade and capital
shares as cumulative allocation targets, persists distribution state through
the durable ledger, and never substitutes a different fine band after policy
selection.

## V17 $10 paper experiment

This build defaults to a $10 paper bankroll and a $10 total open-exposure cap.
Every accepted paper bet is printed with its individual spend, cumulative spend,
remaining cash, open exposure, and percentage of starting bankroll used. The
underlying trader-derived sizing policy is unchanged; the $10 cap is an
experiment-level paper-account control.

Example log line:
`BET #7 | ... | SPENT=$0.58 | TOTAL_SPENT=$4.21 | CASH_REMAINING=$5.79 | OPEN_EXPOSURE=$2.44`
