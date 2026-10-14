import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";
import { tickerOf, tidyName } from "../lib/names.js";
import { Tick } from "./Pencil.jsx";

// Every listed company on EDGAR, fetched once per visit (about 100 KB
// compressed) and searched here, so typing never waits on the network.
let directory = null;
const loadDirectory = () =>
  (directory ||= api.companies().catch((e) => { directory = null; throw e; }));

const fold = (s) => (s || "").toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
  .replace(/[^a-z0-9&]+/g, " ").trim();

/** Rank a company against a folded query: a ticker typed exactly, then names
 *  that begin with it, then names with a word that begins with it. */
function rank(row, q) {
  if (row.tickers.some((t) => t.toLowerCase() === q)) return 0;
  if (row.folded.startsWith(q)) return 1;
  if (row.tickers.some((t) => t.toLowerCase().startsWith(q))) return 2;
  if (row.folded.includes(" " + q)) return 3;
  if (row.folded.includes(q)) return 4;
  return -1;
}

/** A long letter set as a run of short pages, each two columns read down and
 *  headed by its guide words, the way a dictionary does it. One column of 771
 *  names would put "American" beside "A10" and leave the eye scrolling miles
 *  down the left before the right column starts. */
const PAGE = 40;
const pagesOf = (rows) => Array.from({ length: Math.ceil(rows.length / PAGE) },
  (_, i) => rows.slice(i * PAGE, (i + 1) * PAGE));

function Entry({ row, read, onChoose }) {
  const name = tidyName(row.name);
  return (
    <li>
      <button type="button" className={"ix-row" + (read ? " read" : "")} onClick={() => onChoose(row)}
              aria-label={`${name}, ${row.tickers[0]}${read ? ", read by Recto" : ""}`}>
        <span className="ix-name">{name}</span>
        <span className="ix-lead" aria-hidden />
        {read && <Tick size={15} seed={row.tickers[0]} animate={false} />}
        <span className="ix-tk">{row.tickers[0]}</span>
      </button>
    </li>
  );
}

/**
 * The back-of-book index: every company with a ticker that files with the
 * SEC, A to Z, under thumb tabs on the fore-edge. The ones Recto has read are
 * ticked in pencil and open straight onto their filing; any other opens its
 * catalogue card, where a filing can be read.
 */
