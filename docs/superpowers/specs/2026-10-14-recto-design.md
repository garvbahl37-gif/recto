# Recto — rebrand, redesign, and a real reader

Date: 2026-10-14 · Status: approved (name and direction chosen by the owner)

## Intent

InsightLedger reads SEC filings and answers questions with every claim traced
to the page region it came from, abstaining when the filing does not support
an answer. The owner wants:

1. A distinctive name. **Chosen: Recto** — the right-hand page of a book;
   Latin *rectus*, "right, correct".
2. A frontend that reads as authored, not generated. **Chosen direction: the
   marked-up proof.**
3. Real answer synthesis through an Ollama Cloud key (Kimi preferred; best
   available model otherwise).
4. More companies available on the deployed app, with whole filings parsed.

## Design direction — the marked-up proof

The product reads *pages*, so the interface shows pages.

- **One colour rule.** Black ink is the filing's own words. Red pencil is the
  machine's judgement — ticks, circles, footnote numerals, abstentions. Nothing
  else in the interface is red.
- **Facsimile pages.** Each cited source renders as a miniature page typeset
  from the page's real text, with the cited passage circled in a hand-drawn
  red-pencil stroke. This is the one orchestrated motion: the circle draws
  itself on when an answer lands. Honest about what it is — a typeset
  reconstruction of the page text, not a scan.
- **Tick-mark legend.** Answers end with the legend every audit workpaper
  carries, explaining what each mark means.
- **Type.** A variable grotesque with a width axis: compressed and very large
  for display, normal width for interface text. A Times-metric serif only for
  text quoted from filings, because that is how EDGAR filings are set.
- **Paper on a desk.** Sheets are square-cornered paper on a quiet desk
  surface; no rounded-card kit, no gradients, no glow.
- **Avoid** the known generated-UI tells: cream + serif + terracotta,
  near-black + single neon accent, all-caps eyebrow labels, middot meta
  strings, decorative numbering, arrows appended to links.

## Reader — Ollama Cloud provider

- Kimi models return `402 Payment Required` on the provided key's free tier.
  Best available: `gpt-oss:120b` (strong, ~17 s/call) and `gpt-oss:20b`
  (fast, ~4 s/call), via the OpenAI-compatible `https://ollama.com/v1`.
- Matches the existing router: lookups → fast model, reasoning → strong model.
  Both model names are env vars, so Kimi is a config change once credits exist.
- Three calls per question at most: extract, verify (batched — one call for all
  claims, not one per claim), synthesize. The verifier is told to judge both
  support *and* responsiveness, as the stub now does.
- Any provider failure falls back to the deterministic stub, so the app never
  hard-fails on the network.
- The key lives only in environment variables (local `.env`, Render env). It is
  never committed.

## Corpus

- Fix: the stub embedder capped every page at 512 tokens, so the tail of each
  page was unsearchable. Deduplicate document tokens instead — lossless for
  MaxSim, smaller, and covers the whole page.
- Fix: `ensure_live_index` returned early whenever any index existed, so new
  seed tickers were never ingested.
- Seed roughly ten large filers, baked into the Docker image at build time so
  cold starts load from disk instead of re-fetching EDGAR. Measure memory
  against Render's 512 MB before choosing the final list.

## Verification

Unit tests (provider with mocked HTTP, embedder dedupe, bootstrap), the eval
gate, screenshots in light, dark and mobile, zero console errors, and a
production query answered by the live model.
