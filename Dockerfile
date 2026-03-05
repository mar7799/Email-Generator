FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install Tk runtime dependencies for the UI.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tk \
    tcl \
    libx11-6 \
    libxext6 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root runtime user.
RUN addgroup --system app && adduser --system --ingroup app app

# Copy application source.
COPY email_generator.py /app/email_generator.py
COPY email_daily_scheduler.py /app/email_daily_scheduler.py
COPY email_generator_ui.py /app/email_generator_ui.py

USER app

# Default command shows CLI usage. Override with your full command.
CMD ["python", "email_generator.py", "--help"]
