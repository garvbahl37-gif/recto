import { displayName, tickerOf } from "../lib/names.js";

/**
 * The indexed filings as books on a shelf. A spine's thickness is its page
 * count, so JPMorgan's 172 pages stand visibly fatter than Apple's 56.
 */
export default function Shelf({ filings, active, onPick, large = false }) {
  if (!filings.length) return null;
  const pages = filings.map((f) => f.pages);
  const lo = Math.min(...pages), hi = Math.max(...pages);
  const [w0, w1] = large ? [46, 104] : [24, 54];
  const width = (p) => Math.round(w0 + ((p - lo) / Math.max(1, hi - lo)) * (w1 - w0));
  const total = pages.reduce((a, b) => a + b, 0);
  return (
    <div>
      <div className={"shelf" + (large ? " large" : "")} role="group" aria-label="Indexed filings">
        {filings.map((f) => {
          const on = active === f.doc_id;
          const name = displayName(f);
          return (
            <button key={f.doc_id} className={"spine" + (on ? " on" : "")}
                    style={{ width: width(f.pages) }} aria-pressed={on}
                    onClick={() => onPick(on ? null : f.doc_id)}
                    title={`${name}, ${f.doc_type}, ${f.pages} pages`}
                    aria-label={`${name}, ${f.doc_type}, ${f.pages} pages${on ? ", selected" : ""}`}>
              <span className="sp-name">{name}</span>
              <span className="sp-tk">{tickerOf(f.doc_id)}</span>
            </button>
          );
        })}
      </div>
      <div className="shelf-legend">
        <span>Thicker spines have more pages</span>
        <span className="num">{filings.length} filings, {total.toLocaleString()} pages</span>
      </div>
    </div>
  );
}
