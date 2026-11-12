"use client";

import { useRef, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { CheckIcon, CopyIcon, DocIcon } from "./icons";

export type Citation = { document: string; section: string };

// The agent cites knowledge-base facts as [Document title › Section]. Not followed by "(",
// so a real Markdown link isn't mistaken for one.
const CITATION = /\[([^\[\]\n]+?)\s*›\s*([^\[\]\n]+?)\](?!\()/g;

/** Turns each citation into a link to #cite-N (rendered as a chip), and lists them in order. */
export function extractCitations(text: string): { markdown: string; citations: Citation[] } {
  const citations: Citation[] = [];
  const markdown = text.replace(CITATION, (_, document: string, section: string) => {
    const doc = document.replace(/^\*+|\*+$/g, "").trim();
    const sec = section.replace(/^\*+|\*+$/g, "").trim();
    let index = citations.findIndex((c) => c.document === doc && c.section === sec);
    if (index === -1) index = citations.push({ document: doc, section: sec }) - 1;
    return `[${doc} › ${sec}](#cite-${index})`;
  });
  return { markdown, citations };
}

function CodeBlock(props: React.HTMLAttributes<HTMLPreElement>) {
  const ref = useRef<HTMLPreElement>(null);
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(ref.current?.innerText ?? "");
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="group relative">
      <pre ref={ref} {...props} />
      <button type="button" onClick={copy} aria-label="Copy code"
        className="absolute right-2 top-2 rounded-md bg-white/10 p-1.5 text-white/80 opacity-0 transition hover:bg-white/20 group-hover:opacity-100 focus:opacity-100">
        {copied ? <CheckIcon className="h-4 w-4" /> : <CopyIcon className="h-4 w-4" />}
      </button>
    </div>
  );
}

type Props = {
  content: string;
  /** Called when a citation chip is clicked, with its index in `extractCitations(content).citations`. */
  onCitation?: (index: number) => void;
  activeCitation?: number | null;
};

export function Markdown({ content, onCitation, activeCitation }: Props) {
  const { markdown } = extractCitations(content);
  const components: Components = {
    pre: CodeBlock,
    a({ href, children }) {
      const cite = href?.match(/^#cite-(\d+)$/);
      if (cite) {
        const index = Number(cite[1]);
        return (
          <button type="button" onClick={() => onCitation?.(index)} aria-expanded={activeCitation === index}
            className={`mx-0.5 inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 align-baseline text-xs font-medium no-underline transition ${
              activeCitation === index ? "border-brand bg-brand text-on-brand" : "border-brand/30 bg-tint text-brand hover:border-brand"
            }`}>
            <DocIcon className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">{children}</span>
          </button>
        );
      }
      return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
    },
  };
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]} components={components}>
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
