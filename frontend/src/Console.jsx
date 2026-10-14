import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import { buildNotes } from "./lib/footnotes.js";
import { displayName, possessive } from "./lib/names.js";
import { Icon } from "./icons.jsx";
import Wordmark from "./components/Wordmark.jsx";
import { AnswerSheet, AskSheet, Stages, nameOfFactory } from "./components/Verso.jsx";
import { RectoReading, RectoShelf, RectoSource } from "./components/Recto.jsx";
import { RectoIndex } from "./components/CompanyIndex.jsx";
import CommandPalette from "./components/CommandPalette.jsx";
import CompanyModal from "./components/CompanyModal.jsx";
import Opening, { openedThisSession, prefersStill } from "./components/Opening.jsx";

export default function Console({ isDark, toggleTheme }) {
  const [filings, setFilings] = useState([]);
  const [loadErr, setLoadErr] = useState("");
  const [scope, setScope] = useState(null);
  const [q, setQ] = useState("");
  const [phase, setPhase] = useState("idle");          // idle | reading | done
  const [events, setEvents] = useState([]);
  const [started, setStarted] = useState(0);
  const [pulled, setPulled] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [activeNote, setActiveNote] = useState(null);
  const [showShelf, setShowShelf] = useState(false);
  const [showIndex, setShowIndex] = useState(false);
  const [view, setView] = useState("verso");           // narrow screens: which page
  const [cmd, setCmd] = useState(false);
  const [modal, setModal] = useState(null);
  const [toast, setToast] = useState(null);
  const inflight = useRef(null);
  const fetchedNow = useRef(false);

  const flash = (msg, err) => { setToast({ msg, err }); setTimeout(() => setToast(null), 3400); };
  const load = useCallback(async () => {
    try { setFilings((await api.documents()).documents); setLoadErr(""); }
    catch (e) { setLoadErr(e.message); }
  }, []);

  // The first load waits out a cold start: the reading service sleeps on the
  // free tier and takes about a minute to answer, so keep asking until it does.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      const t0 = Date.now();
      for (;;) {
        try {
          const d = await api.documents();
          if (!alive) return;
          setFilings(d.documents); setLoadErr("");
          break;
        } catch (e) {
          if (!alive) return;
          setLoadErr(e.message);
          if (Date.now() - t0 > 95000) break;
          await new Promise((r) => setTimeout(r, 2500));
        }
      }
      if (alive) setReady(true);
    })();
    return () => { alive = false; };
  }, []);

  // A first visit gets the whole opening. A later one, or anyone who asked
  // for less motion, gets it only if the service is still asleep after a
  // moment, opened straight onto the page.
  const [opening, setOpening] = useState(() =>
    (openedThisSession() || prefersStill() ? "pending" : "full"));
  useEffect(() => {
    if (opening !== "pending") return;
    if (ready) { setOpening("off"); return; }
    const t = setTimeout(() => setOpening((o) => (o === "pending" ? "wait" : o)), 900);
    return () => clearTimeout(t);
  }, [opening, ready]);

  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmd((o) => !o); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const nameOf = useMemo(() => nameOfFactory(filings), [filings]);
  const scoped = filings.find((f) => f.doc_id === scope);
  const scopeName = scoped ? displayName(scoped) : null;

  const ask = async (text, docOverride) => {
    const question = (typeof text === "string" ? text : q).trim();
    if (!question) return;
    inflight.current?.abort();
    const ac = new AbortController();
    inflight.current = ac;
    const docIds = docOverride !== undefined ? (docOverride ? [docOverride] : null)
      : scope ? [scope] : null;
    const t0 = performance.now();
    setQ(question); setPhase("reading"); setEvents([]); setPulled([]); setAnswer(null);
    setActiveNote(null); setShowShelf(false); setShowIndex(false); setStarted(t0); setView("verso");
    try {
      const a = await api.ask(question, docIds, (ev) => {
        const e = { ...ev, t: performance.now() };
        setEvents((es) => [...es, e]);
        if (ev.stage === "retriever") setPulled(ev.pages);
        if (ev.stage === "fetched") fetchedNow.current = true;
      }, ac.signal);
      if (ac.signal.aborted) return;
      // a filing fetched for this question joins the shelf before the answer
      // lands, so its name and page links resolve
      if (fetchedNow.current) { fetchedNow.current = false; await load(); }
      setAnswer(a);
      setPhase("done");
      setActiveNote(buildNotes(a).notes[0]?.key || null);
    } catch (e) {
      if (ac.signal.aborted) return;
      setPhase("idle");
      flash(`The question could not be read: ${e.message}`, true);
    }
  };

  const openNote = (key) => { setActiveNote(key); setShowShelf(false); setShowIndex(false); setView("recto"); };
  const pickScope = (docId) => { setScope(docId); setShowShelf(false); setShowIndex(false); };
  const openIndex = () => { setShowIndex(true); setView("recto"); };

  let recto;
  if (showIndex) {
    recto = <RectoIndex filings={filings} onScope={(id) => { pickScope(id); setView("verso"); }}
                        onPick={setModal} onClose={() => setShowIndex(false)} />;
  } else if (showShelf || phase === "idle") {
    recto = <RectoShelf filings={filings} scope={scope} onScope={pickScope} onPick={setModal}
                        onBrowse={openIndex} />;
  } else if (phase === "reading") {
    const f = [...events].reverse().find((e) => e.stage === "fetch" || e.stage === "fetched");
    recto = <RectoReading pages={pulled} nameOf={nameOf} fetching={f?.stage === "fetch" ? f : null} />;
  } else if (answer?.citations?.length) {
    recto = <RectoSource answer={answer} activeNote={activeNote} onNote={openNote} nameOf={nameOf} />;
  } else {
    const read = (answer?.retrieved || []).map((p) => ({ doc_id: p.doc_id, page_number: p.page_number }));
    recto = <RectoReading pages={read} nameOf={nameOf} done />;
  }

  return (
    <div className="desk">
      {(opening === "full" || opening === "wait") && (
        <Opening variant={opening} ready={ready} onDone={() => setOpening("off")} />
      )}
      <header className="desk-head">
        <Wordmark href="/" />
        <button className="scope" onClick={() => { setShowShelf((s) => !s); setShowIndex(false); setView("recto"); }}
                aria-expanded={showShelf}>
          {scopeName ? `Reading ${possessive(scopeName)} ${scoped.doc_type}` : `Reading all ${filings.length} filings`}
        </button>
        {scope && <button className="link scope-clear" onClick={() => setScope(null)}>Read all</button>}
        <span className="sp" />
        <button className="find" onClick={() => setCmd(true)}>
          <Icon name="search" size={15} /> Find <span className="kbd">⌘K</span>
        </button>
        <button className="iconbtn" onClick={toggleTheme} aria-label={isDark ? "Switch to light" : "Switch to dark"}>
          <Icon name={isDark ? "sun" : "moon"} size={17} />
        </button>
      </header>

      {loadErr && (
        <p className="wake" role="status">
          The reading service is waking up, which takes about a minute on the free tier.{" "}
          <button className="link" onClick={load}>Try again</button>
        </p>
      )}

      <div className="flip" role="tablist" aria-label="Page">
        <button role="tab" aria-selected={view === "verso"} className={view === "verso" ? "on" : ""}
                onClick={() => setView("verso")}>Question</button>
        <button role="tab" aria-selected={view === "recto"} className={view === "recto" ? "on" : ""}
                onClick={() => setView("recto")}>Pages</button>
      </div>

      <main className={"spread desk-spread show-" + view}>
        <section className="page verso" aria-label="Question and answer">
          <div className="verso-inner">
            <div className="running">
              <span>Recto</span>
              <span>{phase === "reading" ? "Reading" : phase === "done" ? "Answered" : "Ask"}</span>
            </div>
            <AskSheet q={q} setQ={setQ} onAsk={ask} busy={phase === "reading"}
                      scopeName={scopeName} filings={filings} onScope={setScope}
                      suggest={phase === "idle"} />
            {phase === "reading" && <Stages events={events} started={started} nameOf={nameOf} />}
            {phase === "done" && answer && (
              <AnswerSheet answer={answer} activeNote={activeNote} onNote={openNote} nameOf={nameOf} />
            )}
          </div>
        </section>
        <section className="page recto" aria-label="Source pages">{recto}</section>
      </main>

      <CommandPalette open={cmd} onClose={() => setCmd(false)} filings={filings}
                      onScope={pickScope} onAsk={(t) => ask(t)} onPick={setModal} onTheme={toggleTheme}
                      onBrowse={openIndex} />
      <CompanyModal ticker={modal} onClose={() => setModal(null)}
                    onRead={(id) => { pickScope(id); setView("verso"); }}
                    onIndexed={async (r) => { flash(`${displayName({ doc_id: r.doc_id, title: r.title })}: read all ${r.pages} pages`); await load(); }} />
      {toast && <div className={"toast" + (toast.err ? " err" : "")} role="status">{toast.msg}</div>}
    </div>
  );
}

