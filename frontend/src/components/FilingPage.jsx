import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { locateQuote } from "../lib/quote.js";
import { Loop } from "./Pencil.jsx";

/** The line boxes of an inline element, with fragments on the same line
 *  merged and slivers (a lone space at a wrap) dropped. */
function lineRects(el) {
  const rows = [];
  for (const r of el.getClientRects()) {
    if (r.width < 4) continue;
    const row = rows.find((x) => Math.abs(x.top - r.top) < r.height / 2);
    if (row) {
      row.left = Math.min(row.left, r.left); row.right = Math.max(row.right, r.right);
      row.width = row.right - row.left;
    } else {
      rows.push({ left: r.left, right: r.right, top: r.top, width: r.width, height: r.height });
    }
  }
  return rows;
}

/** Non-overlapping [start, end) spans for every quote found on the page. */
function spansFor(text, quotes) {
  const found = quotes.map((q) => locateQuote(text, q)).filter(Boolean)
    .sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const s of found) {
    const last = merged[merged.length - 1];
    if (last && s[0] <= last[1]) last[1] = Math.max(last[1], s[1]);
    else merged.push([...s]);
  }
  return merged;
}

/**
 * A filing page set in the filing's own type, with each cited passage circled
 * in pencil. A loop goes around the union of a passage's line boxes, so a
 * quote that wraps gets one loop rather than a broken outline per line.
 */
export default function FilingPage({ text, quotes = [], seed = "p", animate = true, delay = 0, onFirstMark }) {
  const host = useRef(null);
  const marks = useRef([]);
  const [boxes, setBoxes] = useState([]);
  const body = text || "";
  const spans = useMemo(() => spansFor(body, quotes), [body, quotes]);

  useLayoutEffect(() => {
    if (!host.current) return;
    const measure = () => {
      const h = host.current.getBoundingClientRect();
      // One loop per line the passage occupies. A passage that wraps is
      // circled in pieces, the way a proofreader would, instead of one wide
      // ellipse swallowing both lines whole.
      setBoxes(marks.current.slice(0, spans.length).flatMap((el) => {
        const rects = el ? lineRects(el) : [];
        return rects.map((r) => ({ x: r.left - h.left, y: r.top - h.top, w: r.width, h: r.height }));
      }));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(host.current);
    if (marks.current[0]) onFirstMark?.(marks.current[0]);
    return () => ro.disconnect();
  }, [spans, body]); // eslint-disable-line react-hooks/exhaustive-deps

  const parts = [];
  let at = 0;
  spans.forEach(([s, e], i) => {
    if (s > at) parts.push(body.slice(at, s));
    parts.push(<mark key={i} ref={(el) => { marks.current[i] = el; }}>{body.slice(s, e)}</mark>);
    at = e;
  });
  parts.push(body.slice(at));

  return (
    <div ref={host} style={{ position: "relative" }}>
      <p className="filing-text">{parts}</p>
      {boxes.some(Boolean) && (
        <svg className="pencil-layer" aria-hidden>
          {boxes.map((b, i) => b && (
            <Loop key={i} box={b} seed={`${seed}-${i}`} delay={delay + i * 260} animate={animate} />
          ))}
        </svg>
      )}
    </div>
  );
}
