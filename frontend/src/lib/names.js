// EDGAR names arrive as "JPMORGAN CHASE & CO" or "Apple Inc."; people say
// "JPMorgan Chase" and "Apple". Known filers get their usual name; anyone
// else is title-cased with the legal suffix dropped.
const KNOWN = {
  AAPL: "Apple", MSFT: "Microsoft", NVDA: "NVIDIA", GOOGL: "Alphabet", GOOG: "Alphabet",
  AMZN: "Amazon", META: "Meta", TSLA: "Tesla", JPM: "JPMorgan Chase", V: "Visa",
  JNJ: "Johnson & Johnson", WMT: "Walmart", LLY: "Eli Lilly", NFLX: "Netflix",
};
const SUFFIX = /[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings|group|& co)\.?$/i;

export const tickerOf = (docId) => {
  // doc ids are TICKER-FORM-DATE, and tickers can contain a hyphen (BRK-B)
  const m = /^(.+?)-(10-K|10-Q|8-K|20-F|40-F|DEF 14A|[0-9A-Z]{1,6}-[A-Z])-\d{4}-\d{2}-\d{2}$/.exec(docId || "");
  return m ? m[1] : (docId || "").split("-")[0];
};

export function displayName(filing) {
  const tk = tickerOf(filing?.doc_id || "");
  if (KNOWN[tk]) return KNOWN[tk];
  let n = (filing?.company || filing?.title || tk).replace(/\s+10-K.*$/i, "");
  for (let i = 0; i < 2; i++) n = n.replace(SUFFIX, "");
  if (n === n.toUpperCase()) {
    n = n.toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase());
  }
  return n.trim() || tk;
}

export const possessive = (name) => (name.endsWith("s") ? `${name}'` : `${name}'s`);

export function filedOn(date) {
  if (!date) return "";
  const d = new Date(date + "T00:00:00");
  return isNaN(d) ? date : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
