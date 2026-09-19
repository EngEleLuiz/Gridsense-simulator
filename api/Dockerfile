# Optional container image for the API. For local development you can
# also just run `uvicorn app.main:app --reload` directly with Python
# -- this Dockerfile exists so the API can be deployed the same way
# as everything else in the stack (docker compose --profile api up).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
