FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application source & Alembic configuration
COPY src/ ./src/
COPY alembic.ini .

ENV PYTHONPATH=/app

CMD ["python", "src/main.py"]
