/** "NPR 2,900" from "2900.00". */
export function money(amount: string | number | undefined, currency = "NPR"): string {
  if (amount === undefined || amount === null || amount === "") return "";
  const n = Number(amount);
  return `${currency} ${Number.isFinite(n) ? n.toLocaleString("en-US", { maximumFractionDigits: 2 }) : amount}`;
}

/** "Fri 13 Nov" from "2026-11-13". */
export function shortDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-GB", {
    weekday: "short", day: "numeric", month: "short", timeZone: "UTC",
  });
}

/** "Today", "Yesterday", "Mon", or "3 Nov" for the sidebar. */
export function relativeDay(iso: string): string {
  const date = new Date(iso);
  const today = new Date();
  const days = Math.floor((startOfDay(today) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (days === 1) return "Yesterday";
  if (days < 7) return date.toLocaleDateString("en-GB", { weekday: "short" });
  return date.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

const STATUS_STYLES: Record<string, string> = {
  pending: "bg-amber-100 text-amber-900",
  confirmed: "bg-sky-100 text-sky-900",
  shipped: "bg-indigo-100 text-indigo-900",
  delivered: "bg-emerald-100 text-emerald-900",
  cancelled: "bg-neutral-200 text-neutral-700",
  booked: "bg-emerald-100 text-emerald-900",
};

export function statusClass(status: string): string {
  return STATUS_STYLES[status] ?? "bg-neutral-100 text-neutral-800";
}
