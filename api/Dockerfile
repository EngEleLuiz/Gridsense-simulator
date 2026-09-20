# Optional container image for the API. For local development you can
# also just run `uvicorn app.main:app --reload` directly with Python
# -- this Dockerfile exists so the API can be deployed the same way
# as everything else in the stack (docker compose --profile api up).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Run as a non-root user. Irrelevant to the current risk (local-only,
# not exposed to the internet), but standard hardening practice and
# free to do -- if this ever moves beyond localhost, the container
# doesn't already need a "run as root" habit unlearned.
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
