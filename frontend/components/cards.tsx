"use client";

import { Fragment, useState } from "react";
import { money, shortDate, statusClass } from "@/lib/format";
import type { BookingDetails, OrderDetails, OrderItem, Slot, ToolResult } from "@/lib/types";
import { BoxIcon, CalendarIcon, CheckIcon } from "./icons";

// Rich cards, built only from the tools' structured results (never from the model's text), so a
// card always shows what the backend actually returned.

type ConfirmCard = {
  kind: "confirm";
  key: string;
  icon: "order" | "booking";
  title: string;
  items?: OrderItem[];
  rows?: [string, string][];
  total?: string;
  totalLabel?: string;
  note?: string;
  confirmLabel: string;
};
type OrderCard = { kind: "order"; key: string; heading: string; order: OrderDetails };
type BookingCard = { kind: "booking"; key: string; heading: string; booking: BookingDetails };
type SlotsCard = { kind: "slots"; key: string; title: string; slots: Slot[]; message: (s: Slot) => string };
export type Card = ConfirmCard | OrderCard | BookingCard | SlotsCard;

const ORDER_HEADINGS: Record<string, string> = { placed: "Order placed", updated: "Order updated", cancelled: "Order cancelled" };
const BOOKING_HEADINGS: Record<string, string> = { booked: "Booked", rescheduled: "Booking moved", cancelled: "Booking cancelled" };

function orderConfirm(r: ToolResult, currency: string): ConfirmCard | null {
  const d = r.data;
  if (r.tool === "create_order" && d.summary) {
    return { kind: "confirm", key: "confirm", icon: "order", title: "Confirm your order", items: d.summary.items,
      total: money(d.summary.total, currency), note: `Deliver to ${d.summary.delivery_address} · cash on delivery`,
      confirmLabel: "Place order" };
  }
  if (r.tool === "reorder" && d.summary) {
    const changes = (d.price_changes ?? []) as { name: string; was: string; now: string }[];
    return { kind: "confirm", key: "confirm", icon: "order", title: `Repeat order ${d.repeating ?? ""}`.trim(),
      items: d.summary.items, total: money(d.summary.total, currency),
      note: [...changes.map((c) => `${c.name}: ${money(c.was, currency)} → ${money(c.now, currency)}`),
        `Deliver to ${d.summary.delivery_address}`].join(" · "),
      confirmLabel: "Place order" };
  }
  if (r.tool === "update_order" && d.new_order) {
    return { kind: "confirm", key: "confirm", icon: "order", title: `Change order ${d.order_number}`,
      items: d.new_order.items, total: money(d.new_order.total, currency), totalLabel: "New total",
      note: `Deliver to ${d.new_order.delivery_address}`, confirmLabel: "Save changes" };
  }
  if (r.tool === "cancel_order" && d.order_to_cancel) {
    const o = d.order_to_cancel as OrderDetails;
    return { kind: "confirm", key: "confirm", icon: "order", title: `Cancel order ${o.order_number}`, items: o.items,
      total: money(o.total, currency), confirmLabel: "Cancel order" };
  }
  return null;
}

function bookingConfirm(r: ToolResult): ConfirmCard | null {
  const d = r.data;
  if (r.tool === "create_booking" && d.summary) {
    const s = d.summary;
    return { kind: "confirm", key: "confirm", icon: "booking", title: "Confirm your booking",
      rows: [["Service", s.service], ["With", s.practitioner], ["When", `${s.date}, ${s.start}–${s.end}`]],
      total: money(s.price, s.currency), totalLabel: "Price", note: "Pay at the studio after your session.",
      confirmLabel: "Book it" };
  }
  if (r.tool === "reschedule_booking" && d.current_booking && d.new_time) {
    const c = d.current_booking as BookingDetails;
    return { kind: "confirm", key: "confirm", icon: "booking", title: `Move booking ${c.booking_number}`,
      rows: [["Session", `${c.service} with ${d.new_time.practitioner}`], ["From", `${c.starts}–${c.ends}`],
        ["To", `${d.new_time.date}, ${d.new_time.start}–${d.new_time.end}`]],
      total: money(c.price, c.currency), totalLabel: "Price (unchanged)", confirmLabel: "Move it" };
  }
  if (r.tool === "cancel_booking" && d.booking && d.cancellation_fee !== undefined) {
    const b = d.booking as BookingDetails;
    return { kind: "confirm", key: "confirm", icon: "booking", title: `Cancel booking ${b.booking_number}`,
      rows: [["Session", `${b.service} with ${b.practitioner}`], ["When", `${b.starts}–${b.ends}`]],
      total: money(d.cancellation_fee, d.currency), totalLabel: "Cancellation fee", note: d.rule_applied,
      confirmLabel: "Cancel booking" };
  }
  return null;
}

