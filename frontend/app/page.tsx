"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { brandStyle } from "@/lib/brand";
import type { OrgSummary } from "@/lib/types";
import { Spinner } from "@/components/icons";

/** The landing page: links to each org's own entry point (/a/<slug>). */
export default function Home() {
  const [orgs, setOrgs] = useState<OrgSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.orgs().then(setOrgs).catch((e) => setError(e.message));
  }, []);

  return (
    <main className="mx-auto flex min-h-full max-w-xl flex-col justify-center px-4 py-12">
      <h1 className="text-2xl font-semibold">Choose your organization</h1>
      <p className="mt-1 text-sm text-neutral-600">Each one has its own assistant and its own customer accounts.</p>
      <div className="mt-6 space-y-3">
        {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
        {!orgs && !error && (
          <p className="flex items-center gap-2 text-sm text-neutral-500"><Spinner /> Loading…</p>
        )}
        {orgs?.map((org) => (
          <Link key={org.slug} href={`/a/${org.slug}`} style={brandStyle(org.branding)}
            className="block rounded-xl border border-neutral-200 bg-white p-4 shadow-sm transition hover:border-brand">
            <div className="flex items-center gap-3">
              <span className="h-9 w-9 shrink-0 rounded-full bg-brand" />
              <div>
                <div className="font-medium">{org.name}</div>
                <div className="text-sm text-neutral-600">{org.branding.tagline}</div>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </main>
  );
}
