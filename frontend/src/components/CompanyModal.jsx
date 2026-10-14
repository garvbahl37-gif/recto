import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { filedOn } from "../lib/names.js";

const FORM_SETS = { Annual: "10-K,20-F,40-F", Quarterly: "10-Q", Events: "8-K", Proxy: "DEF 14A", All: "" };
const tidy = (s) => (s || "").toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());

/** A catalogue card for one company: its filings, any of which can be indexed. */
export default function CompanyModal({ ticker, onClose, onIndexed, onRead }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState("Annual");
  const [busy, setBusy] = useState(null);
  const closeRef = useRef(null);

  useEffect(() => {
    if (!ticker) return;
    setData(null); setErr("");
    let alive = true;
    api.company(ticker, FORM_SETS[tab], 40).then((d) => alive && setData(d))
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [ticker, tab]);

  useEffect(() => {
    if (!ticker) return;
    closeRef.current?.focus();
    const h = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [ticker, onClose]);

  if (!ticker) return null;

  const index = async (f) => {
    setBusy(f.accession); setErr("");
    try {
      const r = await api.ingestFiling({ ticker, form: f.form, accession: f.accession,
                                         primary_doc: f.primary_doc, date: f.date });
      onIndexed?.(r);
      setData((d) => ({ ...d, filings: d.filings.map((x) =>
        x.accession === f.accession ? { ...x, indexed: true, doc_id: r.doc_id } : x) }));
    } catch (e) { setErr(`Indexing failed: ${e.message}`); }
    finally { setBusy(null); }
  };

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card" role="dialog" aria-modal="true" aria-labelledby="cm-title">
        <div className="card-head">
          <div className="running"><span>{ticker}</span>
            <button ref={closeRef} className="link" onClick={onClose}>Close</button></div>
          <h2 className="card-title" id="cm-title">{data ? tidy(data.name) : ticker}</h2>
          <div className="card-sub">{data ? `${data.industry || "Industry not listed"}, CIK ${data.cik}` : "Looking it up on EDGAR"}</div>
        </div>
        <div className="tabs" role="tablist">
          {Object.keys(FORM_SETS).map((k) => (
            <button key={k} role="tab" aria-selected={tab === k}
                    className={"tab" + (tab === k ? " on" : "")} onClick={() => setTab(k)}>{k}</button>
          ))}
        </div>
        <div className="card-body">
          {err && <div className="hint err-text">{err}</div>}
          {!data && !err && <div className="hint">Loading filings</div>}
          {data && !data.filings.length && <div className="hint">No {tab.toLowerCase()} filings on record.</div>}
          {data?.filings.map((f) => (
            <div key={f.accession} className="row-btn static">
              <span className="r-a">{f.form}</span>
              <span className="r-b num">{filedOn(f.date)}{f.description && f.description !== f.form ? `, ${f.description}` : ""}</span>
              {f.indexed ? (
                <button className="link" onClick={() => { onRead?.(f.doc_id); onClose(); }}>Ask it</button>
              ) : busy === f.accession ? (
                <span className="r-c">Indexing</span>
              ) : (
                <button className="link" disabled={!!busy} onClick={() => index(f)}>Index</button>
              )}
            </div>
          ))}
        </div>
        <div className="card-foot">Straight from SEC EDGAR. Indexing reads every page of the filing.</div>
      </div>
    </div>
  );
}
