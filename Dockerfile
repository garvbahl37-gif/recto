FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src IL_DATA_DIR=/app/data

# core deps only — the image runs the full system on stub backends.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src

# Ingest the seed filings from EDGAR at build time and bake the index into the
# image. A cold start then loads ~35 MB of vectors from disk in under a second
# instead of re-fetching thirteen 10-Ks over the network.
ENV IL_BOOTSTRAP=live \
    IL_SEED_TICKERS=AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,JPM,V,JNJ,WMT,LLY,NFLX \
    IL_EDGAR_USER_AGENT="Recto research garvbahl37@gmail.com"
RUN python -m insightledger.cli bootstrap && \
    python -c "from insightledger.eval.golden_set import write_golden; write_golden()"

EXPOSE 8000
CMD ["python", "-m", "insightledger.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
