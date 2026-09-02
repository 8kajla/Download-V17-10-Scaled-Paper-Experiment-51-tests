import logging
import os
import shutil
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from market_discovery import discover, book, resolve
from paper_ledger import PaperLedger
from research_logger import ResearchLogger
from strategy import CapitalFirstStrategy
from paper_risk import bounded_float, drawdown_halt, paper_budget_target

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s UTC %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")


def prepare_fresh_data_dir():
    data_dir = Path(os.getenv("DATA_DIR", "/app/data")).expanduser()
    fresh = os.getenv("FRESH_START", "true").lower() in ("1", "true", "yes", "on")

    if str(data_dir) in ("/", ".", ""):
        raise RuntimeError(f"Refusing to wipe unsafe DATA_DIR={data_dir!r}")

    data_dir.mkdir(parents=True, exist_ok=True)
    if fresh:
        for child in data_dir.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    return data_dir


DATA = prepare_fresh_data_dir()

if os.getenv("PAPER_TRADING", "true").lower() != "true":
    raise SystemExit("SAFETY LOCK: PAPER_TRADING must be true")

# V17 $10 paper experiment defaults. Override with environment variables for
# other paper-only test sizes; PAPER_TRADING remains hard-locked below.
STARTING_CAPITAL = max(0.01, float(os.getenv("STARTING_CAPITAL", "10")))
ABSOLUTE_MAX_TOTAL_EXPOSURE = max(0.0, float(os.getenv("MAX_TOTAL_EXPOSURE", "10")))
PAPER_TARGET_SCALE = bounded_float("PAPER_TARGET_SCALE", 0.10, 0.0, 1.0)
MAX_BET_BANKROLL_PCT = bounded_float("MAX_BET_BANKROLL_PCT", 0.10, 0.0, 1.0)
MAX_OPEN_EXPOSURE_PCT = bounded_float("MAX_OPEN_EXPOSURE_PCT", 0.50, 0.0, 1.0)
PAPER_MAX_DRAWDOWN_PCT = bounded_float("PAPER_MAX_DRAWDOWN_PCT", 0.30, 0.0, 0.99)
MIN_PAPER_FILL_USD = bounded_float("MIN_PAPER_FILL_USD", 0.01, 0.0, 1000.0)
EFFECTIVE_MAX_TOTAL_EXPOSURE = min(
    ABSOLUTE_MAX_TOTAL_EXPOSURE, STARTING_CAPITAL * MAX_OPEN_EXPOSURE_PCT
)

strategy = CapitalFirstStrategy(
    bankroll=STARTING_CAPITAL,
    max_total_exposure=EFFECTIVE_MAX_TOTAL_EXPOSURE,
    start_sec=float(os.getenv("START_TRADING_SECOND", "0")),
    stop_sec=float(os.getenv("STOP_TRADING_SECOND", "240")),
    hard_cutoff_seconds=float(os.getenv("HARD_CUTOFF_SECONDS", "60")),
    # Zero here because the trader's median 2s cadence is not a hard rule.
    min_trade_gap_seconds=float(os.getenv("MIN_TRADE_GAP_SECONDS", "0")),
)

ledger = PaperLedger(DATA / "paper_state.json", strategy.bankroll)
# The ledger file is created here so startup validation never treats an
# expected first-run artifact as a fatal missing dependency.
ledger.save()
strategy.restore_policy_state(ledger.trades)
research = ResearchLogger(DATA, ledger)

markets = {}
histories = {}
pending = {}
last_disc = 0.0
last_report = 0.0
last_maintenance = 0.0
last_trade = {}
ob_last = {}
decision_last = {}
consecutive_errors = 0
# Global trader-style trade clock. This is the execution cadence model,
# not a minimum-gap rule. One accepted trade advances this clock by a
# sample from the trader's observed intertrade distribution.
next_trade_at = 0.0
scan_offset = 0
current_target_band = None
paper_halted = False

