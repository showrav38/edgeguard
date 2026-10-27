// iX "classic" theme, dark by default, light on request. Remembered per browser (a convenience only).
export type Theme = "dark" | "light";

export function loadTheme(): Theme {
  try {
    return localStorage.getItem("edgeguard.theme") === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function applyTheme(theme: Theme) {
  document.documentElement.setAttribute("data-ix-theme", "classic");
  document.documentElement.setAttribute("data-ix-color-schema", theme);
}

export function toggleTheme() {
  const next: Theme = document.documentElement.getAttribute("data-ix-color-schema") === "light" ? "dark" : "light";
  applyTheme(next);
  try { localStorage.setItem("edgeguard.theme", next); } catch { /* storage blocked: keep it for this page */ }
}