function slotsCard(r: ToolResult): SlotsCard | null {
  const d = r.data;
  if (r.tool === "find_available_slots" && d.slots?.length) {
    return { kind: "slots", key: "slots", title: `Free times · ${d.service}`, slots: d.slots,
      message: (s) => `I'd like the ${d.service} on ${s.day} ${shortDate(s.date)} at ${s.start} with ${s.practitioner}.` };
  }
  if (d.nearest_alternatives?.length) {
    const booking = d.booking?.booking_number;
    return { kind: "slots", key: "slots", title: "Nearest free times", slots: d.nearest_alternatives,
      message: (s) => booking
        ? `Please move booking ${booking} to ${s.day} ${shortDate(s.date)} at ${s.start} with ${s.practitioner}.`
        : `I'll take ${s.day} ${shortDate(s.date)} at ${s.start} with ${s.practitioner} instead.` };
  }
  return null;
}

/** The cards for one reply: the latest confirmation and slot list, and each order or booking it touched. */
export function buildCards(results: ToolResult[], currency: string): Card[] {
  const singles = new Map<string, Card>(); // the latest of each kind wins
  const records = new Map<string, Card>();
  for (const r of results) {
    const d = r.data ?? {};
    if (d.status === "needs_confirmation") {
      const card = orderConfirm(r, currency) ?? bookingConfirm(r);
      if (card) singles.set("confirm", card);
      continue;
    }
    const slots = slotsCard(r);
    if (slots) singles.set("slots", slots);
    if (d.order?.order_number && (ORDER_HEADINGS[d.status] || r.tool === "get_order_status")) {
      records.set(`order-${d.order.order_number}`, { kind: "order", key: `order-${d.order.order_number}`,
        heading: ORDER_HEADINGS[d.status] ?? "Order status", order: d.order });
    }
    if (d.booking?.booking_number && BOOKING_HEADINGS[d.status]) {
      records.set(`booking-${d.booking.booking_number}`, { kind: "booking", key: `booking-${d.booking.booking_number}`,
        heading: BOOKING_HEADINGS[d.status], booking: d.booking });
    }
  }
  return [...records.values(), ...singles.values()];
}

// ---------------------------------------------------------------------------

function Frame({ icon, title, aside, children }: { icon: "order" | "booking"; title: string; aside?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-3.5 shadow-sm">
      <div className="mb-2 flex items-center gap-2">
        <span className="rounded-lg bg-tint p-1.5 text-brand">
          {icon === "order" ? <BoxIcon className="h-4 w-4" /> : <CalendarIcon className="h-4 w-4" />}
        </span>
        <span className="font-medium">{title}</span>
        <span className="ml-auto">{aside}</span>
      </div>
      {children}
    </div>
  );
}

