import { useEffect, useState } from "react";
import { api } from "./api.js";
import { Icon } from "./icons.jsx";
import { NVDA_PAGE, SHELF } from "./lib/snapshot.js";
import Wordmark from "./components/Wordmark.jsx";
import FilingPage from "./components/FilingPage.jsx";
import Shelf from "./components/Shelf.jsx";
import { Cross, Struck, Tick } from "./components/Pencil.jsx";

const REPO = "https://github.com/garvbahl37-gif/recto";

/* The same JPMorgan question, timed against the live service: each stage's
   event arrived at these offsets. */
const RUN = [
  ["Route the question", 0.1, "A lookup, so five pages are enough."],
  ["Pull the pages", 0.1, "Rare words in the question count for more than common ones, and a page that prints “Net income” beside a figure moves to the front. Pages 15, 139, 171, 16 and 118 come back."],
  ["Read them for claims", 5.4, "gpt-oss reads those pages and writes down claims, each carrying a quote copied from its page."],
  ["Check each claim", 7.2, "The quote has to be on the page, character for character. Then a second pass asks whether the claim answers the question at all."],
  ["Write the answer", 8.8, "Only checked claims are used, and every sentence keeps its footnote."],
];

const RESULTS = [
  ["Answer pages retrieved", "0.938", "1.000"],
  ["Claims faithful to their page", "0.000", "1.000"],
  ["Answers correct", "0.812", "1.000"],
  ["Citation lands on the right region", "0.000", "1.000"],
  ["Answers with an invented fact", "0.188", "0.000"],
];

/* NVIDIA's geographic revenue table, as printed on page 69 (in millions). */
const TABLE = [
  ["United States", "149,617", "77,482", "31,533"],
  ["Taiwan", "42,345", "23,600", "14,912"],
  ["China, including Hong Kong", "19,677", "25,048", "12,330"],
  ["Other", "4,299", "4,367", "2,147"],
];

