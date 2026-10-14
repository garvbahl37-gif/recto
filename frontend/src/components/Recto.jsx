import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { buildNotes } from "../lib/footnotes.js";
import { filedOn, possessive } from "../lib/names.js";
import CompanyPicker from "./CompanyPicker.jsx";
import FilingPage from "./FilingPage.jsx";
import Shelf from "./Shelf.jsx";

/** Pages are fetched one at a time and kept, so turning back is instant. */
const cache = new Map();
export function usePage(docId, n) {
  const key = docId && n ? `${docId}#${n}` : null;
  const [state, setState] = useState(() => (key && cache.get(key)) || null);
  useEffect(() => {
    if (!key) { setState(null); return; }
    if (cache.has(key)) { setState(cache.get(key)); return; }
    let alive = true;
    setState(null);
    api.page(docId, n).then((p) => { cache.set(key, p); if (alive) setState(p); })
      .catch((e) => alive && setState({ error: e.message }));
    return () => { alive = false; };
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  return state;
}

/* ------------------------------------------------------------- the shelf */

export function RectoShelf({ filings, scope, onScope, onPick, onBrowse }) {
  return (
    <div className="recto-inner">
      <div className="running"><span>On the shelf</span><span>Filed with the SEC</span></div>
      <h2 className="recto-h">
        {filings.length ? "Choose a filing, or ask across all of them." : "Nothing read yet."}
      </h2>
      <Shelf filings={filings} active={scope} onPick={onScope} />
      <div className="add">
        <p className="add-p">Recto has read these. Ask about any other company that files with the
          SEC and it fetches that company&rsquo;s annual report from EDGAR and reads it first. To
          choose a particular filing, find the company here.</p>
        <CompanyPicker onSelect={onPick} />
        <p className="add-browse">
          <button type="button" className="link" onClick={onBrowse}>Browse every company, A to Z</button>
        </p>
      </div>
    </div>
  );
}

/* ------------------------------------------------- what is being read now */

function PullRow({ p, nameOf }) {
  const page = usePage(p.doc_id, p.page_number);
  return (
    <li className="pull">
      <span className="pull-where num">{nameOf(p.doc_id)}, p. {p.page_number}</span>
      <span className="pull-text">{page?.text ? page.text.slice(0, 180) + " …" : " "}</span>
    </li>
  );
}

export function RectoReading({ pages, nameOf, done, fetching }) {
  if (fetching && !pages.length) {
    const who = (fetching.company || fetching.ticker || "").toLowerCase()
      .replace(/\s*\/.*$/, "").replace(/\b(inc|corp|co|ltd|plc)\.?$/, "").trim()
      .replace(/\b([a-z])/g, (c) => c.toUpperCase());
    return (
      <div className="recto-inner">
        <div className="running"><span>Not on the shelf yet</span><span>SEC EDGAR</span></div>
        <h2 className="recto-h">Fetching {possessive(who)} annual report from EDGAR.</h2>
        <p className="add-p">Recto reads every page of it before answering. The next question
          about {who} won&rsquo;t need to wait.</p>
      </div>
    );
  }
  return (
    <div className="recto-inner">
      <div className="running"><span>Pulled from the index</span>
        <span className="num">{pages.length ? `${pages.length} pages` : ""}</span></div>
      {pages.length ? (
        <>
          <h2 className="recto-h">{done ? "Read these. None of them answers it."
            : "Reading these pages."}</h2>
          <ol className="pulls">
            {pages.map((p) => <PullRow key={`${p.doc_id}#${p.page_number}`} p={p} nameOf={nameOf} />)}
          </ol>
        </>
      ) : (
        <h2 className="recto-h quiet">Choosing which pages to read.</h2>
      )}
    </div>
  );
}

/* ------------------------------------------------- the source, marked up */

export function RectoSource({ answer, activeNote, onNote, nameOf }) {
  const { notes } = useMemo(() => buildNotes(answer), [answer]);
  const note = notes.find((n) => n.key === activeNote) || notes[0];
  const page = usePage(note?.docId, note?.page);
  const scrollTo = useRef(null);

  // Bring the circled passage into view when the page turns.
  const onFirstMark = (el) => {
    scrollTo.current = el;
    const r = el.getBoundingClientRect();
    if (r.top < 80 || r.bottom > window.innerHeight - 40) {
      el.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  };

  if (!note) return null;
  return (
    <>
      <nav className="thumb-index" aria-label="Source pages">
        {notes.map((n) => (
          <button key={n.key} className={"thumb" + (n.key === note.key ? " on" : "")}
                  onClick={() => onNote(n.key)} aria-current={n.key === note.key ? "page" : undefined}
                  aria-label={`Source ${n.n}: ${nameOf(n.docId)}, page ${n.page}`}>
            {n.n}
          </button>
        ))}
      </nav>
      <div className="recto-inner source" key={note.key}>
        <div className="running">
          <span>{nameOf(note.docId)}{page?.doc_type ? `, ${page.doc_type}` : ""}</span>
          <span className="num">{page ? `Page ${page.page_number} of ${page.total_pages}` : ""}</span>
        </div>
        {page?.error ? (
          <p className="recto-h quiet">This page could not be loaded: {page.error}</p>
        ) : page ? (
          <>
            <FilingPage text={page.text} quotes={note.raw} seed={note.key} onFirstMark={onFirstMark} />
            <p className="source-note">
              <span className="fn" aria-hidden>{note.n}</span>
              Page {page.page_number} of {page.total_pages} in {possessive(nameOf(note.docId))} {page.doc_type}
              {page.date ? `, filed ${filedOn(page.date)}` : ""}.{" "}
              {page.source && <a className="link" href={page.source} target="_blank" rel="noreferrer">Open the filing on EDGAR</a>}
            </p>
          </>
        ) : (
          <div className="page-skeleton" aria-label="Loading the page">
            {Array.from({ length: 18 }, (_, i) => <span key={i} style={{ width: `${88 + ((i * 37) % 12)}%` }} />)}
          </div>
        )}
      </div>
      <span className="folio num">{note.page}</span>
    </>
  );
}