function Pill({ status }: { status: string }) {
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${statusClass(status)}`}>{status}</span>;
}

function Items({ items, currency }: { items: OrderItem[]; currency: string }) {
  return (
    <ul className="divide-y divide-neutral-100 text-sm">
      {items.map((i) => (
        <li key={i.sku} className="flex gap-3 py-1.5">
          <span className="min-w-0 flex-1">
            {i.quantity} × {i.name} <span className="text-xs text-neutral-500">{i.sku}</span>
          </span>
          <span className="shrink-0 tabular-nums">{money(i.line_total, currency)}</span>
        </li>
      ))}
    </ul>
  );
}

function TotalRow({ label = "Total", value }: { label?: string; value?: string }) {
  if (!value) return null;
  return (
    <div className="mt-1 flex justify-between border-t border-neutral-200 pt-2 text-sm font-semibold">
      <span>{label}</span><span className="tabular-nums">{value}</span>
    </div>
  );
}

type Actions = { enabled: boolean; send: (text: string) => void; focusComposer: () => void };

function ConfirmView({ card, currency, actions }: { card: ConfirmCard; currency: string; actions: Actions }) {
  const [chosen, setChosen] = useState<"yes" | "change" | null>(null);
  return (
    <Frame icon={card.icon} title={card.title}>
      {card.items && <Items items={card.items} currency={currency} />}
      {card.rows && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
          {card.rows.map(([k, v]) => (
            <Fragment key={k}><dt className="text-neutral-500">{k}</dt><dd>{v}</dd></Fragment>
          ))}
        </dl>
      )}
      <TotalRow label={card.totalLabel} value={card.total} />
      {card.note && <p className="mt-2 text-xs text-neutral-600">{card.note}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" disabled={!actions.enabled || chosen !== null}
          onClick={() => { setChosen("yes"); actions.send("Yes, confirm."); }}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-on-brand disabled:opacity-50">
          {chosen === "yes" && <CheckIcon className="h-4 w-4" />} {card.confirmLabel}
        </button>
        <button type="button" disabled={!actions.enabled || chosen !== null}
          onClick={() => { setChosen("change"); actions.focusComposer(); }}
          className="rounded-lg border border-neutral-300 px-3.5 py-2 text-sm font-medium disabled:opacity-50">
          Change something
        </button>
      </div>
    </Frame>
  );
}

export function OrderView({ order, heading, currency }: { order: OrderDetails; heading: string; currency: string }) {
  return (
    <Frame icon="order" title={`${heading} · ${order.order_number}`} aside={<Pill status={order.status} />}>
      <Items items={order.items} currency={currency} />
      <TotalRow value={money(order.total, currency)} />
      <p className="mt-2 text-xs text-neutral-600">Placed {order.placed_at} · to {order.delivery_address}</p>
      {order.status_history?.length > 1 && (
        <ol className="mt-2 space-y-0.5 border-l-2 border-tint pl-3 text-xs text-neutral-600">
          {order.status_history.map((h, i) => (
            <li key={i}><span className="font-medium capitalize text-neutral-800">{h.to}</span> · {h.at}</li>
          ))}
        </ol>
      )}
    </Frame>
  );
}

export function BookingView({ booking, heading }: { booking: BookingDetails; heading: string }) {
  return (
    <Frame icon="booking" title={`${heading} · ${booking.booking_number}`} aside={<Pill status={booking.status} />}>
      <p className="text-sm font-medium">{booking.service}</p>
      <p className="text-sm text-neutral-700">with {booking.practitioner}</p>
      <p className="text-sm text-neutral-700">{booking.starts}–{booking.ends}</p>
      <p className="mt-1 text-sm">
        {money(booking.price, booking.currency)}
        {booking.cancellation_fee !== undefined && booking.status === "cancelled" && (
          <span className="text-neutral-600"> · cancellation fee {money(booking.cancellation_fee, booking.currency)}</span>
        )}
      </p>
    </Frame>
  );
}

function SlotsView({ card, actions }: { card: SlotsCard; actions: Actions }) {
  const [picked, setPicked] = useState<string | null>(null);
  const byDate = new Map<string, Slot[]>();
  for (const s of card.slots) byDate.set(s.date, [...(byDate.get(s.date) ?? []), s]);
  return (
    <Frame icon="booking" title={card.title}>
      <div className="space-y-2.5">
        {[...byDate.entries()].map(([date, slots]) => (
          <div key={date}>
            <div className="mb-1 text-xs font-medium text-neutral-600">{shortDate(date)}</div>
            <div className="flex flex-wrap gap-1.5">
              {slots.map((s) => {
                const id = `${s.date}-${s.start}-${s.practitioner}`;
                return (
                  <button key={id} type="button" disabled={!actions.enabled || picked !== null}
                    onClick={() => { setPicked(id); actions.send(card.message(s)); }}
                    title={`${s.start}–${s.end} with ${s.practitioner}`}
                    className={`rounded-lg border px-2.5 py-1.5 text-left text-sm transition disabled:opacity-50 ${
                      picked === id ? "border-brand bg-brand text-on-brand" : "border-neutral-300 hover:border-brand hover:bg-tint"}`}>
                    <span className="font-medium tabular-nums">{s.start}</span>
                    <span className="ml-1 text-xs opacity-75">{s.practitioner.split(" ")[0]}</span>
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </Frame>
  );
}

export function Cards({ cards, currency, actions }: { cards: Card[]; currency: string; actions: Actions }) {
  if (!cards.length) return null;
  return (
    <div className="mt-2 space-y-2">
      {cards.map((card) => {
        if (card.kind === "confirm") return <ConfirmView key={card.key} card={card} currency={currency} actions={actions} />;
        if (card.kind === "order") return <OrderView key={card.key} order={card.order} heading={card.heading} currency={currency} />;
        if (card.kind === "booking") return <BookingView key={card.key} booking={card.booking} heading={card.heading} />;
        return <SlotsView key={card.key} card={card} actions={actions} />;
      })}
    </div>
  );
}