export default function Landing({ isDark, toggleTheme, onOpen }) {
  const [shelf, setShelf] = useState(SHELF);
  useEffect(() => {
    let alive = true;
    api.documents().then((d) => { if (alive && d.documents?.length) setShelf(d.documents); })
      .catch(() => { /* the snapshot stands in while the API sleeps */ });
    return () => { alive = false; };
  }, []);
  const pages = shelf.reduce((a, f) => a + f.pages, 0);
  const open = (e) => { e.preventDefault(); onOpen(); };

  return (
    <div className="lp">
      <header className="lp-head">
        <Wordmark />
        <nav className="lp-nav" aria-label="Sections">
          <a href="#reads">How it reads</a>
          <a href="#marks">The marks</a>
          <a href="#measured">Measured</a>
        </nav>
        <span className="sp" />
        <button className="iconbtn" onClick={toggleTheme} aria-label={isDark ? "Switch to light" : "Switch to dark"}>
          <Icon name={isDark ? "sun" : "moon"} size={17} />
        </button>
        <a className="btn btn-ink" href="/app" onClick={open}>Open Recto</a>
      </header>

      {/* ---------------------------------------------------------------- hero
          An open book. The answer on the left, the page it rests on facing it. */}
      <section className="hero spread" aria-label="Recto, a reader for SEC filings">
        <div className="page verso">
          <div className="hero-verso">
            <div className="running"><span>Recto</span><span>A reader for SEC filings</span></div>
            <h1 className="hero-h">Every figure, traced to its page.</h1>
            <div className="demo">
              <p className="demo-q">What was NVIDIA&rsquo;s total revenue in fiscal 2026?</p>
              <p className="demo-a">
                <span className="demo-tick"><Tick size={22} seed="hero" delay={1700} title="Agreed with its page" /></span>
                $215,938 million.<span className="fn" aria-hidden>1</span>
              </p>
            </div>
            <p className="hero-p">
              Ask a question about a company&rsquo;s annual report. Every figure in the answer comes
              back ticked and footnoted, with its page open beside it and the passage circled. If the
              filing doesn&rsquo;t say it, Recto doesn&rsquo;t write it.
            </p>
            <div className="hero-cta">
              <a className="btn btn-ink" href="/app" onClick={open}>Open Recto</a>
              <a className="btn btn-line" href={REPO} target="_blank" rel="noreferrer">Read the source</a>
            </div>
            <p className="hero-small num">{shelf.length} companies on the shelf, {pages.toLocaleString()} pages. No sign-up.</p>
          </div>
          <span className="folio num">68</span>
        </div>
        <div className="page recto">
          <div className="hero-recto">
            <div className="running"><span>NVIDIA, Form 10-K</span>
              <span className="num">Page {NVDA_PAGE.page_number} of {NVDA_PAGE.total_pages}</span></div>
            <FilingPage text={NVDA_PAGE.text} quotes={[NVDA_PAGE.quote]} seed="hero-nvda" delay={650} />
          </div>
          <span className="folio num">{NVDA_PAGE.page_number}</span>
        </div>
      </section>

      {/* ------------------------------------------------- a page, not a paragraph */}
      <section className="lp-sec" id="pages">
        <div className="sec-grid">
          <h2 className="sec-h">A page, not a paragraph.</h2>
          <p className="sec-p">
            Most retrieval tools cut a filing into paragraphs of text before anything reads it.
            Tables come through that badly: a figure loses its column, and the model has to guess
            which year a number belongs to. Recto keeps the page whole, so a line item and its
            figures stay together.
          </p>
        </div>
        <div className="pair">
          <figure className="sheet">
            <figcaption className="sheet-cap">As NVIDIA printed it, page 69</figcaption>
            <table className="ftable">
              <thead>
                <tr><th>In millions</th><th>Jan 25, 2026</th><th>Jan 26, 2025</th><th>Jan 28, 2024</th></tr>
              </thead>
              <tbody>
                {TABLE.map(([a, b, c, d]) => (
                  <tr key={a}><td>{a}</td><td>{b}</td><td>{c}</td><td>{d}</td></tr>
                ))}
              </tbody>
              <tfoot><tr><td>Total revenue</td><td>215,938</td><td>130,497</td><td>60,922</td></tr></tfoot>
            </table>
          </figure>
          <figure className="sheet">
            <figcaption className="sheet-cap">The same table after text extraction</figcaption>
            <p className="flat">
              Geographic Revenue based upon Customer Headquarters Location (1): (In millions) United
              States $ 149,617 $ 77,482 $ 31,533 Taiwan (2) <mark className="flat-mark">42,345 23,600 14,912</mark>{" "}
              China (including Hong Kong) 19,677 25,048 12,330 Other 4,299 4,367 2,147 Total revenue
              $ 215,938 $ 130,497 $ 60,922
            </p>
            <p className="pencil-note">Which of these is fiscal 2025? The columns are gone, so a
              model has to guess.</p>
          </figure>
        </div>
      </section>

      {/* ----------------------------------------------------------- how it reads */}
      <section className="lp-sec" id="reads">
        <div className="sec-grid">
          <h2 className="sec-h">How Recto reads a question.</h2>
          <p className="sec-p">
            &ldquo;How much net income did JPMorgan report?&rdquo;, timed against the live service.
            Each stage reports back as it finishes, and inside Recto you watch the same list tick
            itself off.
          </p>
        </div>
        <div className="run">
          <div className="run-axis" aria-hidden>
            {[0, 2, 4, 6, 8].map((s) => (
              <span key={s} className="num" style={{ left: `${(s / 9) * 100}%` }}>{s} s</span>
            ))}
          </div>
          <ol className="run-list">
            {RUN.map(([what, t, how]) => (
              <li key={what} className="run-row">
                <div className="run-track" aria-hidden>
                  <span className="run-bar" style={{ width: `${Math.max(0.6, (t / 9) * 100)}%` }} />
                  <span className="run-tick" style={{ left: `${(t / 9) * 100}%` }}>
                    <Tick size={16} seed={"run" + what} animate={false} />
                  </span>
                </div>
                <div className="run-text">
                  <span className="run-what">{what}</span>
                  <span className="run-t num">{t.toFixed(1)} s</span>
                  <span className="run-how">{how}</span>
                </div>
              </li>
            ))}
          </ol>
          <p className="run-result">
            The answer: net income of $57,048 million for 2025 and $58,471 million for 2024, both
            from page 15 of JPMorgan&rsquo;s 10-K.
          </p>
        </div>
      </section>

      {/* -------------------------------------------------------------- the marks */}
      <section className="lp-sec" id="marks">
        <div className="sec-grid">
          <h2 className="sec-h">Two colours, and what they mean.</h2>
          <p className="sec-p">
            Black is the filing&rsquo;s own words. Red is Recto&rsquo;s judgement, made in pencil, so
            you can always tell what the company said from what the machine concluded about it.
          </p>
        </div>
        <dl className="marks">
          <div className="mark-cell sheet">
            <dt><Tick size={64} seed="spec-tick" animate={false} /></dt>
            <dd><b>Agreed.</b> The claim matches the page it cites, figure for figure.</dd>
          </div>
          <div className="mark-cell sheet">
            <dt>
              <span className="spec-loop">
                <FilingPage text="Total revenue $ 215,938" quotes={["215,938"]} seed="spec-loop" animate={false} />
              </span>
            </dt>
            <dd><b>Where it rests.</b> The passage a claim was taken from, circled on the right-hand page.</dd>
          </div>
          <div className="mark-cell sheet">
            <dt><span className="spec-strike"><Struck seed="spec-s">Margins hit a record</Struck></span></dt>
            <dd><b>Left out.</b> A claim that didn&rsquo;t hold up, kept in view so you can see what was rejected.</dd>
          </div>
          <div className="mark-cell sheet">
            <dt><span className="spec-fn">$57,048 million<span className="fn">1</span></span></dt>
            <dd><b>Footnote.</b> Select it and the page turns to the source.</dd>
          </div>
          <div className="mark-cell sheet">
            <dt><Cross size={58} seed="spec-x" animate={false} /></dt>
            <dd><b>Nothing written.</b> No page answers the question, so there is no answer to read.</dd>
          </div>
        </dl>
      </section>

      {/* ---------------------------------------------------------------- refusal */}
      <section className="lp-sec">
        <div className="sec-grid">
          <h2 className="sec-h">It won&rsquo;t write what the filing doesn&rsquo;t say.</h2>
          <p className="sec-p">
            A 10-K doesn&rsquo;t record what a chief executive said on an earnings call. Most assistants
            answer anyway. Recto checks every claim against a page before it writes, so when no page
            supports one, it writes nothing and says so.
          </p>
        </div>
        <div className="refuse sheet">
          <p className="refuse-q">&ldquo;What did the CEO say on the Q3 earnings call?&rdquo;</p>
          <div className="refusal">
            <Cross size={30} seed="lp-refuse" animate={false} />
            <p>Nothing in the pages Recto read answers this, so nothing was written.</p>
          </div>
        </div>
      </section>

      {/* ---------------------------------------------------------------- measured */}
      <section className="lp-sec" id="measured">
        <div className="sec-grid">
          <h2 className="sec-h">Measured against plain text search.</h2>
          <p className="sec-p">
            Sixteen questions, including ones the filings can&rsquo;t answer, run through Recto and
            through a conventional text pipeline on the same documents.
          </p>
        </div>
        <table className="results">
          <thead><tr><th>Measure</th><th>Text search</th><th>Recto</th></tr></thead>
          <tbody>
            {RESULTS.map(([m, a, b]) => (
              <tr key={m}><td>{m}</td><td className="num">{a}</td><td className="num">{b}</td></tr>
            ))}
          </tbody>
        </table>
        <p className="caveat">
          Read these honestly. They come from a small, hand-built set of questions whose answers are
          in the documents, scored by the deterministic local reader, and the text pipeline has no
          citations to score at all. They show the grounding contract holding, not a reader that
          never errs. Reproduce them with <code>make compare</code>.
        </p>
      </section>

      {/* ------------------------------------------------------------------ shelf */}
      <section className="lp-sec">
        <div className="sec-grid">
          <h2 className="sec-h">On the shelf.</h2>
          <p className="sec-p">
            Thirteen annual reports, every page indexed. Inside Recto you can add any company that
            files with the SEC; it pulls the filing from EDGAR and reads all of it.
          </p>
        </div>
        <div className="shelf-wrap sheet">
          <Shelf filings={shelf} active={null} onPick={() => onOpen()} large />
        </div>
        <div className="hero-cta">
          <a className="btn btn-ink" href="/app" onClick={open}>Open Recto</a>
        </div>
      </section>

      {/* ---------------------------------------------------------------- colophon */}
      <footer className="colophon">
        <h2 className="colophon-h">Colophon</h2>
        <p>
          Recto is set in Sofia Sans and Sofia Sans Extra Condensed. Text quoted from filings is set
          in Tinos, which matches the Times New Roman that EDGAR filings use. Judgements are marked
          in pencil, <span className="num">#D62B1E</span>. It reads {shelf.length} annual reports,{" "}
          {pages.toLocaleString()} pages, found by weighted late-interaction matching and read by
          gpt-oss on Ollama Cloud.
        </p>
        <p className="colophon-links">
          <a className="link" href={REPO} target="_blank" rel="noreferrer">Source</a>
          <a className="link" href="https://www.sec.gov/edgar/search/" target="_blank" rel="noreferrer">SEC EDGAR</a>
        </p>
      </footer>
    </div>
  );
}
