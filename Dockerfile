FROM python:3.11-slim-bookworm

# Install browsers to a shared path so the non-root runtime user can read them.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .

# OS deps + Chromium are installed as root at build time; the service itself
# runs as an unprivileged user (below). That non-root boundary is what contains
# a hostile page that exploits the renderer — important because Chromium still
# runs with --no-sandbox (the in-container sandbox needs privileges we don't
# grant). Pair this image with a seccomp profile / read-only rootfs in prod.
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /ms-playwright

COPY . .
RUN chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
