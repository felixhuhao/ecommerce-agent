# Sandbox executor service (design doc §6.2/§6.6).
#
# Extends the sandbox image (python + pandas/numpy + ecommerce_analysis) and
# adds the HTTP service layer + bubblewrap. Built AFTER the sandbox image:
#   docker build -t ecommerce-agent-sandbox:dev sandbox_image/
#   docker compose -f docker-compose.yml -f compose.chart-mcp.yml \
#     -f compose.sandbox.yml build sandbox-executor
FROM ecommerce-agent-sandbox:dev

# CAP_SYS_ADMIN (granted via compose) must be in the effective set, so the
# service runs as root; bwrap creates a per-execute mount namespace. This is
# the probe-validated privilege posture for trusted analytical code (§10).
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30"

WORKDIR /app
COPY app.py __init__.py /app/

RUN mkdir -p /workspaces

ENV WORKSPACES_DIR=/workspaces \
    PYTHONPATH=/opt/ecommerce_analysis

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]
