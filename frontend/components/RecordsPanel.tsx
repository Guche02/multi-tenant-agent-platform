"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { BookingDetails, Org, OrderDetails } from "@/lib/types";
import { BookingView, OrderView } from "./cards";
import { AlertIcon, RefreshIcon, Spinner, XIcon } from "./icons";

type Data =
  | { kind: "orders"; orders: OrderDetails[]; currency: string }
  | { kind: "bookings"; upcoming: BookingDetails[]; past: BookingDetails[] };

/**
 * "My orders" or "My bookings", from the same service functions the agent's tools use, so the
 * panel and the agent agree. Reloads whenever `version` changes (after a reply that changed something).
 */
export function RecordsPanel({ org, open, version, onClose }: { org: Org; open: boolean; version: number; onClose: () => void }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const title = org.toolset === "orders" ? "My orders" : "My bookings";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (org.toolset === "orders") {
        const r = await api.orders(org.slug);
        setData({ kind: "orders", orders: r.orders, currency: r.currency });
      } else {
        const r = await api.bookings(org.slug);
        setData({ kind: "bookings", upcoming: r.upcoming, past: r.past_and_cancelled });
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load.");
    } finally {
      setLoading(false);
    }
  }, [org.slug, org.toolset]);

  useEffect(() => {
    if (open) load();
  }, [open, version, load]);

  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-30 bg-black/30 xl:hidden" onClick={onClose} aria-hidden="true" />
      <aside aria-label={title}
        className="fixed inset-y-0 right-0 z-40 flex w-full max-w-sm flex-col border-l border-neutral-200 bg-neutral-50 xl:static xl:z-auto xl:w-96">
        <div className="flex items-center gap-2 border-b border-neutral-200 bg-white px-4 py-3">
          <h2 className="flex-1 font-semibold">{title}</h2>
          <button type="button" onClick={load} aria-label="Refresh" title="Refresh" className="rounded-md p-1.5 hover:bg-neutral-100">
            {loading ? <Spinner /> : <RefreshIcon className="h-4 w-4" />}
          </button>
          <button type="button" onClick={onClose} aria-label={`Close ${title}`} className="rounded-md p-1.5 hover:bg-neutral-100">
            <XIcon className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 p-2.5 text-sm text-red-800">
              <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" /><span className="flex-1">{error}</span>
              <button type="button" onClick={load} className="font-medium underline">Retry</button>
            </div>
          )}
          {!data && !error && [0, 1].map((i) => <div key={i} className="h-28 animate-pulse rounded-xl bg-neutral-200/70" />)}
          {data?.kind === "orders" && (data.orders.length === 0
            ? <p className="py-8 text-center text-sm text-neutral-500">No orders yet. Ask the assistant to place one.</p>
            : data.orders.map((o) => <OrderView key={o.order_number} order={o} heading="Order" currency={data.currency} />))}
          {data?.kind === "bookings" && (
            <>
              <h3 className="px-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">Upcoming</h3>
              {data.upcoming.length === 0
                ? <p className="px-1 text-sm text-neutral-500">No upcoming sessions.</p>
                : data.upcoming.map((b) => <BookingView key={b.booking_number} booking={b} heading="Booking" />)}
              {data.past.length > 0 && (
                <>
                  <h3 className="px-1 pt-2 text-xs font-semibold uppercase tracking-wide text-neutral-500">Past and cancelled</h3>
                  {data.past.map((b) => <BookingView key={b.booking_number} booking={b} heading="Booking" />)}
                </>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
