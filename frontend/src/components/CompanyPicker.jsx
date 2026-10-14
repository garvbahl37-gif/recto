import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Icon } from "../icons.jsx";

const tidy = (s) => (s || "").toLowerCase().replace(/\b([a-z])/g, (m) => m.toUpperCase());

/** Search every EDGAR filer. Results that arrive out of order are dropped,
 *  so a slow first request can never overwrite a later one. */
export default function CompanyPicker({ onSelect }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState([]);
  const [hl, setHl] = useState(0);
  const [state, setState] = useState("idle");   // idle | loading | error
  const box = useRef(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const mine = ++seq.current;
    setState("loading");
    const t = setTimeout(async () => {
      try {
        const r = await api.tickers(q);
        if (mine !== seq.current) return;
        setOpts(r.results || []); setHl(0); setState("idle");
      } catch {
        if (mine === seq.current) { setOpts([]); setState("error"); }
      }
    }, q ? 180 : 0);
    return () => clearTimeout(t);
  }, [q, open]);

  useEffect(() => {
    const h = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const choose = (o) => { setOpen(false); setQ(""); onSelect(o.ticker); };
  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setHl((h) => Math.min(h + 1, opts.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHl((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Enter" && open && opts[hl]) { e.preventDefault(); choose(opts[hl]); }
    else if (e.key === "Escape") setOpen(false);
  };

  return (
    <div className="picker" ref={box}>
      <label className="field">
        <Icon name="search" size={16} />
        <span className="vh">Search EDGAR for a company</span>
        <input value={q} placeholder="Search EDGAR by name or ticker" spellCheck={false}
               autoComplete="off" role="combobox" aria-expanded={open} aria-controls="picker-list"
               onChange={(e) => { setQ(e.target.value); setOpen(true); }}
               onFocus={() => setOpen(true)} onKeyDown={onKey} />
      </label>
      {open && (
        <div className="picker-menu" id="picker-list" role="listbox">
          {state === "error" && <div className="hint">EDGAR did not answer. Try again in a moment.</div>}
          {state !== "error" && !opts.length && state !== "loading" &&
            <div className="hint">No filer matches that. Try a ticker such as KO.</div>}
          {opts.map((o, i) => (
            <button key={o.ticker} type="button" role="option" aria-selected={i === hl}
                    className={"row-btn" + (i === hl ? " hl" : "")}
                    onMouseEnter={() => setHl(i)} onClick={() => choose(o)}>
              <span className="r-a">{o.ticker}</span>
              <span className="r-b">{tidy(o.title)}</span>
              {o.indexed && <span className="r-c">On the shelf</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
