# Use Python 3.11 slim as base
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH="/home/app/.local/bin:${PATH}"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pm2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m app
USER app
WORKDIR /home/app

# Copy requirements and install
COPY --chown=app:app backend/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt
RUN pip install --no-cache-dir --user psutil gunicorn

# Copy backend code
COPY --chown=app:app backend/ ./backend/
COPY --chown=app:app auth/ ./auth/
COPY --chown=app:app database/ ./database/
COPY --chown=app:app .env* ./

# Copy system prompt
RUN cp backend/system_prompt.txt . || true

# Expose port
EXPOSE 5000

# Use PM2 to run the Python server
# We'll use a process file or simple command
CMD ["pm2-runtime", "backend/app.py", "--interpreter", "python3"]
