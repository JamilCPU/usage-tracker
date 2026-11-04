export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 60) return "0m";
  const total = Math.round(seconds / 60);
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h === 0) return `${m}m`;
  return `${h}h ${String(m).padStart(2, "0")}m`;
}

export function hourLabel(n: number): string {
  const h = ((Math.trunc(n) % 24) + 24) % 24;
  const suffix = h < 12 ? "am" : "pm";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve}${suffix}`;
}

export function fmtDay(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1);
  const wd = date.toLocaleDateString("en-US", { weekday: "short" });
  return `${wd} ${date.getDate()}`;
}

const COLORS = [
  "#5b9cf5",
  "#f5a35b",
  "#5bd6a0",
  "#d77be0",
  "#f0d35b",
  "#5bd1e0",
  "#f27b8a",
  "#a3c45b",
];
const OTHER = "#7d8590";

/** Index 0-7 gives a distinct series color; any other index is the gray "other". */
export function palette(index: number): string {
  return COLORS[index] ?? OTHER;
}