export function RectoIndex({ filings, onScope, onPick, onClose }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [letter, setLetter] = useState("A");
  const [q, setQ] = useState("");
  const [far, setFar] = useState(null);           // every-filer search: null | "loading" | rows
  const sec = useRef(null);
  const field = useRef(null);

  const fetchList = () => {
    setErr("");
    loadDirectory().then(setData).catch((e) => setErr(e.message));
  };
  useEffect(fetchList, []);
  // Ready to type on a desk; on a phone the keyboard would cover the index.
  useEffect(() => {
    if (matchMedia("(min-width: 901px)").matches) field.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => setFar(null), [q]);

  const onShelf = useMemo(() => {
    const m = new Map();
    filings.forEach((f) => m.set(tickerOf(f.doc_id), f.doc_id));
    return m;
  }, [filings]);
  const readId = (row) => row.tickers.map((t) => onShelf.get(t)).find(Boolean);

  const rows = useMemo(() => (data?.sections || []).flatMap(([L, list]) =>
    list.map(([name, tickers, cik]) => ({ L, name, cik, tickers: tickers.split(" "), folded: fold(name) }))),
  [data]);
  const letters = (data?.sections || []).map(([L, list]) => [L, list.length]);

  const query = fold(q);
  const found = useMemo(() => {
    if (!query) return null;
    const hits = [];
    for (const r of rows) {
      const s = rank(r, query);
      if (s >= 0) hits.push([s, r]);
    }
    hits.sort((a, b) => a[0] - b[0] || a[1].folded.localeCompare(b[1].folded));
    return hits.map(([, r]) => r);
  }, [query, rows]);
  const shown = found ? found.slice(0, 120) : rows.filter((r) => r.L === letter);

  const choose = (row) => {
    const id = readId(row);
    if (id) onScope(id);
    else onPick(row.tickers[0]);
  };
  const turnTo = (L) => {
    setQ(""); setLetter(L);
    // to the letter itself, not back up past the heading and the field
    requestAnimationFrame(() => sec.current?.scrollIntoView({ block: "start",
      behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }));
  };
  const searchFilers = async () => {
    setFar("loading");
    try {
      const r = await api.tickers(q.trim(), 25);
      const listed = new Set(rows.map((x) => x.cik));
      setFar((r.results || []).filter((x) => !listed.has(x.cik)));
    } catch { setFar([]); }
  };
  const onKey = (e) => {
    if (e.key === "Enter" && found?.length) { e.preventDefault(); choose(found[0]); }
    else if (e.key === "Escape") { if (q) setQ(""); else onClose(); }
  };

  const tabs = (cls) => (
    <nav className={cls} aria-label="Letters">
      {letters.map(([L, n]) => (
        <button key={L} type="button" className={"ix-tab" + (!found && L === letter ? " on" : "")}
                aria-pressed={!found && L === letter} aria-label={`${L === "#" ? "Numbers" : L}, ${n} companies`}
                onClick={() => turnTo(L)}>{L}</button>
      ))}
    </nav>
  );

  return (
    <>
      {data && <div className="ix-edge">{tabs("ix-edge-tabs")}</div>}
      <div className="recto-inner ix">
        <div className="running">
          <span>Index of companies</span>
          <button type="button" className="link" onClick={onClose}>Back to the shelf</button>
        </div>
        <h2 className="recto-h">Find any company that files with the SEC.</h2>
        <p className="add-p">
          {data ? `${data.count.toLocaleString()} companies trade under a ticker and file with EDGAR. ` : ""}
          Ticked ones Recto has read; choose any other to read one of its filings.
        </p>
        <label className="field ix-field">
          <Icon name="search" size={16} />
          <span className="vh">Find a company by name or ticker</span>
          <input ref={field} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
                 placeholder="Name or ticker" spellCheck={false} autoComplete="off" />
        </label>
        {data && tabs("ix-rail")}

        {err ? (
          <p className="hint ix-hint">The company list did not load: {err}.{" "}
            <button type="button" className="link" onClick={fetchList}>Try again</button></p>
        ) : !data ? (
          <div className="page-skeleton" aria-label="Loading the index">
            {Array.from({ length: 14 }, (_, i) => <span key={i} style={{ width: `${46 + ((i * 29) % 40)}%` }} />)}
          </div>
        ) : (
          <section aria-live="polite">
            <div className="ix-sec" ref={sec}>
              {found ? (
                <span className="ix-count num">
                  {found.length === 1 ? "1 company matches" : found.length ? `${found.length.toLocaleString()} companies match` : "No listed company matches"}
                  {found.length > shown.length ? `, showing the first ${shown.length}` : ""}
                </span>
              ) : (
                <>
                  <span className="ix-initial" aria-hidden>{letter}</span>
                  <span className="ix-count num">{shown.length.toLocaleString()} companies under {letter === "#" ? "numbers" : letter}</span>
                </>
              )}
            </div>
            {pagesOf(shown).map((block, i) => (
              <div className="ix-page" key={i}>
                {!found && block.length > 1 && (
                  <p className="ix-guide" aria-hidden>
                    <span>{tidyName(block[0].name)}</span><span>{tidyName(block[block.length - 1].name)}</span>
                  </p>
                )}
                <ol className="ix-list">
                  {block.map((r) => <Entry key={r.cik} row={r} read={!!readId(r)} onChoose={choose} />)}
                </ol>
              </div>
            ))}
            {found && query.length >= 3 && (
              <div className="ix-far">
                {far === null && (
                  <button type="button" className="link" onClick={searchFilers}>
                    Search every SEC filer for &ldquo;{q.trim()}&rdquo;, with or without a ticker
                  </button>
                )}
                {far === "loading" && <p className="hint ix-hint">Searching EDGAR</p>}
                {Array.isArray(far) && !far.length && <p className="hint ix-hint">No other filer on EDGAR matches that.</p>}
                {Array.isArray(far) && far.length > 0 && (
                  <>
                    <h3 className="ix-far-h">Other filers on EDGAR</h3>
                    <ol className="ix-list">
                      {far.map((f) => (
                        <li key={f.cik}>
                          <button type="button" className="ix-row" onClick={() => onPick(f.ticker)}>
                            <span className="ix-name">{tidyName(f.title)}</span>
                            <span className="ix-lead" aria-hidden />
                            <span className="ix-tk">{f.ticker.startsWith("CIK") ? `CIK ${f.cik}` : f.ticker}</span>
                          </button>
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </div>
            )}
          </section>
        )}
      </div>
    </>
  );
}
