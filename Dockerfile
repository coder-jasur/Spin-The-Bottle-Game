FROM python:3.13-slim

WORKDIR /app

# yt-dlp audio, ffmpeg
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip uv

COPY pyproject.toml uv.lock /app/

RUN uv pip compile pyproject.toml -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

COPY . /app/

# Tarjima papkasi bo'lsa kompilyatsiya (ixtiyoriy)
RUN if [ -d translations ] && [ -f babel.cfg ]; then pybabel compile -d translations; fi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/favicon.ico', timeout=3)"

CMD ["python", "-m", "src.app.main"]
