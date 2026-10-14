// Turn an answer into a footnote apparatus: every [DOC p.N] in the prose
// becomes a numbered marker, and every cited (filing, page) one note — the
// page that will turn up on the right-hand side.

export const TOKEN = /\[([A-Za-z0-9.\-]+)\s+p\.(\d+)\]/g;

// Display only: tidy a raw snippet into a readable quotation. The raw text is
// kept separately, because the page is searched for it verbatim.
export function cleanQuote(raw) {
  let s = (raw || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  if (s.length > 260) s = s.slice(0, 260).replace(/\s+\S*$/, "") + " …";
  else if (!/[.!?;:)]$/.test(s)) s = s.replace(/\s+\S*$/, "") + " …";
  return s;
}

export function buildNotes(answer) {
  const order = [], byKey = new Map();
  const note = (docId, page) => {
    const key = `${docId}#${page}`;
    if (!byKey.has(key)) {
      const n = { key, n: order.length + 1, docId, page: Number(page), raw: [], quotes: [] };
      byKey.set(key, n);
      order.push(n);
    }
    return byKey.get(key);
  };
  (answer.text || "").split("\n").forEach((line) => {
    for (const m of line.matchAll(TOKEN)) note(m[1], m[2]);
  });
  (answer.citations || []).forEach((c) => {
    const n = note(c.doc_id, c.page_number);
    if (c.snippet && !n.raw.includes(c.snippet)) {
      n.raw.push(c.snippet);
      n.quotes.push(cleanQuote(c.snippet));
    }
  });
  return { notes: order, numberOf: (d, p) => byKey.get(`${d}#${p}`)?.n };
}

/** Split one line of prose into text and citation markers. */
export function segment(line) {
  const out = [];
  let last = 0;
  for (const m of line.matchAll(TOKEN)) {
    if (m.index > last) out.push({ text: line.slice(last, m.index).replace(/\s+$/, "") });
    out.push({ marker: true, docId: m[1], page: Number(m[2]) });
    last = m.index + m[0].length;
  }
  if (last < line.length) out.push({ text: line.slice(last) });
  return out;
}
