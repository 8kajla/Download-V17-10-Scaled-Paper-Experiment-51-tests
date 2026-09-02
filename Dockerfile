FROM python:3.11-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m compileall -q . && python -m pytest -q
ENV PYTHONUNBUFFERED=1 \
    PAPER_TRADING=true \
    STARTING_CAPITAL=10 \
    MAX_TOTAL_EXPOSURE=10 \
    PAPER_TARGET_SCALE=0.10 \
    MAX_BET_BANKROLL_PCT=0.10 \
    MAX_OPEN_EXPOSURE_PCT=0.50 \
    PAPER_MAX_DRAWDOWN_PCT=0.30 \
    MIN_PAPER_FILL_USD=0.01 \
    LOOP_SECONDS=0.25 \
    DISCOVERY_INTERVAL_SECONDS=5 \
    CADENCE_FALLBACK_SECONDS=2 \
    FRESH_START=false
CMD ["python","bot.py"]