# P90 of the observed trader intertrade distribution. This is recorded as a
# descriptive burst boundary only; it is NOT used as a trade trigger.
BURST_GAP_SECONDS = float(os.getenv("BURST_GAP_SECONDS", "18"))
# The observed trader cadence is used as the primary trade clock. When the
# exact quota has no currently available band, allow a controlled fallback
# after a short wait rather than manufacturing a long artificial gap.
CADENCE_FALLBACK_SECONDS = max(0.5, float(os.getenv("CADENCE_FALLBACK_SECONDS", "2")))
DISCOVERY_INTERVAL_SECONDS = max(2.0, float(os.getenv("DISCOVERY_INTERVAL_SECONDS", "5")))
BOOK_WORKERS = max(2, int(os.getenv("BOOK_WORKERS", "8")))
LOOP_SECONDS = max(0.05, float(os.getenv("LOOP_SECONDS", "0.25")))
BOOK_EXECUTOR = ThreadPoolExecutor(max_workers=BOOK_WORKERS, thread_name_prefix="v17-book")


def asset_exposure(asset):
    return sum(
        float(p.get("cost", 0))
        for p in ledger.positions.values()
        if p.get("asset") == asset
    )


def prepare_histories(history_map, now, window_seconds=60.0):
    for side in ("Up", "Down"):
        history_map[side] = [
            point for point in history_map.get(side, [])
            if float(point[0]) >= now - window_seconds
        ]


def market_entry_state(condition, now):
    entries = [
        t for t in ledger.trades
        if t.get("action") == "BUY"
        and t.get("condition") == condition
    ]
    if not entries:
        return {
            "count": 0,
            "seconds_since_first": 0.0,
            "seconds_since_previous": None,
            "side": None,
            "price": None,
            "burst_position": 0,
        }

    ordered = sorted(entries, key=lambda t: float(t.get("ts", now)))
    first_ts = float(ordered[0].get("ts", now))
    previous_ts = float(ordered[-1].get("ts", now))
    gaps = [float(cur.get("ts", now)) - float(prev.get("ts", now))
            for prev, cur in zip(ordered, ordered[1:])]

    burst_position = 1
    for gap in reversed(gaps):
        if gap <= BURST_GAP_SECONDS:
            burst_position += 1
        else:
            break

    latest = ordered[-1]
    return {
        "count": len(ordered),
        "seconds_since_first": max(0.0, now - first_ts),
        "seconds_since_previous": max(0.0, now - previous_ts),
        "side": latest.get("side"),
        "price": latest.get("price"),
        "burst_position": burst_position,
    }


def p(message):
    log.info(message)


def startup_data_check():
    required = [
        "decisions.jsonl",
        "orderbooks.jsonl",
        "trades.csv",
        "markets.csv",
        "resolutions.csv",
        "pnl_1min.csv",
        "paper_state.json",
    ]
    missing = [name for name in required if not (DATA / name).exists()]
    if missing:
        raise RuntimeError(f"DATA STORE INITIALIZATION FAILED: {missing}")


def resolve_pending(now):
    for condition, market in list(pending.items()):
        if now < float(market.get("end_ts", 0)) + 2:
            continue

        try:
            token, outcome, status = resolve(market)
            if token:
                closed = ledger.settle(condition, token)
                pnl = sum(float(x["pnl"]) for x in closed)

                research.record_resolution(
                    ts=now,
                    market=market,
                    winner=outcome or token,
                    winner_token=token,
                    closed=closed,
                )

                p(
                    f"RESOLUTION | asset={market['asset']} | slug={market['slug']} "
                    f"| winner={outcome or token} | pnl={pnl:+.4f} | closed={len(closed)}"
                )

                pending.pop(condition, None)
                markets.pop(condition, None)
                histories.pop(condition, None)
            elif status == "CLOSED_UNRESOLVED":
                research.record_resolution_error(
                    ts=now, market=market, status=status
                )
        except Exception as exc:
            research.record_resolution_error(
                ts=now,
                market=market,
                status=f"ERROR:{type(exc).__name__}",
            )
            p(
                f"RESOLUTION ERROR | {market['slug']} | "
                f"{type(exc).__name__}: {exc}"
            )


def report(books):
    global last_report

    now = time.time()
    interval = float(os.getenv("REPORT_INTERVAL_SECONDS", "60"))
    if now - last_report < interval:
        return

    last_report = now
    metrics = ledger.mark(books)
    metrics["positions"] = len(ledger.positions)
    research.record_pnl(now, metrics)

    p(
        f"P&L ours ${metrics['pnl']:+.2f} | realized ${metrics['realized']:+.2f} "
        f"| unrealized ${metrics['unrealized']:+.2f} | cash ${metrics['cash']:.2f} "
        f"| open ${metrics['open_cost']:.2f} | positions {metrics['positions']}"
    )


