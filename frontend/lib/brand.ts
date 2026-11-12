import type { CSSProperties } from "react";
import type { Branding } from "./types";

/** CSS variables that make bg-brand, text-on-brand and bg-tint use the org's colours. */
export function brandStyle(branding?: Branding): CSSProperties {
  if (!branding) return {};
  return { "--brand": branding.primary, "--on-brand": branding.on_primary, "--tint": branding.tint } as CSSProperties;
}
