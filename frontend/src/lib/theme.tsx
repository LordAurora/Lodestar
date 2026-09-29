import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

/** What the user picked. "system" follows the OS setting. */
export type ThemeMode = "light" | "dark" | "system";
type Theme = "light" | "dark";

const STORAGE_KEY = "lodestar.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

function savedMode(): ThemeMode {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark" || saved === "system") return saved;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

const systemTheme = (): Theme => (matchMedia(DARK_QUERY).matches ? "dark" : "light");

interface ThemeState {
  mode: ThemeMode;
  theme: Theme; // the theme actually applied
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
}

const ThemeContext = createContext<ThemeState>({
  mode: "system",
  theme: "light",
  setMode: () => {},
  toggle: () => {},
});

/** Light / dark / system theme. The choice is remembered in localStorage. */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(savedMode);
  const [system, setSystem] = useState<Theme>(systemTheme);
  const theme = mode === "system" ? system : mode;

  // Follow OS changes while in "system" mode.
  useEffect(() => {
    const mq = matchMedia(DARK_QUERY);
    const onChange = () => setSystem(mq.matches ? "dark" : "light");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
  }, [theme]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }, []);

  const toggle = useCallback(() => setMode(theme === "dark" ? "light" : "dark"), [theme, setMode]);

  return (
    <ThemeContext.Provider value={{ mode, theme, setMode, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
