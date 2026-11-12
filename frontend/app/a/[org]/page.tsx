"use client";

import { useParams, useRouter } from "next/navigation";
import { FormEvent, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { brandStyle } from "@/lib/brand";
import type { Org } from "@/lib/types";
import { AlertIcon, Spinner } from "@/components/icons";

/**
 * An org's own entry point, e.g. /a/himali-tea: its name, colours and greeting, and sign-in or
 * sign-up. Signing up here makes the account part of this org. Signed-in users go straight to the chat.
 */
export default function EntryPage() {
  const { org: slug } = useParams<{ org: string }>();
  const router = useRouter();
  const [org, setOrg] = useState<Org | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [otherOrg, setOtherOrg] = useState(false);

  useEffect(() => {
    api.org(slug).then((o) => {
      setOrg(o);
      document.title = `${o.name} · Sign in`;
    }).catch((e) => setLoadError(e instanceof ApiError && e.status === 404 ? "This organization doesn't exist." : e.message));
    api.me(slug).then(() => router.replace(`/a/${slug}/chat`)).catch((e) => {
      if (e instanceof ApiError && e.status === 403) setOtherOrg(true);
    });
  }, [slug, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await (mode === "login" ? api.login : api.signup)(slug, email, password);
      router.replace(`/a/${slug}/chat`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setBusy(false);
    }
  }

  async function signOut() {
    await api.logout().catch(() => undefined);
    setOtherOrg(false);
  }

  if (loadError) {
    return (
      <main className="flex min-h-full items-center justify-center p-4">
        <p className="flex items-center gap-2 rounded-lg bg-red-50 p-4 text-red-800"><AlertIcon /> {loadError}</p>
      </main>
    );
  }
  if (!org) {
    return (
      <main className="flex min-h-full items-center justify-center text-neutral-500"><Spinner className="h-6 w-6" /></main>
    );
  }

  return (
    <main style={brandStyle(org.branding)} className="flex min-h-full items-center justify-center bg-tint px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 h-12 w-12 rounded-full bg-brand" />
          <h1 className="text-2xl font-semibold">{org.name}</h1>
          <p className="mt-1 text-sm text-neutral-600">{org.branding.tagline}</p>
        </div>

        <div className="mb-4 rounded-2xl rounded-bl-sm bg-white p-4 text-[15px] shadow-sm">{org.greeting}</div>

        <div className="rounded-2xl bg-white p-5 shadow-sm">
          {otherOrg && (
            <div className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
              You're signed in with an account from another organization.{" "}
              <button onClick={signOut} className="font-medium underline">Sign out</button> to use {org.name}.
            </div>
          )}
          <div className="mb-4 grid grid-cols-2 rounded-lg bg-neutral-100 p-1 text-sm" role="tablist">
            {(["login", "signup"] as const).map((m) => (
              <button key={m} role="tab" aria-selected={mode === m} onClick={() => { setMode(m); setError(null); }}
                className={`rounded-md py-1.5 font-medium ${mode === m ? "bg-white shadow-sm" : "text-neutral-600"}`}>
                {m === "login" ? "Sign in" : "Create account"}
              </button>
            ))}
          </div>
          <form onSubmit={submit} className="space-y-3">
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Email</span>
              <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)}
                className="w-full rounded-lg border border-neutral-300 px-3 py-2 outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Password</span>
              <input type="password" required minLength={8} maxLength={72}
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                value={password} onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-lg border border-neutral-300 px-3 py-2 outline-none focus:border-brand focus:ring-2 focus:ring-brand/20" />
              {mode === "signup" && <span className="mt-1 block text-xs text-neutral-500">At least 8 characters.</span>}
            </label>
            {error && <p className="rounded-lg bg-red-50 p-2.5 text-sm text-red-800">{error}</p>}
            <button type="submit" disabled={busy}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand py-2.5 font-medium text-on-brand disabled:opacity-60">
              {busy && <Spinner />} {mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
