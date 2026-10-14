import { useEffect, useMemo, useRef, useState } from "react";
import { NVDA_PAGE } from "../lib/snapshot.js";
import { loopPath, tickPath, underlinePath } from "../lib/pencil.js";

/*
 * The opening: what Recto does, acted out once.
 *
 * A closed book lies on the pasteboard. The camera rises as the cover swings
 * open, a dozen leaves riffle past and slow, and the book settles on NVIDIA's
 * page 101. The pencil circles "Total revenue $ 215,938" and ticks the margin;
 * then the camera dives into the circled line and the page dissolves into the
 * app. Find the page, mark the figure.
 *
 * It holds for real work and only for real work: on a cold start the reading
 * service takes about a minute to wake, and the book stays open, the pencil
 * reading along line by line, until the filings arrive. Once per session;
 * Skip and Escape always work; under reduced motion it is a still page.
 * Everything animated is transform, opacity or a stroke offset.
 */

const SEEN = "recto.opened";

export function openedThisSession() {
  try { return sessionStorage.getItem(SEEN) === "1"; } catch { return false; }
}
function markOpened() {
  try { sessionStorage.setItem(SEEN, "1"); } catch { /* private mode: it plays again */ }
}
export const prefersStill = () =>
  typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function rng(seed) {
  let h = 2166136261;
  for (const c of String(seed)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The pages the book opens on are the real ones: NVIDIA's 100 and 101.
const LINES = NVDA_PAGE.text.split("\n");
const CUT = Math.max(1, LINES.findIndex((l) => l.startsWith("Revenue by geographic area")));
const VERSO = LINES.slice(0, CUT);
const RECTO = LINES.slice(CUT);

// The leaves that riffle past, by their front folio (each back is the next
// page), and the gaps between them: a thumb letting go fast, then slower.
const FOLIOS = [3, 11, 21, 31, 43, 55, 65, 75, 83, 91, 97, 99];
const GAPS = [0, 36, 36, 38, 41, 45, 52, 61, 73, 89, 112, 146];

const SVG = "http://www.w3.org/2000/svg";

/** A page of type seen too fast to read: paragraphs, a heading, a table. */
function Greek({ seed }) {
  const blocks = useMemo(() => {
    const r = rng(seed);
    const out = [];
    let rows = 0, k = 0;
    while (rows < 28) {
      const roll = r();
      if (roll < 0.15 && rows > 0) {
        out.push(<i key={k++} className="g g-h" style={{ width: `${30 + r() * 30}%` }} />);
        rows += 2;
      } else if (roll < 0.42) {
        const n = 3 + Math.floor(r() * 5);
        for (let i = 0; i < n; i++) {
          out.push(
            <i key={k++} className="g-row">
              <i className="g" style={{ width: `${22 + r() * 24}%` }} />
              <i className="g g-n" /><i className="g g-n" /><i className="g g-n" />
            </i>,
          );
        }
        out.push(<i key={k++} className="g-gap" />);
        rows += n + 1;
      } else {
        const n = 3 + Math.floor(r() * 6);
        for (let i = 0; i < n; i++) {
          out.push(<i key={k++} className="g" style={{ width: i === n - 1 ? `${18 + r() * 62}%` : "100%" }} />);
        }
        out.push(<i key={k++} className="g-gap" />);
        rows += n + 1;
      }
    }
    return out;
  }, [seed]);
  return <div className="fx-copy">{blocks}</div>;
}

/** The filing's own words, a line to a block, each line measurable. */
function Printed({ lines, into }) {
  return (
    <div className="fx-copy fx-print">
      {lines.map((l, i) => (
        <span key={i} className="fx-line">
          <span ref={(el) => { into.current[i] = el; }}>{l}</span>
        </span>
      ))}
    </div>
  );
}

function stroke(svg, d, thick, transform) {
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", d);
  p.setAttribute("class", "pencil" + (thick ? " thick" : ""));
  if (transform) p.setAttribute("transform", transform);
  svg.appendChild(p);
  const len = Math.ceil(p.getTotalLength()) + 2;
  p.style.strokeDasharray = `${len}`;
  p.style.strokeDashoffset = `${len}`;
  return { el: p, len };
}

/**
 * variant "full" plays the whole opening; "wait" opens straight onto the
 * marked page, for a later visit that lands on a sleeping server. `ready`
 * false holds the book open until it turns true.
 */
export default function Opening({ variant = "full", ready = true, onDone }) {
  const still = useMemo(prefersStill, []);
  const [waiting, setWaiting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const root = useRef(null), stage = useRef(null), book = useRef(null), shadow = useRef(null);
  const cover = useRef(null), recto = useRef(null), rectoSvg = useRef(null), versoSvg = useRef(null);
  const leaves = useRef([]);
  const rectoLines = useRef([]), versoLines = useRef([]);
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const wake = useRef(null);
  const skipRef = useRef(() => {});
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  useEffect(() => { if (ready) wake.current?.(); }, [ready]);

  // Nothing behind the book scrolls while it is on the desk.
  useEffect(() => {
    const html = document.documentElement;
    const prev = html.style.overflow;
    html.style.overflow = "hidden";
    return () => { html.style.overflow = prev; };
  }, []);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") skipRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    let alive = true, over = false;
    const anims = [], timers = [];
    const play = (el, frames, opt) => {
      const a = el.animate(frames, { fill: "forwards", ...opt });
      anims.push(a);
      return a;
    };
    const settled = (a) => a.finished.catch(() => {});
    const sleep = (ms) => new Promise((r) => timers.push(setTimeout(r, ms)));
    const gone = () => !alive || over;

    const finish = () => {
      if (!alive) return;
      over = true;
      markOpened();
      doneRef.current?.();
    };
    skipRef.current = () => {
      if (gone()) return;
      over = true;
      anims.forEach((a) => a.cancel());
      const a = root.current.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, fill: "forwards" });
      anims.push(a);
      a.finished.then(() => { if (alive) { markOpened(); doneRef.current?.(); } }, () => {});
    };

    const W = book.current.offsetWidth;
    const N = FOLIOS.length, Z = 0.7;
    const leafZ = (i) => (N - i) * Z;          // the first leaf to turn lies on top
    const coverZ = (N + 1) * Z;
    const shut = (z) => `rotateY(0deg) translateZ(${z}px)`;
    const open = (z) => `rotateY(-180deg) translateZ(${z}px)`;
    // On a portrait phone the spread is small, so the camera ends on the
    // right-hand page alone, filling the width: the recto, by name.
    const portrait = window.innerWidth < 700 && window.innerHeight > window.innerWidth;
    const k = portrait ? Math.min(1.9, (window.innerWidth * 0.9) / (W / 2)) : 1;
    const pose = {
      far: `translate3d(${-W * 0.25}px, ${W * 0.04}px, 0) rotateX(31deg) rotateZ(-4deg) scale(0.8)`,
      open: `translate3d(0px, 0px, 0) rotateX(11deg) rotateZ(0deg) scale(0.95)`,
      flat: `translate3d(${portrait ? -W * 0.25 * k : 0}px, 0px, 0) rotateX(0deg) rotateZ(0deg) scale(${k})`,
    };
    const shades = (el) => [...el.querySelectorAll(":scope > .fx-face > .fx-shade")];

    // Where the pencil goes: measured from layout, which no transform touches.
    const marks = () => {
      const page = recto.current, svg = rectoSvg.current;
      const q = rectoLines.current.find((s) => s && s.textContent.includes(NVDA_PAGE.quote))
        || rectoLines.current[Math.floor(RECTO.length / 2)];
      const box = { x: q.offsetLeft, y: q.offsetTop, w: q.offsetWidth, h: q.offsetHeight };
      const pw = page.offsetWidth;
      const size = Math.max(10, W * 0.017);
      const loop = stroke(svg, loopPath(box, "opening-loop"), true);
      const tick = stroke(svg, tickPath(size, "opening-tick"), true,
                          `translate(${pw - pw * 0.075} ${box.y + box.h / 2 - size * 0.55})`);
      return { loop, tick, quote: q, origin: { x: W / 2 + box.x + box.w / 2, y: box.y + box.h / 2 } };
    };

    // The pencil reading along while the reading service wakes: short lines
    // only, so each underline sits under one line of type.
    const readAlong = (quote) => {
      const pick = (spans, svg) => spans
        .filter((s) => s && s !== quote && s.textContent.length > 8 && s.textContent.length < 64)
        .map((s) => ({ s, svg }));
      const lines = [...pick(rectoLines.current, rectoSvg.current), ...pick(versoLines.current, versoSvg.current)];
      let k = 0;
      const next = () => {
        if (gone() || k >= lines.length) return;
        const { s, svg } = lines[k++];
        const u = stroke(svg, underlinePath(s.offsetWidth, `read-${k}`), false,
                         `translate(${s.offsetLeft} ${s.offsetTop + s.offsetHeight - 2})`);
        if (still) u.el.style.strokeDashoffset = "0";
        else play(u.el, [{ strokeDashoffset: u.len }, { strokeDashoffset: 0 }],
                  { duration: 520, easing: "cubic-bezier(.4,0,.2,1)" });
        timers.push(setTimeout(next, 1700));
      };
      timers.push(setTimeout(next, 500));
    };

    const hold = async (quote) => {
      if (readyRef.current) return;
      setWaiting(true);
      const t0 = Date.now();
      const tick = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
      timers.push(tick);
      readAlong(quote);
      // A minute and a half covers the slowest cold start; past that the app
      // opens anyway and says the service is waking.
      await Promise.race([new Promise((r) => { wake.current = r; if (readyRef.current) r(); }), sleep(95000)]);
      clearInterval(tick);
    };

    // Into the circled line: one transform about the book's centre that ends
    // with the line at the middle of the screen, three and a half times up.
    const dive = async (origin) => {
      const b = book.current;
      const cx = b.offsetLeft + W / 2, cy = b.offsetTop + b.offsetHeight / 2;
      const lx = b.offsetLeft + origin.x, ly = b.offsetTop + origin.y;
      const s = 3.6 * k;
      const tx = window.innerWidth / 2 - cx - s * (lx - cx);
      const ty = window.innerHeight / 2 - cy - s * (ly - cy);
      play(b, [{ transform: pose.flat },
               { transform: `translate3d(${tx}px, ${ty}px, 0) rotateX(0deg) rotateZ(0deg) scale(${s})` }],
           { duration: 900, easing: "cubic-bezier(.65,0,.3,1)" });
      await settled(play(root.current, [{ opacity: 1 }, { opacity: 1, offset: 0.36 }, { opacity: 0 }],
                         { duration: 900, easing: "linear" }));
    };

    // Every part begins where it will rest, or where the opening starts it.
    const lay = (opened) => {
      cover.current.style.transform = opened ? open(coverZ) : shut(coverZ);
      leaves.current.forEach((l, i) => { l.style.transform = opened ? open(leafZ(i)) : shut(leafZ(i)); });
      shadow.current.style.transform = `translateZ(-1px) scaleX(${opened ? 1.99 : 1})`;
      book.current.style.transform = opened ? pose.flat : pose.far;
    };

    const fonts = () => Promise.race([
      Promise.all([
        document.fonts?.load('860 64px "Sofia Sans Extra Condensed Variable"'),
        document.fonts?.load('400 12px "Tinos"'),
        document.fonts?.load('600 14px "Sofia Sans Variable"'),
      ]).catch(() => {}),
      sleep(700),
    ]);

    (async () => {
      const opened = still || variant !== "full";
      lay(opened);
      stage.current.style.opacity = "0";
      await fonts();
      if (gone()) return;
      const m = marks();

      if (still) {
        m.loop.el.style.strokeDashoffset = "0";
        m.tick.el.style.strokeDashoffset = "0";
        stage.current.style.opacity = "1";
        await sleep(readyRef.current ? 900 : 0);
        if (gone()) return;
        await hold(m.quote);
        if (gone()) return;
        await settled(play(root.current, [{ opacity: 1 }, { opacity: 0 }], { duration: 220 }));
        if (!gone()) finish();
        return;
      }

      if (opened) {
        // A later visit on a sleeping server: the book is already open.
        m.loop.el.style.strokeDashoffset = "0";
        m.tick.el.style.strokeDashoffset = "0";
        await settled(play(stage.current, [{ opacity: 0, transform: "scale(.97)" }, { opacity: 1, transform: "scale(1)" }],
                           { duration: 520, easing: "cubic-bezier(.2,.6,.2,1)" }));
        if (gone()) return;
        await hold(m.quote);
        if (gone()) return;
        await dive(m.origin);
        if (!gone()) finish();
        return;
      }

      // 1. The book on the desk, the camera rising over it.
      play(stage.current, [{ opacity: 0 }, { opacity: 1 }], { duration: 600, easing: "cubic-bezier(.2,.6,.2,1)" });
      play(book.current, [{ transform: pose.far }, { transform: pose.open }],
           { delay: 320, duration: 1150, easing: "cubic-bezier(.55,.06,.25,1)" });

      // 2. The cover swings open on its spine, a touch past flat, and settles.
      const coverAt = 380, coverFor = 860;
      play(cover.current, [
        { transform: shut(coverZ) },
        { transform: `rotateY(-105deg) translateZ(${coverZ + 16}px)`, offset: 0.52 },
        { transform: `rotateY(-184deg) translateZ(${coverZ}px)`, offset: 0.86 },
        { transform: open(coverZ) },
      ], { delay: coverAt, duration: coverFor, easing: "cubic-bezier(.5,0,.3,1)" });
      const [cf, cb] = shades(cover.current);
      play(cf, [{ opacity: 0 }, { opacity: 0.55, offset: 0.5 }, { opacity: 0.55 }], { delay: coverAt, duration: coverFor });
      play(cb, [{ opacity: 0.6 }, { opacity: 0.6, offset: 0.5 }, { opacity: 0 }], { delay: coverAt, duration: coverFor });
      // the shadow spreads under the left side only as the cover lands there
      play(shadow.current, [{ transform: "translateZ(-1px) scaleX(1)" }, { transform: "translateZ(-1px) scaleX(1.99)" }],
           { delay: coverAt + coverFor * 0.45, duration: coverFor * 0.55, easing: "cubic-bezier(.3,0,.2,1)" });

      // 3. The riffle: each leaf lifts, turns and lands, a little later and a
      //    little slower than the one before.
      let t = coverAt + 440, end = 0;
      leaves.current.forEach((leaf, i) => {
        t += GAPS[i];
        const d = 400 + i * 20, z = leafZ(i);
        play(leaf, [
          { transform: shut(z) },
          { transform: `rotateY(-90deg) translateZ(${z + 8}px)`, offset: 0.5 },
          { transform: open(z) },
        ], { delay: t, duration: d, easing: "cubic-bezier(.42,.02,.36,1)" });
        const [f, b] = shades(leaf);
        play(f, [{ opacity: 0 }, { opacity: 0.5, offset: 0.5 }, { opacity: 0.5 }], { delay: t, duration: d });
        play(b, [{ opacity: 0.5 }, { opacity: 0.5, offset: 0.5 }, { opacity: 0 }], { delay: t, duration: d });
        end = t + d;
      });

      // 4. The camera comes down flat over the page as the last leaf lands.
      play(book.current, [{ transform: pose.open }, { transform: pose.flat }],
           { delay: end - 640, duration: 820, easing: "cubic-bezier(.3,0,.2,1)" });

      // 5. The pencil: the figure circled, the margin ticked.
      play(m.loop.el, [{ strokeDashoffset: m.loop.len }, { strokeDashoffset: 0 }],
           { delay: end + 140, duration: 640, easing: "cubic-bezier(.5,0,.2,1)" });
      const tickA = play(m.tick.el, [{ strokeDashoffset: m.tick.len }, { strokeDashoffset: 0 }],
                         { delay: end + 820, duration: 260, easing: "cubic-bezier(.4,0,.2,1)" });
      await settled(tickA);
      if (gone()) return;

      await hold(m.quote);
      if (gone()) return;
      await sleep(120);
      if (gone()) return;
      // 6. Into the circled line, and through it to the app.
      await dive(m.origin);
      if (!gone()) finish();
    })();

    return () => {
      alive = false;
      anims.forEach((a) => a.cancel());
      timers.forEach((id) => { clearTimeout(id); clearInterval(id); });
      [rectoSvg.current, versoSvg.current].forEach((svg) => svg && svg.replaceChildren());
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const mins = Math.floor(elapsed / 60), secs = String(elapsed % 60).padStart(2, "0");
  return (
    <div className={"fx" + (still ? " still" : "")} ref={root} role="status" aria-live="polite"
         onClick={(e) => { if (e.target === e.currentTarget) skipRef.current(); }}>
      <span className="vh">{waiting ? "The reading service is waking up." : "Opening Recto."}</span>
      <div className="fx-stage" ref={stage} aria-hidden>
        <div className="fx-book" ref={book}>
          <div className="fx-shadow" ref={shadow} />
          <div className="fx-page fx-right" ref={recto}>
            <Printed lines={RECTO} into={rectoLines} />
            <span className="fx-folio">101</span>
            <svg className="fx-marks" ref={rectoSvg} />
          </div>
          {FOLIOS.map((folio, i) => {
            const last = i === FOLIOS.length - 1;
            return (
              <div key={folio} className="fx-leaf" ref={(el) => { leaves.current[i] = el; }}>
                <div className="fx-face fx-front">
                  <Greek seed={`f${folio}`} />
                  <span className="fx-folio">{folio}</span>
                  <span className="fx-shade" />
                </div>
                <div className="fx-face fx-back">
                  {last ? <Printed lines={VERSO} into={versoLines} /> : <Greek seed={`b${folio}`} />}
                  <span className="fx-folio">{folio + 1}</span>
                  {last && <svg className="fx-marks" ref={versoSvg} />}
                  <span className="fx-shade" />
                </div>
              </div>
            );
          })}
          <div className="fx-cover" ref={cover}>
            <div className="fx-face fx-front fx-board">
              <span className="fx-title">Recto<sup>1</sup></span>
              <span className="fx-note"><sup>1</sup>Every figure, traced to its page.</span>
              <span className="fx-shade" />
            </div>
            <div className="fx-face fx-back fx-endpaper"><span className="fx-shade" /></div>
          </div>
        </div>
      </div>
      <div className={"fx-wait" + (waiting ? " on" : "")}>
        <p className="fx-wait-h">The reading service is waking up
          <span className="fx-wait-t num">{mins}:{secs}</span></p>
        <p className="fx-wait-p">It sleeps when nobody is reading, so the first visit after a quiet
          spell takes about a minute.</p>
      </div>
      <button type="button" className="fx-skip" onClick={() => skipRef.current()}>Skip</button>
    </div>
  );
}
