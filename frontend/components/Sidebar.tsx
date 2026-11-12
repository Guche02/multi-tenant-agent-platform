"use client";

import { useState } from "react";
import { relativeDay } from "@/lib/format";
import type { Conversation, Org, User } from "@/lib/types";
import { AlertIcon, LogoutIcon, PencilIcon, PlusIcon, TrashIcon, XIcon } from "./icons";

type Props = {
  org: Org;
  user: User;
  conversations: Conversation[] | null;
  error: string | null;
  activeId: string | null;
  open: boolean;
  onClose: () => void;
  onNew: () => void;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onReload: () => void;
  onSignOut: () => void;
};

function Row({ c, active, onOpen, onRename, onDelete }: {
  c: Conversation; active: boolean; onOpen: () => void;
  onRename: (title: string) => Promise<void>; onDelete: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(c.title);
  const [busy, setBusy] = useState(false);

  async function save() {
    const next = title.trim();
    setEditing(false);
    if (next && next !== c.title) {
      setBusy(true);
      await onRename(next).catch(() => setTitle(c.title));
      setBusy(false);
    } else setTitle(c.title);
  }

  if (editing) {
    return (
      <li className="px-2 py-1">
        <input autoFocus value={title} maxLength={200} aria-label="Conversation name"
          onChange={(e) => setTitle(e.target.value)} onBlur={save}
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { setTitle(c.title); setEditing(false); } }}
          className="w-full rounded-md border border-brand px-2 py-1.5 text-sm outline-none ring-2 ring-brand/20" />
      </li>
    );
  }
  return (
    <li className={`group mx-1 flex items-center rounded-lg ${active ? "bg-tint" : "hover:bg-neutral-100"} ${busy ? "opacity-60" : ""}`}>
      <button type="button" onClick={onOpen} aria-current={active ? "page" : undefined}
        className="min-w-0 flex-1 px-2.5 py-2 text-left">
        <div className={`truncate text-sm ${active ? "font-medium text-brand" : ""}`}>{c.title}</div>
        <div className="text-xs text-neutral-500">{relativeDay(c.updated_at)}</div>
      </button>
      <div className="flex shrink-0 pr-1 opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100">
        <button type="button" onClick={() => setEditing(true)} aria-label={`Rename “${c.title}”`}
          className="rounded-md p-1.5 text-neutral-500 hover:bg-white hover:text-neutral-800"><PencilIcon className="h-4 w-4" /></button>
        <button type="button" aria-label={`Delete “${c.title}”`}
          onClick={async () => { if (confirm(`Delete “${c.title}”? This can't be undone.`)) { setBusy(true); await onDelete().catch(() => setBusy(false)); } }}
          className="rounded-md p-1.5 text-neutral-500 hover:bg-white hover:text-red-700"><TrashIcon className="h-4 w-4" /></button>
      </div>
    </li>
  );
}

/** The user's conversations. A drawer on small screens, a column from md up. */
export function Sidebar(p: Props) {
  return (
    <>
      {p.open && <div className="fixed inset-0 z-30 bg-black/30 md:hidden" onClick={p.onClose} aria-hidden="true" />}
      <aside aria-label="Conversations"
        className={`fixed inset-y-0 left-0 z-40 flex w-72 max-w-[85vw] flex-col border-r border-neutral-200 bg-white transition-transform md:static md:z-auto md:translate-x-0 ${
          p.open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="flex items-center gap-2 border-b border-neutral-200 px-3 py-3">
          <span className="h-7 w-7 shrink-0 rounded-full bg-brand" />
          <span className="min-w-0 flex-1 truncate font-semibold">{p.org.name}</span>
          <button type="button" onClick={p.onClose} aria-label="Close menu" className="rounded-md p-1 md:hidden"><XIcon /></button>
        </div>
        <div className="p-2">
          <button type="button" onClick={p.onNew}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-on-brand">
            <PlusIcon className="h-4 w-4" /> New chat
          </button>
        </div>
        <nav className="min-h-0 flex-1 overflow-y-auto pb-2">
          {p.error && (
            <div className="m-2 flex items-start gap-2 rounded-lg bg-red-50 p-2.5 text-sm text-red-800">
              <AlertIcon className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="flex-1">{p.error}</span>
              <button type="button" onClick={p.onReload} className="font-medium underline">Retry</button>
            </div>
          )}
          {!p.conversations && !p.error && (
            <div className="space-y-2 p-3" aria-label="Loading conversations">
              {[0, 1, 2, 3].map((i) => <div key={i} className="h-9 animate-pulse rounded-lg bg-neutral-100" />)}
            </div>
          )}
          {p.conversations?.length === 0 && (
            <p className="px-4 py-6 text-center text-sm text-neutral-500">No conversations yet. Say hello to start one.</p>
          )}
          {p.conversations && p.conversations.length > 0 && (
            <ul className="space-y-0.5">
              {p.conversations.map((c) => (
                <Row key={c.id} c={c} active={c.id === p.activeId} onOpen={() => p.onOpen(c.id)}
                  onRename={(title) => p.onRename(c.id, title)} onDelete={() => p.onDelete(c.id)} />
              ))}
            </ul>
          )}
        </nav>
        <div className="flex items-center gap-2 border-t border-neutral-200 px-3 py-2.5 text-sm">
          <span className="min-w-0 flex-1 truncate text-neutral-600" title={p.user.email}>{p.user.email}</span>
          <button type="button" onClick={p.onSignOut} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-neutral-600 hover:bg-neutral-100">
            <LogoutIcon className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>
    </>
  );
}
