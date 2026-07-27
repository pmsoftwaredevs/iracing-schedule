# iRacing Calendar — production image.
#
# Runs a single uvicorn process. IMPORTANT: app/scheduler.py starts an in-process
# APScheduler on startup (season/schedule/special-events refresh, rollover emails).
# Do NOT run more than one replica/worker of this image against the same database —
# each instance would run its own scheduler and duplicate emails to users. Scale
# vertically, not horizontally, or move the scheduler out of the request process
# first.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir .

# Both paths are relative-to-cwd defaults baked into the app (app/config.py's
# database_url, app/scheduler.py's SCHEDULE_CACHE_DIR) — kept as separate
# subdirectories, not /app itself, so mounting volumes here never hides the code.
RUN mkdir -p /app/data /app/cache/schedules \
    && useradd --create-home --uid 1000 ircal \
    && chown -R ircal:ircal /app
USER ircal

ENV IRCAL_DATABASE_URL=sqlite:////app/data/iracing_calendar.db

EXPOSE 6337

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:6337/manage || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6337"]