def main():
    global last_disc, last_maintenance, consecutive_errors, next_trade_at, scan_offset, current_target_band, paper_halted
    cadence_due_since = None

    startup_data_check()
    # Keep the canonical V17 version identifier in the runtime banner for
    # existing monitoring/regression checks while making the $10 experiment explicit.
    p(
        f"BOT B | PAPER ONLY | V17 FULL-SCALE TRADER REPLICA 40PCT | $10 SCALED EXPERIMENT | "
        f"starting_cash=${strategy.bankroll:.2f} | max_open_exposure=${strategy.max_total_exposure:.2f} | "
        f"paper_target_scale={PAPER_TARGET_SCALE:.3f} | max_bet_pct={MAX_BET_BANKROLL_PCT*100:.1f}% | "
        f"max_open_pct={MAX_OPEN_EXPOSURE_PCT*100:.1f}% | max_drawdown_pct={PAPER_MAX_DRAWDOWN_PCT*100:.1f}%"
    )

    while True:
        try:
            now = time.time()

            if now - last_disc >= DISCOVERY_INTERVAL_SECONDS:
                for market in discover():
                    markets[market["condition"]] = market

                for condition, market in list(markets.items()):
                    if any(
                        position.get("condition") == condition
                        for position in ledger.positions.values()
                    ):
                        pending[condition] = market
                    elif market["end_ts"] < now - 30:
                        markets.pop(condition, None)

                last_disc = now
                p(
                    f"MARKETS | active={len(markets)} "
                    f"| pending_resolution={len(pending)}"
                )

            resolve_pending(now)
            books = {}
            market_list = list(markets.values())

            # One global policy decision at each trader-paced cadence tick.
            # First collect ALL eligible candidates across all markets. The
            # empirical fine-band scheduler then chooses among those candidates;
            # it never asks each market to choose its own band first.
            if now >= next_trade_at:
                if cadence_due_since is None:
                    cadence_due_since = next_trade_at

                eligible = []
                scannable = []
                for market in market_list:
                    if not market.get("end_ts") or market["end_ts"] < now - 30:
                        continue
                    elapsed = now - market["start_ts"]
                    left = market["end_ts"] - now
                    if left <= 0 or elapsed < 0 or elapsed > 300:
                        continue
                    if not market["accepting_orders"]:
                        continue
                    scannable.append((market, elapsed, left))

                # Fetch all required token books concurrently. The V17 strategy
                # remains unchanged; only I/O is parallelized.
                book_futures = {}
                for market, _elapsed, _left in scannable:
                    for token_name, token in (("up", market["up"]), ("down", market["down"])):
                        book_futures[BOOK_EXECUTOR.submit(book, token)] = (market, token_name)

                market_books = {}
                for future in as_completed(book_futures):
                    market, token_name = book_futures[future]
                    try:
                        market_books.setdefault(market["condition"], {})[token_name] = future.result()
                    except Exception as exc:
                        p(f"BOOK ERROR | {market['asset']} | {market['slug']} | "
                          f"token={token_name} | {type(exc).__name__}: {exc}")

                for market, elapsed, left in scannable:
                    pair = market_books.get(market["condition"], {})
                    if "up" not in pair or "down" not in pair:
                        continue
                    up_bid, up_ask, up_bid_depth, up_ask_depth = pair["up"]
                    down_bid, down_ask, down_bid_depth, down_ask_depth = pair["down"]

                    books[market["up"]] = up_bid
                    books[market["down"]] = down_bid
                    history = histories.setdefault(market["condition"], {"Up": [], "Down": []})
                    if up_bid is not None:
                        history["Up"].append((now, up_bid))
                    if down_bid is not None:
                        history["Down"].append((now, down_bid))
                    prepare_histories(history, now, 60.0)

                    orderbook_interval = float(os.getenv("ORDERBOOK_SAMPLE_SECONDS", "1"))
                    if now - ob_last.get(market["condition"], 0) >= orderbook_interval:
                        research.record_orderbook(
                            ts=now, market=market, elapsed=elapsed, left=left,
                            up_bid=up_bid, up_ask=up_ask, up_depth=up_bid_depth,
                            down_bid=down_bid, down_ask=down_ask, down_depth=down_bid_depth,
                            up_ask_depth=up_ask_depth, down_ask_depth=down_ask_depth,
                            up_history=history["Up"], down_history=history["Down"],
                        )
                        ob_last[market["condition"]] = now

                    state = market_entry_state(market["condition"], now)
                    candidates = strategy.build_candidates_for_market(
                        elapsed, up_ask, down_ask, up_bid, down_bid,
                        history["Up"], history["Down"], now,
                        asset=market["asset"], market=market["asset"],
                        thesis_side=state["side"], market_entry_count=state["count"],
                        seconds_since_first_entry=state["seconds_since_first"],
                        up_depth=up_bid_depth, down_depth=down_bid_depth,
                    )
                    for c in candidates:
                        c["_market"] = market
                        c["_state"] = state
                        c["_elapsed"] = elapsed
                        c["_left"] = left
                        c["_up_ask"] = up_ask
                        c["_down_ask"] = down_ask
                        c["_up_depth"] = up_bid_depth
                        c["_down_depth"] = down_bid_depth
                    eligible.extend(candidates)

                if eligible and not paper_halted and ledger.cash >= MIN_PAPER_FILL_USD:
                    current_metrics = ledger.mark(books)
                    if drawdown_halt(
                        current_metrics["equity"], ledger.start_equity, PAPER_MAX_DRAWDOWN_PCT
                    ):
                        paper_halted = True
                        p(
                            f"PAPER HALT | drawdown_limit={PAPER_MAX_DRAWDOWN_PCT*100:.1f}% "
                            f"| equity=${current_metrics['equity']:.2f} | start=${ledger.start_equity:.2f} "
                            f"| cash=${ledger.cash:.2f} | open=${ledger.total_open_cost():.2f}"
                        )
                    if paper_halted:
                        target_band = None
                    else:
                        target_band = strategy.choose_distribution_band(eligible)
                    fallback_used = False
                    if target_band is None and (
                        now - cadence_due_since >= CADENCE_FALLBACK_SECONDS
                    ):
                        target_band = strategy.scheduler.choose_band(
                            eligible, allow_over_quota=True
                        )
                        fallback_used = target_band is not None
                        if fallback_used:
                            p(
                                f"CADENCE FALLBACK | waited={now - cadence_due_since:.2f}s "
                                f"| band={target_band}"
                            )
                    band_candidates = [c for c in eligible if c["band"] == target_band]
                    if band_candidates:
                        best = max(
                            band_candidates,
                            key=lambda c: (
                                c["trajectory_likelihood"],
                                c["band_prior"],
                                -c["bid"],
                            ),
                        )
                        market = best["_market"]
                        state = best["_state"]
                        elapsed = best["_elapsed"]
                        left = best["_left"]
                        signal = strategy.choose_process_candidate([best], target_band)
                        if signal is not None:
                            budget = paper_budget_target(
                                float(signal["target"]),
                                bankroll=strategy.bankroll,
                                cash=ledger.cash,
                                open_exposure=ledger.total_open_cost(),
                                target_scale=PAPER_TARGET_SCALE,
                                max_bet_fraction=MAX_BET_BANKROLL_PCT,
                                max_open_fraction=MAX_OPEN_EXPOSURE_PCT,
                                absolute_open_cap=ABSOLUTE_MAX_TOTAL_EXPOSURE,
                            )
                            notion = budget["spend"]
                            min_fill = MIN_PAPER_FILL_USD
                            if notion >= min_fill and left > strategy.hard_cutoff_seconds:
                                token = market["up"] if signal["side"] == "Up" else market["down"]
                                band, regime = strategy.fine_band(signal["bid"])
                                target = strategy.entry_target(signal["bid"], market["asset"], state["count"])
                                meta = {
                                    "slug": market["slug"], "asset": market["asset"],
                                    "start_ts": market["start_ts"], "end_ts": market["end_ts"],
                                    "market_id": market["id"], "up_token": market["up"],
                                    "down_token": market["down"], "model_version": strategy.VERSION,
                                    "entry_count_before": state["count"],
                                    "burst_position": state["burst_position"],
                                    "seconds_since_first_entry": state["seconds_since_first"],
                                    "seconds_since_previous_trade": state["seconds_since_previous"],
                                    "regime": regime, "fine_band": band,
                                    "execution_mode": "PASSIVE_BID_PROXY",
                                    "target_capital": target,
                                    "paper_model_target": float(signal["target"]),
                                    "paper_scale": PAPER_TARGET_SCALE,
                                    "paper_scaled_target": budget["scaled_target"],
                                    "paper_max_bet": budget["max_bet"],
                                    "paper_open_cap": budget["open_cap"],
                                    "bid_size": signal["depth"] if signal.get("depth") is not None else 0.0,
                                    "trajectory_likelihood": signal["trajectory_likelihood"],
                                }
                                trade = ledger.buy(
                                    market["condition"], token, market["market"], signal["side"],
                                    signal["bid"], notion, now, meta,
                                )
                                bet_no = ledger.total_buy_count()
                                cumulative_spent = ledger.total_spent
                                open_exposure = ledger.total_open_cost()
                                cash_remaining = ledger.cash
                                pending[market["condition"]] = market
                                last_trade[market["condition"]] = now
                                strategy.observe_trade_distribution(band, notion)
                                ledger.save()
                                shares = strategy.distribution_snapshot()
                                p(
                                    f"BET #{bet_no} | TRADE PAPER | asset={market['asset']} "
                                    f"| side={signal['side']} | SPENT=${notion:.2f} "
                                    f"| TOTAL_SPENT=${cumulative_spent:.2f} "
                                    f"| CASH_REMAINING=${cash_remaining:.2f} "
                                    f"| OPEN_EXPOSURE=${open_exposure:.2f} "
                                    f"| EQUITY=${ledger.last_equity:.2f} "
                                    f"| OPEN_EXPOSURE_PCT={(open_exposure / strategy.bankroll * 100.0):.2f}% "
                                    f"| CUMULATIVE_SPEND=${cumulative_spent:.2f} "
                                    f"| MODEL_TARGET=${float(signal['target']):.2f} "
                                    f"| SCALED_TARGET=${budget['scaled_target']:.2f} "
                                    f"| bid=${signal['bid']:.4f} | target=${target:.2f} "
                                    f"| band={band} | regime={regime} | entry_count={state['count']} "
                                    f"| trade_share={shares['trade'].get(band,0.0):.5f} "
                                    f"| capital_share={shares['capital'].get(band,0.0):.5f} "
                                    f"| {signal.get('reason','')}"
                                )
                                research.record_trade(
                                    ts=now, market=market, elapsed=elapsed, left=left,
                                    up_bid=best.get("bid") if best["side"] == "Up" else books.get(market["up"]),
                                    up_ask=best.get("ask"), up_depth=best.get("depth"),
                                    down_bid=best.get("bid") if best["side"] == "Down" else books.get(market["down"]),
                                    down_ask=best.get("ask"), down_depth=best.get("depth"),
                                    trade=trade, score=signal["trajectory_likelihood"], momentum=None,
                                    reason=signal.get("reason",""), cash_after=ledger.cash,
                                    exposure_after=ledger.exposure(market["condition"]),
                                    entry_count_before=state["count"], burst_position=state["burst_position"],
                                    seconds_since_previous=state["seconds_since_previous"],
                                    bet_number=bet_no, cumulative_spent=cumulative_spent,
                                    bankroll_used_pct=(open_exposure / strategy.bankroll * 100.0),
                                    up_history=histories[market["condition"]]["Up"],
                                    down_history=histories[market["condition"]]["Down"],
                                )
                                next_trade_at = now + max(0.0, strategy.cadence.sample_gap())
                                cadence_due_since = None
                                scan_offset += 1
                            elif target_band is not None and fallback_used:
                                next_trade_at = now + max(0.0, strategy.cadence.sample_gap())
                                cadence_due_since = None

            # Never let one cadence event remain permanently due. Once the fallback window
            # has elapsed and no trade was accepted, resample the next trader gap and move on.
            if cadence_due_since is not None and now - cadence_due_since >= CADENCE_FALLBACK_SECONDS:
                if now >= next_trade_at:
                    next_trade_at = now + max(0.0, strategy.cadence.sample_gap())
                    cadence_due_since = None

            # Marking every 250ms would turn the ledger into a constant disk-write path.
            # We mark for the normal report cadence, and immediately before a due trade.
            report(books)

            maintenance_interval = float(
                os.getenv("DATA_MAINTENANCE_SECONDS", "3600")
            )
            if now - last_maintenance >= maintenance_interval:
                research.maintenance()
                last_maintenance = now

            consecutive_errors = 0
            time.sleep(LOOP_SECONDS)

        except KeyboardInterrupt:
            break
        except Exception as exc:
            consecutive_errors += 1
            p(f"LOOP ERROR | {type(exc).__name__}: {exc}")
            traceback.print_exc()
            if consecutive_errors >= 10:
                raise
            time.sleep(2)


if __name__ == "__main__":
    main()
