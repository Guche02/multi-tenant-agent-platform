"use client";

import { useEffect, useRef, useState } from "react";
import type { Org, UiMessage } from "@/lib/types";
import { Cards, buildCards } from "./cards";
import { AlertIcon, CheckIcon, Spinner, XIcon } from "./icons";
import { Markdown, extractCitations } from "./Markdown";

type Actions = { send: (text: string) => void; retry: (message: UiMessage) => void; focusComposer: () => void };

const SUGGESTIONS: Record<Org["toolset"], string[]> = {
  orders: ["Which tea is good for a beginner?", "What's your returns policy?", "Show my orders", "Same as last time, please"],
  bookings: ["What services do you offer?", "Any massage slots tomorrow afternoon?", "What's your cancellation policy?", "Show my bookings"],
};

function ToolSteps({ message }: { message: UiMessage }) {
  if (!message.tools.length) return null;
  return (
    <ul className="mb-2 flex flex-wrap gap-1.5" aria-label="What the assistant did">
      {message.tools.map((t, i) => {
        const done = message.results.length > i || message.status !== "streaming";
        return (
          <li key={i} className="inline-flex items-center gap-1.5 rounded-full bg-neutral-100 px-2.5 py-1 text-xs text-neutral-700">
            {done ? <CheckIcon className="h-3.5 w-3.5 text-emerald-700" /> : <Spinner className="h-3.5 w-3.5" />}
            {done ? t.label.replace(/…$/, "") : t.label}
          </li>
        );
      })}
    </ul>
  );
}

/** The cited section's text, from what search_knowledge returned in this reply. */
function CitationSource({ message, index, onClose }: { message: UiMessage; index: number; onClose: () => void }) {
  const citation = extractCitations(message.content).citations[index];
  if (!citation) return null;
  const norm = (s: string) => s.toLowerCase().replace(/\s+/g, " ").trim();
  const hits = message.results
    .filter((r) => r.tool === "search_knowledge")
    .flatMap((r) => (r.data.results ?? []) as { document: string; section: string; text: string }[]);
  const source = hits.find((h) => norm(h.document) === norm(citation.document) && norm(h.section) === norm(citation.section))
    ?? hits.find((h) => norm(h.document) === norm(citation.document) && norm(citation.section).includes(norm(h.section)));
  return (
    <div className="mt-2 rounded-xl border border-brand/30 bg-tint p-3 text-sm">
      <div className="mb-1 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{citation.document}</div>
          <div className="text-xs text-neutral-600">{citation.section}</div>
        </div>
        <button type="button" onClick={onClose} aria-label="Close source" className="rounded p-0.5 hover:bg-white/60">
          <XIcon className="h-4 w-4" />
        </button>
      </div>
      {source ? (
        <p className="max-h-48 overflow-y-auto whitespace-pre-wrap text-neutral-800">
          {/* Chunks start with their own heading, which the header above already shows. */}
          {source.text.replace(/^\s*#*\s*([^\n]+)\n+/, (line, heading: string) =>
            norm(citation.section).endsWith(norm(heading)) ? "" : line)}
        </p>
      ) : (
        <p className="text-neutral-600">The excerpt is shown for replies in this session only. The source is the knowledge-base section named above.</p>
      )}
    </div>
  );
}

function AssistantMessage({ message, org, isLatest, actions }: { message: UiMessage; org: Org; isLatest: boolean; actions: Actions }) {
  const [citation, setCitation] = useState<number | null>(null);
  const cards = buildCards(message.results, org.currency);
  const waiting = message.status === "streaming" && !message.content && !message.tools.length;
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[42rem]">
        <ToolSteps message={message} />
        {waiting && (
          <div className="inline-flex items-center gap-2 rounded-2xl rounded-bl-sm bg-white px-4 py-3 text-sm text-neutral-500 shadow-sm">
            <Spinner /> Thinking…
          </div>
        )}
        {message.content && (
          <div className="rounded-2xl rounded-bl-sm bg-white px-4 py-3 text-[15px] shadow-sm">
            <Markdown content={message.content} activeCitation={citation}
              onCitation={(i) => setCitation((c) => (c === i ? null : i))} />
            {message.status === "streaming" && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-neutral-400 align-middle" />}
          </div>
        )}
        {citation !== null && <CitationSource message={message} index={citation} onClose={() => setCitation(null)} />}
        <Cards cards={cards} currency={org.currency}
          actions={{ enabled: isLatest && message.status === "done", send: actions.send, focusComposer: actions.focusComposer }} />
        {message.status === "stopped" && <p className="mt-1 text-xs text-neutral-500">Stopped.</p>}
        {message.status === "error" && (
          <div className="mt-2 flex items-start gap-2 rounded-lg bg-red-50 p-2.5 text-sm text-red-800">
            <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1">{message.error ?? "Something went wrong."}</span>
            {isLatest && <button type="button" onClick={() => actions.retry(message)} className="font-medium underline">Retry</button>}
          </div>
        )}
      </div>
    </div>
  );
}

type Props = {
  org: Org;
  messages: UiMessage[];
  loading: boolean;
  error: string | null;
  onReload: () => void;
  actions: Actions;
};

export function Thread({ org, messages, loading, error, onReload, actions }: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true); // follow new text unless the user has scrolled up

  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const onScroll = () => {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-4 px-3 py-5 sm:px-6">
        {loading && (
          <div className="space-y-4" aria-label="Loading conversation">
            {[0, 1, 2].map((i) => (
              <div key={i} className={`flex ${i % 2 ? "justify-end" : ""}`}>
                <div className={`h-12 animate-pulse rounded-2xl bg-neutral-200/70 ${i % 2 ? "w-1/2" : "w-3/4"}`} />
              </div>
            ))}
          </div>
        )}
        {error && !loading && (
          <div className="flex items-start gap-2 rounded-lg bg-red-50 p-3 text-sm text-red-800">
            <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button type="button" onClick={onReload} className="font-medium underline">Try again</button>
          </div>
        )}
        {!loading && !error && messages.length === 0 && (
          <div className="pt-6 sm:pt-12">
            <div className="max-w-[42rem] rounded-2xl rounded-bl-sm bg-white px-4 py-3 text-[15px] shadow-sm">{org.greeting}</div>
            <div className="mt-4 flex flex-wrap gap-2">
              {SUGGESTIONS[org.toolset].map((s) => (
                <button key={s} type="button" onClick={() => actions.send(s)}
                  className="rounded-full border border-brand/30 bg-white px-3 py-1.5 text-sm text-brand transition hover:bg-tint">
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {!loading && messages.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="flex justify-end">
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-brand px-4 py-2.5 text-[15px] text-on-brand sm:max-w-[75%]">
                {m.content}
              </div>
            </div>
          ) : (
            <AssistantMessage key={m.id} message={m} org={org} isLatest={m === lastAssistant} actions={actions} />
          ),
        )}
      </div>
    </div>
  );
}
