import { useLayoutEffect, useRef, useState } from "react";
import { crossPaths, loopPath, strikePath, tickPath, underlinePath } from "../lib/pencil.js";

const reduced = () =>
  typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A path that draws itself on, the way a pencil would, once. */
export function Stroke({ d, delay = 0, thick = false, animate = true }) {
  const ref = useRef(null);
  const [style, setStyle] = useState(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !animate || reduced()) { setStyle(null); return; }
    const len = Math.ceil(el.getTotalLength()) + 2;
    setStyle({ strokeDasharray: len, strokeDashoffset: len, transitionDelay: `${delay}ms` });
    const id = requestAnimationFrame(() => requestAnimationFrame(() =>
      setStyle({ strokeDasharray: len, strokeDashoffset: 0, transitionDelay: `${delay}ms` })));
    return () => cancelAnimationFrame(id);
  }, [d, delay, animate]);
  return <path ref={ref} d={d} className={"pencil draw" + (thick ? " thick" : "")} style={style || undefined} />;
}

export function Loop({ box, seed, delay = 0, animate = true }) {
  return <Stroke d={loopPath(box, seed)} delay={delay} thick animate={animate} />;
}

export function Tick({ size = 18, seed = "t", delay = 0, animate = true, title }) {
  return (
    <svg className="mark-svg" width={size} height={size} viewBox={`0 0 ${size} ${size}`}
         role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <Stroke d={tickPath(size, seed)} delay={delay} thick animate={animate} />
    </svg>
  );
}

export function Cross({ size = 18, seed = "x", delay = 0, animate = true, title }) {
  const [a, b] = crossPaths(size, seed);
  return (
    <svg className="mark-svg" width={size} height={size} viewBox={`0 0 ${size} ${size}`}
         role={title ? "img" : undefined} aria-label={title} aria-hidden={title ? undefined : true}>
      <Stroke d={a} delay={delay} thick animate={animate} />
      <Stroke d={b} delay={delay + 160} thick animate={animate} />
    </svg>
  );
}

/** A small hand-drawn loop, for legends. */
export function LoopMark({ w = 24, h = 14, seed = "lm" }) {
  return (
    <svg className="mark-svg" width={w + 8} height={h + 8} viewBox={`-4 -4 ${w + 8} ${h + 8}`} aria-hidden>
      <Stroke d={loopPath({ x: 4, y: 3, w: w - 8, h: h - 6 }, seed)} animate={false} />
    </svg>
  );
}

/** A pencil line struck through whatever it wraps — the proofreader's delete. */
export function Struck({ children, seed = "s" }) {
  const ref = useRef(null);
  const [lines, setLines] = useState([]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const host = el.getBoundingClientRect();
      setLines([...el.getClientRects()].map((r) => ({
        x: r.left - host.left, y: r.top - host.top, w: r.width, h: r.height })));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [children]);
  return (
    <span ref={ref} style={{ position: "relative" }}>
      {children}
      <svg className="pencil-layer" aria-hidden>
        {lines.map((l, i) => (
          <g key={i} transform={`translate(${l.x},${l.y})`}>
            <Stroke d={strikePath(l.w, l.h, seed + i)} delay={i * 140} />
          </g>
        ))}
      </svg>
    </span>
  );
}

export function Underline({ width = 120, seed = "u" }) {
  return (
    <svg className="mark-svg" width={width} height={8} viewBox={`0 0 ${width} 8`} aria-hidden>
      <Stroke d={underlinePath(width, seed)} />
    </svg>
  );
}
