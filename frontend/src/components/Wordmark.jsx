/** "Recto" with its first footnote: the right-hand page is page one. */
export default function Wordmark({ href = "/", size }) {
  return (
    <a className="wordmark" href={href} style={size ? { fontSize: size } : undefined} aria-label="Recto, home">
      Recto<sup aria-hidden>1</sup>
    </a>
  );
}
