// EDGAR names arrive as "JPMORGAN CHASE & CO" or "Apple Inc."; people say
// "JPMorgan Chase" and "Apple". Known filers get their usual name; anyone
// else is title-cased with the legal suffix dropped.
const KNOWN = {
  AAPL: "Apple", MSFT: "Microsoft", NVDA: "NVIDIA", GOOGL: "Alphabet", GOOG: "Alphabet",
  AMZN: "Amazon", META: "Meta", TSLA: "Tesla", JPM: "JPMorgan Chase", V: "Visa",
  JNJ: "Johnson & Johnson", WMT: "Walmart", LLY: "Eli Lilly", NFLX: "Netflix",
  ORCL: "Oracle", AVGO: "Broadcom", AMD: "AMD", CRM: "Salesforce", BAC: "Bank of America",
  GS: "Goldman Sachs", MA: "Mastercard", "BRK-B": "Berkshire Hathaway", COST: "Costco",
  KO: "Coca-Cola", PEP: "PepsiCo", PG: "Procter & Gamble", MCD: "McDonald's", NKE: "Nike",
  DIS: "Disney", UNH: "UnitedHealth", CVX: "Chevron", GE: "GE Aerospace", IBM: "IBM",
  INTC: "Intel", SBUX: "Starbucks",
};
const SUFFIX = /[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings|group|& co)\.?$/i;

// Words a title-cased legal name keeps in capitals or in lower case.
const CAPS = new Set(["LLC", "LP", "LLP", "PLC", "NV", "N.V.", "SA", "S.A.", "AG", "SE", "ASA", "AB",
  "USA", "US", "UK", "II", "III", "IV", "VI", "ETF", "REIT", "ADR", "NA"]);
const SMALL = new Set(["of", "and", "the", "de", "del", "della", "dei", "la", "le", "les", "for", "in",
  "on", "du", "des", "di", "da", "van", "von", "der"]);
const FORMS = new Set(["inc", "inc.", "corp", "corp.", "co", "co.", "ltd", "ltd.", "cos"]);

/** "BANK OF AMERICA CORP /DE/" -> "Bank of America Corp". A name EDGAR
 *  already sets in mixed case ("Apple Inc.", "Walt Disney Co") is left as it
 *  is; one mostly in capitals ("ELI LILLY & Co") is recased whole. */
export function tidyName(name) {
  let n = (name || "").trim();
  for (let i = 0; i < 2; i++) n = n.replace(/\s*\/[A-Za-z]{1,6}\/?\s*$/, "");   // /DE/, /NEW, /ADR
  const letters = n.replace(/[^A-Za-z]/g, "");
  if (!letters || letters.replace(/[^A-Z]/g, "").length / letters.length < 0.7) return n;
  return n.toLowerCase().split(/(\s+|-)/).map((w, i) => {
    if (!w.trim() || w === "-") return w;
    const up = w.toUpperCase();
    if (FORMS.has(w)) return w[0].toUpperCase() + w.slice(1);
    if (CAPS.has(up) || /[&\d]/.test(w) || (w.length <= 4 && !/[aeiouy]/.test(w))) return up;
    if (i > 0 && SMALL.has(w)) return w;
    if (/^mc[a-z]{2}/.test(w)) return "Mc" + w[2].toUpperCase() + w.slice(3);
    return w[0].toUpperCase() + w.slice(1);
  }).join("");
}

export const tickerOf = (docId) => {
  // doc ids are TICKER-FORM-DATE, and tickers can contain a hyphen (BRK-B)
  const m = /^(.+?)-(10-K|10-Q|8-K|20-F|40-F|DEF 14A|[0-9A-Z]{1,6}-[A-Z])-\d{4}-\d{2}-\d{2}$/.exec(docId || "");
  return m ? m[1] : (docId || "").split("-")[0];
};

export function displayName(filing) {
  const tk = tickerOf(filing?.doc_id || "");
  if (KNOWN[tk]) return KNOWN[tk];
  let n = tidyName((filing?.company || filing?.title || tk).replace(/\s+10-K.*$/i, ""));
  for (let i = 0; i < 2; i++) n = n.replace(SUFFIX, "");
  return n.trim() || tk;
}

export const possessive = (name) => (name.endsWith("s") ? `${name}'` : `${name}'s`);

export function filedOn(date) {
  if (!date) return "";
  const d = new Date(date + "T00:00:00");
  return isNaN(d) ? date : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}
