FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src IL_DATA_DIR=/app/data

# core deps only — the image runs the full system on stub backends.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src

# Ingest the seed filings from EDGAR at build time and bake the index into the
# image, with EDGAR's list of every filer. A cold start then loads the index
# from disk in about a second instead of re-fetching thirty 10-Ks.
ENV IL_BOOTSTRAP=live \
    IL_SEED_TICKERS=AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,NFLX,ORCL,AVGO,AMD,CRM,JPM,BAC,GS,V,MA,BRK-B,WMT,COST,KO,PEP,PG,MCD,NKE,DIS,JNJ,LLY,UNH,CVX \
    IL_EDGAR_USER_AGENT="Recto research garvbahl37@gmail.com"
RUN python -m insightledger.cli bootstrap && \
    python -c "from insightledger.eval.golden_set import write_golden; write_golden()"

# At run time, filings fetched for a question live in memory only: the disk
# resets to this image on every restart, so writing them out was pure cost.
ENV IL_PERSIST_FETCHED=0

EXPOSE 8000
CMD ["python", "-m", "insightledger.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
