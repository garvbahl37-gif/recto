import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { QUESTIONS } from "../lib/questions.js";
import { displayName } from "../lib/names.js";

const tidy = (s) => (s || "").toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());

export default function CommandPalette({ open, onClose, filings, onScope, onAsk, onPick, onTheme }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState([]);
  const [hl, setHl] = useState(0);
  const input = useRef(null);
  const seq = useRef(0);

  useEffect(() => { if (open) { setQ(""); setHl(0); setTimeout(() => input.current?.focus(), 30); } }, [open]);
  useEffect(() => {
    if (!open || q.trim().length < 2) { setHits([]); return; }
    const mine = ++seq.current;
    const t = setTimeout(async () => {
      try {
        const r = await api.tickers(q);
        if (mine === seq.current) setHits((r.results || []).filter((x) => !x.indexed).slice(0, 5));
      } catch { /* palette still works without EDGAR */ }
    }, 180);
    return () => clearTimeout(t);
  }, [q, open]);

  const items = useMemo(() => {
    const ql = q.toLowerCase(), list = [];
    filings.filter((f) => !ql || displayName(f).toLowerCase().includes(ql) || f.doc_id.toLowerCase().includes(ql))
      .forEach((f) => list.push({ group: "Read one filing", a: f.doc_id.split("-")[0], b: displayName(f),
        c: `${f.pages} pages`, run: () => onScope(f.doc_id) }));
    QUESTIONS.filter(({ q: t }) => !ql || t.toLowerCase().includes(ql))
      .forEach(({ q: t }) => list.push({ group: "Ask", a: "Ask", b: t, run: () => onAsk(t) }));
    hits.forEach((h) => list.push({ group: "Add from EDGAR", a: h.ticker, b: tidy(h.title), run: () => onPick(h.ticker) }));
    if (!ql || "theme dark light".includes(ql)) list.push({ group: "View", a: "Theme", b: "Switch between light and dark", run: onTheme });
    return list;
  }, [q, filings, hits]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => setHl(0), [q]);
  if (!open) return null;
  const run = (it) => { onClose(); it.run(); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setHl((h) => Math.min(h + 1, items.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHl((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter" && items[hl]) { e.preventDefault(); run(items[hl]); }
    else if (e.key === "Escape") onClose();
  };
  let last = null;
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="card" role="dialog" aria-modal="true" aria-label="Find">
        <div className="card-head palette-head">
          <label className="field">
            <span className="vh">Find a filing, a question, or a company</span>
            <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}
                   placeholder="Find a filing, a question, or a company on EDGAR" />
            <span className="kbd">Esc</span>
          </label>
        </div>
        <div className="card-body" role="listbox">
          {!items.length && <div className="hint">Nothing matches that.</div>}
          {items.map((it, i) => {
            const head = it.group !== last; last = it.group;
            return (
              <div key={i}>
                {head && <div className="group">{it.group}</div>}
                <button type="button" role="option" aria-selected={i === hl}
                        className={"row-btn" + (i === hl ? " hl" : "")}
                        onMouseEnter={() => setHl(i)} onClick={() => run(it)}>
                  <span className="r-a">{it.a}</span><span className="r-b">{it.b}</span>
                  {it.c && <span className="r-c num">{it.c}</span>}
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
