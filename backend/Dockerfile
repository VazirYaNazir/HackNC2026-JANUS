
FROM python:3.12-slim

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /uvx /bin/

WORKDIR /app

# Configure Python and uv
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Install dependencies using the lockfile
COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev --no-install-project

# Copy the entire backend
COPY . .

# FastAPI port
EXPOSE 8000

# Start the cloud API
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
