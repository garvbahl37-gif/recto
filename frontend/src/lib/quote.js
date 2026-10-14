// Find a quoted span inside a page's text, forgiving the whitespace and
// curly-quote differences between what the reader quoted and what the
// filing printed. Mirrors quote_on_page() on the server.

const norm = (ch) => (ch === "’" || ch === "‘" ? "'"
  : ch === "“" || ch === "”" ? '"' : ch.toLowerCase());

/** -> [start, end) offsets into `text`, or null. */
export function locateQuote(text, quote) {
  if (!text || !quote) return null;
  const idx = [], squashed = [];
  for (let i = 0; i < text.length; i++) {
    if (/\s/.test(text[i])) continue;
    squashed.push(norm(text[i]));
    idx.push(i);
  }
  const page = squashed.join("");
  // quotes may be clipped with an ellipsis; anchor on the longest fragment
  const frags = quote.split(/\.\.\.|…/).map((f) => f.replace(/\s+/g, ""))
    .filter((f) => f.length >= 6).sort((a, b) => b.length - a.length);
  for (const f of frags) {
    const at = page.indexOf([...f].map(norm).join(""));
    if (at >= 0) return [idx[at], idx[at + f.length - 1] + 1];
  }
  return null;
}
