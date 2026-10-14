// Hand-drawn pencil strokes, generated rather than drawn once and reused, so
// every mark is a little different — the way a person's pencil is.
// Deterministic per seed: the same quote always gets the same loop.

function rng(seedText) {
  let h = 2166136261;
  for (const c of String(seedText)) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Catmull-Rom through the points, as cubic Béziers: smooth, but it still
// wobbles wherever the points do.
function smooth(pts) {
  let d = `M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

/** A loose loop around a box, overshooting where it started, like a circled
 *  passage on a proof. */
export function loopPath({ x, y, w, h }, seed = "loop") {
  const r = rng(seed);
  const cx = x + w / 2, cy = y + h / 2;
  const rx = w / 2 + Math.min(14, Math.max(7, w * 0.04)), ry = h / 2 + Math.min(9, Math.max(4.5, h * 0.3));
  const start = -Math.PI * (0.62 + r() * 0.2);
  const sweep = Math.PI * 2 + 0.38 + r() * 0.3;      // past the start: overshoot
  const tilt = (r() - 0.5) * 0.05;
  const ph1 = r() * 6.28, ph2 = r() * 6.28;
  const n = 46, pts = [];
  for (let i = 0; i <= n; i++) {
    const t = start + (sweep * i) / n;
    // low-frequency wobble, and the second lap sits a little outside the first
    const wob = 1 + 0.035 * Math.sin(t * 2 + ph1) + 0.022 * Math.sin(t * 3 + ph2) + 0.03 * (i / n);
    const px = rx * wob * Math.cos(t), py = ry * wob * Math.sin(t);
    pts.push([cx + px * Math.cos(tilt) - py * Math.sin(tilt), cy + px * Math.sin(tilt) + py * Math.cos(tilt)]);
  }
  return smooth(pts);
}

/** An auditor's tick: a short down-stroke, then a longer flick up and right. */
export function tickPath(size = 16, seed = "tick") {
  const r = rng(seed), s = size;
  const a = [s * (0.08 + r() * 0.06), s * (0.5 + r() * 0.08)];
  const b = [s * (0.36 + r() * 0.05), s * (0.86 + r() * 0.05)];
  const c = [s * (0.94 + r() * 0.05), s * (0.06 + r() * 0.08)];
  const mid1 = [(a[0] + b[0]) / 2 + (r() - 0.5) * 1.2, (a[1] + b[1]) / 2];
  const mid2 = [(b[0] + c[0]) / 2 + (r() - 0.5) * 1.5, (b[1] + c[1]) / 2 + (r() - 0.5) * 1.5];
  return smooth([a, mid1, b, mid2, c]);
}

/** A single pass through text — the proofreader's delete. */
export function strikePath(w, h, seed = "strike") {
  const r = rng(seed), pts = [], n = 8;
  for (let i = 0; i <= n; i++) {
    pts.push([(w * i) / n, h * (0.55 + (r() - 0.5) * 0.12) - (i / n) * h * 0.08]);
  }
  return smooth(pts);
}

/** A hand-ruled underline. */
export function underlinePath(w, seed = "under") {
  const r = rng(seed), pts = [], n = 6;
  for (let i = 0; i <= n; i++) pts.push([(w * i) / n, 3 + (r() - 0.5) * 1.6 + (i / n) * 1.2]);
  return smooth(pts);
}

/** Two bowed diagonals — a decisive no, not a scribble. */
export function crossPaths(size = 20, seed = "x") {
  const r = rng(seed), s = size, j = () => (r() - 0.5) * s * 0.08;
  const a = smooth([[s * 0.1 + j(), s * 0.12 + j()], [s * 0.52 + j(), s * 0.47 + j()], [s * 0.9 + j(), s * 0.9 + j()]]);
  const b = smooth([[s * 0.86 + j(), s * 0.1 + j()], [s * 0.47 + j(), s * 0.53 + j()], [s * 0.14 + j(), s * 0.88 + j()]]);
  return [a, b];
}
