import { useMemo } from "react";
import { QUESTIONS } from "../lib/questions.js";
import { buildNotes, segment } from "../lib/footnotes.js";
import { displayName, possessive, tickerOf } from "../lib/names.js";
import { Cross, LoopMark, Struck, Tick } from "./Pencil.jsx";

/* ------------------------------------------------------------------ ask */

export function AskSheet({ q, setQ, onAsk, busy, scopeName, filings, onScope, suggest = true }) {
  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onAsk(); }
  };
  const grow = (e) => {
    e.target.style.height = "auto";
    e.target.style.height = Math.min(e.target.scrollHeight, 260) + "px";
  };
  const byTicker = Object.fromEntries(filings.map((f) => [tickerOf(f.doc_id), f]));
  return (
    <form className="ask" onSubmit={(e) => { e.preventDefault(); onAsk(); }}>
      <label htmlFor="question" className="ask-label">
        {scopeName ? `Ask ${possessive(scopeName)} filing` : "Ask the filings"}
      </label>
      <textarea id="question" className="ask-input" rows={2} value={q} spellCheck={false}
                placeholder="Ask about a figure, a risk, or a disclosure"
                onChange={(e) => { setQ(e.target.value); grow(e); }} onKeyDown={onKey} />
      <div className="ask-bar">
        <span className="ask-hint">Return to ask. Shift and Return for a new line.</span>
        <button className="btn btn-ink" type="submit" disabled={busy || !q.trim()}>
          {busy ? "Reading" : "Ask"}
        </button>
      </div>
      {suggest && !busy && (
        <ul className="suggest" aria-label="Questions to try">
          {QUESTIONS.map(({ q: text, doc }) => {
            const f = byTicker[doc];
            return (
              <li key={text}>
                <button type="button" onClick={() => { if (f) onScope(f.doc_id); onAsk(text, f?.doc_id); }}>
                  {text}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </form>
  );
}

/* --------------------------------------------------------------- stages */

const STAGES = [
  ["router", "Route the question"],
  ["retriever", "Pull the pages"],
  ["extractor", "Read them for claims"],
  ["verifier", "Check each claim against its page"],
  ["synthesizer", "Write the answer"],
];

function detail(stage, ev, nameOf) {
  if (!ev) return "";
  switch (stage) {
    case "fetch":
      return ev.stage === "fetch_failed"
        ? `Could not fetch ${tidyName(ev.company)}: ${ev.message}`
        : `${possessive(tidyName(ev.company))} ${ev.form}, ${ev.pages} pages, in ${ev.seconds} s`;
    case "router": {
      const depth = ev.difficulty === "complex"
        ? `needs reasoning across pages, so ${ev.top_k} pages` : `a lookup, so ${ev.top_k} pages`;
      if (ev.scoped?.length) {
        const names = [...new Set(ev.scoped.map(nameOf))];
        return `About ${names.join(" and ")}: ${depth} from ${names.length > 1 ? "their filings" : "its filing"}`;
      }
      return depth.charAt(0).toUpperCase() + depth.slice(1);
    }
    case "retriever": {
      const docs = [...new Set(ev.pages.map((p) => p.doc_id))];
      const nums = ev.pages.map((p) => p.page_number);
      return docs.length === 1
        ? `Pages ${nums.join(", ")} of ${possessive(nameOf(docs[0]))} 10-K`
        : `${ev.pages.length} pages from ${docs.length} filings`;
    }
    case "extractor":
      return ev.claims ? `${ev.claims} candidate claim${ev.claims > 1 ? "s" : ""}` : "Nothing on these pages answers it";
    case "verifier":
      return ev.total ? `${ev.grounded} of ${ev.total} agreed with the page they cite` : "Nothing to check";
    case "synthesizer":
      return `Written by ${ev.model}`;
    default:
      return "";
  }
}

/** The pipeline as it runs. Each stage is ticked off when its event arrives —
 *  real progress from the server, not a timer. */
const tidyName = (s) => (s || "").toLowerCase().replace(/\s*\/.*$/, "")
  .replace(/\b(inc|corp|co|ltd|plc)\.?$/, "").trim().replace(/\b([a-z])/g, (c) => c.toUpperCase());

export function Stages({ events, started, nameOf }) {
  const last = {}, at = {};
  let retries = 0;
  for (const e of events) {
    last[e.stage] = e;
    at[e.stage] = e.t;
    if (e.stage === "retry") retries = e.attempt;
  }
  // A company that is not on the shelf yet gets fetched before anything else.
  const fetching = last.fetch || last.fetched || last.fetch_failed;
  const stages = fetching ? [["fetch", "Fetch the filing from EDGAR"], ...STAGES] : STAGES;
  if (last.fetched || last.fetch_failed) { last.fetch = last.fetched || last.fetch_failed; at.fetch = at.fetched || at.fetch_failed; }
  else if (fetching) delete last.fetch;              // started, not finished
  const doneCount = stages.findIndex(([k]) => !last[k]);
  const current = doneCount === -1 ? stages.length : doneCount;
  return (
    <ol className="stages" aria-live="polite">
      {stages.map(([key, label], i) => {
        const done = !!last[key];
        const live = i === current;
        return (
          <li key={key} className={"stage" + (done ? " done" : live ? " live" : "")}>
            <span className="st-mark">
              {done ? <Tick size={18} seed={"st" + key} title="Done" /> : <span className="st-dot" aria-hidden />}
            </span>
            <span className="st-label">
              {label}
              {key === "retriever" && retries > 0 && <span className="st-retry"> widened once</span>}
            </span>
            <span className="st-detail">{done ? detail(key, last[key], nameOf) : live ? "working" : ""}</span>
            <span className="st-time num">{done ? `${((at[key] - started) / 1000).toFixed(1)} s` : ""}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* --------------------------------------------------------------- answer */

const LEGEND = {
  tick: "Agreed with the page it cites",
  loop: "The passage it rests on, circled on the right-hand page",
  strike: "Left out: did not hold up against its page",
  numeral: "Footnote: select it to turn to that page",
};

export function AnswerSheet({ answer, activeNote, onNote, nameOf }) {
  const { notes, numberOf } = useMemo(() => buildNotes(answer), [answer]);
  const abstained = !answer.citations?.length;
  const claims = answer.claims || [];
  const agreed = new Set(claims.filter((c) => c.verified)
    .flatMap((c) => c.citations.map((x) => `${x.doc_id}#${x.page_number}`)));
  const rejected = claims.filter((c) => c.verified === false);
  const nAgreed = claims.filter((c) => c.verified).length;
  const weak = !abstained && answer.confidence < 0.6;
  const lines = (answer.text || "").split("\n").map((l) => l.trim()).filter(Boolean);

  const marker = (seg, j) => {
    const n = numberOf(seg.docId, seg.page);
    const key = `${seg.docId}#${seg.page}`;
    return (
      <button key={j} type="button" className={"fn" + (activeNote === key ? " on" : "")}
              onClick={() => onNote(key)}
              aria-label={`Source ${n}: ${nameOf(seg.docId)}, page ${seg.page}`}>{n}</button>
    );
  };

  return (
    <article className="answer" aria-live="polite">
      {abstained ? (
        <div className="refusal">
          <Cross size={30} seed="refuse" title="Nothing written" />
          <p>Nothing in the pages Recto read answers this, so nothing was written.
            Try a filing that would report it, or rephrase with the term the filing uses.</p>
        </div>
      ) : (
        <>
          {weak && (
            <p className="margin-note" role="note">
              Weak match. These pages only partly answer the question; read the circled passage
              before relying on it.
            </p>
          )}
          <div className="prose">
            {lines.map((line, i) => {
              const isStatement = line.startsWith("-");
              const segs = segment(line.replace(/^-\s*/, ""));
              const content = segs.map((s, j) => (s.marker ? marker(s, j) : <span key={j}>{s.text}</span>));
              // one lead sentence; any later unbulleted line is plain text
              if (!isStatement) {
                const first = lines.findIndex((l) => !l.startsWith("-")) === i;
                return <p key={i} className={first ? "lede" : "plain"}>{content}</p>;
              }
              const cites = segs.filter((s) => s.marker);
              const ok = cites.length && cites.every((s) => agreed.has(`${s.docId}#${s.page}`));
              return (
                <p key={i} className="stmt">
                  <span className="stmt-mark">
                    {ok ? <Tick size={17} seed={"a" + i} delay={i * 180} title="Agreed with its page" /> : null}
                  </span>
                  {content}
                </p>
              );
            })}
          </div>
        </>
      )}

      {rejected.length > 0 && (
        <div className="left-out">
          <h3 className="sub-h">Left out</h3>
          <ul>
            {rejected.slice(0, 4).map((c, i) => (
              <li key={i}>
                <Struck seed={"r" + i}>{c.text}</Struck>
                <span className="lo-why">{c.verifier_note}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <dl className="imprint">
        <div><dt>Agreed</dt><dd className="num">{nAgreed} of {claims.length}</dd></div>
        <div><dt>Confidence</dt><dd className="num">{abstained ? "none" : answer.confidence.toFixed(2)}</dd></div>
        <div><dt>Read by</dt><dd>{answer.model_used}</dd></div>
        <div><dt>Time</dt><dd className="num">{(answer.latency_ms / 1000).toFixed(1)} s</dd></div>
      </dl>

      {!abstained && (
        <dl className="legend" aria-label="What the marks mean">
          <div><dt><Tick size={16} seed="lg1" animate={false} /></dt><dd>{LEGEND.tick}</dd></div>
          <div><dt><LoopMark seed="legend-loop" /></dt><dd>{LEGEND.loop}</dd></div>
          {rejected.length > 0 && <div><dt><svg className="mark-svg" width="22" height="16" aria-hidden>
            <path d="M1 9 C7 7, 14 10, 21 7" className="pencil" /></svg></dt><dd>{LEGEND.strike}</dd></div>}
          <div><dt><span className="fn" aria-hidden>1</span></dt><dd>{LEGEND.numeral}</dd></div>
        </dl>
      )}
      <p className="trace">Trace {answer.trace_id}</p>
      <span className="vh">{notes.length} source pages</span>
    </article>
  );
}

export const nameOfFactory = (filings) => (docId) => {
  const f = filings.find((x) => x.doc_id === docId);
  return f ? displayName(f) : tickerOf(docId);
};
