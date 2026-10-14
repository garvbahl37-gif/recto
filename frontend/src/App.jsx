import { useEffect, useState } from "react";
import Landing from "./Landing.jsx";
import Console from "./Console.jsx";

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try { return localStorage.getItem("recto-theme") || "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("recto-theme", theme); } catch { /* private mode */ }
  }, [theme]);
  return { isDark: theme === "dark", toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) };
}

export default function App() {
  const { isDark, toggle } = useTheme();
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = (to) => { window.history.pushState({}, "", to); setPath(to); window.scrollTo(0, 0); };
  return path.startsWith("/app")
    ? <Console isDark={isDark} toggleTheme={toggle} />
    : <Landing isDark={isDark} toggleTheme={toggle} onOpen={() => go("/app")} />;
}
