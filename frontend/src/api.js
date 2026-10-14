async function j(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let m = r.statusText;
    try { m = (await r.json()).detail || m; } catch {}
    throw new Error(m);
  }
  return r.json();
}
const post = (url, body) =>
  j(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

/**
 * Ask a question and hear about every stage as it happens.
 * Server-Sent Events over a POST, read off the response body — EventSource
 * only does GET. Falls back to the plain endpoint if the stream breaks.
 */
async function ask(question, docIds, onStage, signal) {
  const body = JSON.stringify({ question, doc_ids: docIds });
  try {
    const r = await fetch("/query/stream", {
      method: "POST", headers: { "Content-Type": "application/json" }, body, signal,
    });
    if (!r.ok || !r.body) throw new Error(`stream ${r.status}`);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let cut;
      while ((cut = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, cut);
        buf = buf.slice(cut + 2);
        const data = block.split("\n").filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trim()).join("");
        if (!data) continue;              // heartbeat comment
        const ev = JSON.parse(data);
        if (ev.stage === "error") throw new Error(ev.message);
        if (ev.stage === "answer") return ev.answer;
        onStage?.(ev);
      }
    }
    throw new Error("stream ended without an answer");
  } catch (err) {
    if (signal?.aborted) throw err;
    return post("/query", { question, doc_ids: docIds });   // still answer
  }
}

export const api = {
  health: () => j("/health"),
  documents: () => j("/documents"),
  page: (docId, n) => j(`/documents/${encodeURIComponent(docId)}/pages/${n}`),
  tickers: (q, limit = 10) => j("/tickers?q=" + encodeURIComponent(q) + "&limit=" + limit),
  companies: () => j("/companies"),
  company: (ticker, forms = "", limit = 40) =>
    j(`/company?ticker=${encodeURIComponent(ticker)}&forms=${encodeURIComponent(forms)}&limit=${limit}`),
  ask,
  ingestFiling: (payload) => post("/ingest", payload),
};
