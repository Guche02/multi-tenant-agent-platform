"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, streamChat } from "@/lib/api";
import { brandStyle } from "@/lib/brand";
import type { Conversation, Org, UiMessage, User } from "@/lib/types";
import { Composer, type ComposerHandle } from "./Composer";
import { AlertIcon, BoxIcon, CalendarIcon, MenuIcon, Spinner } from "./icons";
import { RecordsPanel } from "./RecordsPanel";
import { Sidebar } from "./Sidebar";
import { Thread } from "./Thread";

// A reply whose tools return one of these changed the customer's orders or bookings.
const CHANGED = new Set(["placed", "updated", "cancelled", "booked", "rescheduled"]);

let counter = 0;
const localId = () => `local-${Date.now()}-${counter++}`;

function readConversationFromUrl(): string | null {
  return typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("c");
}

function writeConversationToUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("c", id);
  else url.searchParams.delete("c");
  window.history.replaceState(null, "", url);
}

export function ChatApp({ slug }: { slug: string }) {
  const router = useRouter();
  const [org, setOrg] = useState<Org | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [fatal, setFatal] = useState<{ message: string; otherOrg?: boolean } | null>(null);

  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [threadLoading, setThreadLoading] = useState(false);
  const [threadError, setThreadError] = useState<string | null>(null);
  const [streaming, setStreaming] = useState(false);

  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelVersion, setPanelVersion] = useState(0);

  const abort = useRef<AbortController | null>(null);
  const composer = useRef<ComposerHandle>(null);
  const activeRef = useRef<string | null>(null);
  activeRef.current = activeId;

  const onAuthError = useCallback((e: unknown) => {
    if (e instanceof ApiError && e.status === 401) router.replace(`/a/${slug}`);
  }, [router, slug]);

  const loadConversations = useCallback(async () => {
    setListError(null);
    try {
      setConversations(await api.conversations(slug));
    } catch (e) {
      onAuthError(e);
      setListError(e instanceof Error ? e.message : "Couldn't load conversations.");
    }
  }, [slug, onAuthError]);

  const openConversation = useCallback(async (id: string | null) => {
    abort.current?.abort();
    setActiveId(id);
    writeConversationToUrl(id);
    setSidebarOpen(false);
    setThreadError(null);
    setMessages([]);
    if (!id) return;
    setThreadLoading(true);
    try {
      const stored = await api.messages(slug, id);
      if (activeRef.current !== id) return; // another conversation was opened meanwhile
      setMessages(stored.map((m) => ({ id: m.id, role: m.role, content: m.content, tools: [], results: [], status: "done" })));
    } catch (e) {
      onAuthError(e);
      if (e instanceof ApiError && e.status === 404) {
        writeConversationToUrl(null);
        setActiveId(null);
      } else setThreadError(e instanceof Error ? e.message : "Couldn't load this conversation.");
    } finally {
      setThreadLoading(false);
    }
  }, [slug, onAuthError]);

  // Who is signed in, for which org. Not signed in → the org's entry page.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [o, me] = await Promise.all([api.org(slug), api.me(slug)]);
        if (cancelled) return;
        setOrg(o);
        setUser(me.user);
        document.title = `${o.name} · Chat`;
        setPanelOpen(window.innerWidth >= 1280);
        loadConversations();
        openConversation(readConversationFromUrl());
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) router.replace(`/a/${slug}`);
        else if (e instanceof ApiError && e.status === 403) setFatal({ message: "You're signed in with an account from another organization.", otherOrg: true });
        else if (e instanceof ApiError && e.status === 404) setFatal({ message: "This organization doesn't exist." });
        else setFatal({ message: e instanceof Error ? e.message : "Couldn't load the chat." });
      }
    })();
    return () => { cancelled = true; abort.current?.abort(); };
  }, [slug, router, loadConversations, openConversation]);

  const send = useCallback(async (text: string) => {
    if (streaming) return;
    const assistantId = localId();
    setMessages((m) => [
      ...m,
      { id: localId(), role: "user", content: text, tools: [], results: [], status: "done" },
      { id: assistantId, role: "assistant", content: "", tools: [], results: [], status: "streaming" },
    ]);
    const update = (fn: (m: UiMessage) => UiMessage) =>
      setMessages((all) => all.map((m) => (m.id === assistantId ? fn(m) : m)));

    const controller = new AbortController();
    abort.current = controller;
    setStreaming(true);
    let changed = false;
    try {
      await streamChat(slug, text, activeRef.current, controller.signal, (event, data) => {
        if (event === "conversation") {
          if (activeRef.current !== data.id) {
            setActiveId(data.id);
            activeRef.current = data.id;
            writeConversationToUrl(data.id);
            setConversations((list) => [
              { id: data.id, title: data.title, created_at: new Date().toISOString(), updated_at: new Date().toISOString() },
              ...(list ?? []).filter((c) => c.id !== data.id),
            ]);
          }
        } else if (event === "tool") {
          update((m) => ({ ...m, tools: [...m.tools, data] }));
        } else if (event === "tool_result") {
          if (CHANGED.has(data.data?.status)) changed = true;
          update((m) => ({ ...m, results: [...m.results, data] }));
        } else if (event === "token") {
          update((m) => ({ ...m, content: m.content + data.text }));
        } else if (event === "done") {
          update((m) => ({ ...m, status: "done" }));
        } else if (event === "error") {
          update((m) => ({ ...m, status: "error", error: data.message }));
        }
      });
      update((m) => (m.status === "streaming" ? { ...m, status: "done" } : m));
    } catch (e) {
      if (controller.signal.aborted) update((m) => ({ ...m, status: "stopped" }));
      else {
        onAuthError(e);
        const message = e instanceof ApiError ? e.message : "Connection lost. Check your network and try again.";
        update((m) => ({ ...m, status: "error", error: message }));
      }
    } finally {
      setStreaming(false);
      abort.current = null;
      loadConversations(); // new title, new order
      if (changed) setPanelVersion((v) => v + 1);
    }
  }, [slug, streaming, loadConversations, onAuthError]);

  const retry = useCallback((failed: UiMessage) => {
    setMessages((all) => {
      const index = all.findIndex((m) => m.id === failed.id);
      const question = all[index - 1];
      if (question?.role === "user") setTimeout(() => send(question.content), 0);
      return all.filter((_, i) => i !== index && i !== index - 1);
    });
  }, [send]);

  async function rename(id: string, title: string) {
    const updated = await api.rename(slug, id, title);
    setConversations((list) => list?.map((c) => (c.id === id ? updated : c)) ?? null);
  }

  async function remove(id: string) {
    await api.remove(slug, id);
    setConversations((list) => list?.filter((c) => c.id !== id) ?? null);
    if (activeRef.current === id) openConversation(null);
  }

  async function signOut() {
    abort.current?.abort();
    await api.logout().catch(() => undefined);
    router.replace(`/a/${slug}`);
  }

  if (fatal) {
    return (
      <main className="flex min-h-full items-center justify-center p-4">
        <div className="max-w-sm rounded-xl bg-white p-5 text-center shadow-sm">
          <AlertIcon className="mx-auto mb-2 h-6 w-6 text-red-700" />
          <p>{fatal.message}</p>
          {fatal.otherOrg && (
            <button type="button" onClick={signOut} className="mt-3 rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white">
              Sign out
            </button>
          )}
        </div>
      </main>
    );
  }
  if (!org || !user) {
    return <main className="flex min-h-full items-center justify-center text-neutral-500"><Spinner className="h-6 w-6" /></main>;
  }

  const title = conversations?.find((c) => c.id === activeId)?.title ?? "New chat";
  const PanelIcon = org.toolset === "orders" ? BoxIcon : CalendarIcon;

  return (
    <div style={brandStyle(org.branding)} className="flex h-dvh overflow-hidden bg-neutral-50">
      <Sidebar org={org} user={user} conversations={conversations} error={listError} activeId={activeId}
        open={sidebarOpen} onClose={() => setSidebarOpen(false)} onNew={() => openConversation(null)}
        onOpen={(id) => openConversation(id)} onRename={rename} onDelete={remove} onReload={loadConversations}
        onSignOut={signOut} />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b border-neutral-200 bg-white px-3 py-2.5 sm:px-4">
          <button type="button" onClick={() => setSidebarOpen(true)} aria-label="Open conversations"
            className="rounded-md p-1.5 hover:bg-neutral-100 md:hidden"><MenuIcon /></button>
          <h1 className="min-w-0 flex-1 truncate font-medium">{title}</h1>
          <button type="button" onClick={() => setPanelOpen((o) => !o)} aria-pressed={panelOpen}
            aria-label={org.toolset === "orders" ? "My orders" : "My bookings"}
            className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-sm font-medium ${
              panelOpen ? "border-brand bg-tint text-brand" : "border-neutral-300 hover:bg-neutral-50"}`}>
            <PanelIcon className="h-4 w-4" />
            <span className="hidden sm:inline">{org.toolset === "orders" ? "My orders" : "My bookings"}</span>
          </button>
        </header>
        <Thread org={org} messages={messages} loading={threadLoading} error={threadError}
          onReload={() => openConversation(activeId)}
          actions={{ send, retry, focusComposer: () => composer.current?.focus() }} />
        <Composer ref={composer} streaming={streaming} disabled={threadLoading} onSend={send}
          onStop={() => abort.current?.abort()} />
      </main>
      <RecordsPanel org={org} open={panelOpen} version={panelVersion} onClose={() => setPanelOpen(false)} />
    </div>
  );
}
