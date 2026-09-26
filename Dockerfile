# Standard library only, so the runtime image needs no build tooling.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# requirements.txt is intentionally empty of dependencies; copying it first
# keeps the layer cached if a dependency is ever added.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py conversation_handlers.py ./
COPY engine ./engine
COPY dataset ./dataset

EXPOSE 8080

# /v1/healthz is the readiness probe; the service is stateless at boot.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/healthz', timeout=4).status==200 else 1)"

CMD ["python", "bot.py"]
