from __future__ import annotations


def bounded_float(name: str, default: float, minimum: float, maximum: float | None = None) -> float:
    import os
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def paper_budget_target(
    model_target: float,
    *,
    bankroll: float,
    cash: float,
    open_exposure: float,
    target_scale: float,
    max_bet_fraction: float,
    max_open_fraction: float,
    absolute_open_cap: float,
) -> dict:
    """Apply paper-only bankroll controls without changing model-relative sizing."""
    bankroll = max(0.0, float(bankroll))
    cash = max(0.0, float(cash))
    open_exposure = max(0.0, float(open_exposure))
    model_target = max(0.0, float(model_target))
    target_scale = max(0.0, float(target_scale))
    max_bet_fraction = max(0.0, float(max_bet_fraction))
    max_open_fraction = max(0.0, float(max_open_fraction))
    absolute_open_cap = max(0.0, float(absolute_open_cap))

    scaled_target = model_target * target_scale
    max_bet = bankroll * max_bet_fraction
    open_cap = min(absolute_open_cap, bankroll * max_open_fraction)
    remaining_open = max(0.0, open_cap - open_exposure)
    spend = min(scaled_target, max_bet, cash, remaining_open)
    return {
        "model_target": model_target,
        "scaled_target": scaled_target,
        "max_bet": max_bet,
        "open_cap": open_cap,
        "remaining_open": remaining_open,
        "spend": max(0.0, spend),
    }


def drawdown_halt(equity: float, start_equity: float, max_drawdown_fraction: float) -> bool:
    start_equity = max(0.0, float(start_equity))
    equity = float(equity)
    max_drawdown_fraction = max(0.0, float(max_drawdown_fraction))
    if start_equity <= 0:
        return True
    return equity <= start_equity * (1.0 - max_drawdown_fraction)
