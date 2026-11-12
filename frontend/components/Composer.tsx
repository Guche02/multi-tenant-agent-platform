"use client";

import { forwardRef, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { SendIcon, StopIcon } from "./icons";

export type ComposerHandle = { focus: () => void };

const MAX_LENGTH = 4000; // the chat endpoint's limit

type Props = { streaming: boolean; disabled?: boolean; onSend: (text: string) => void; onStop: () => void };

/** Enter sends, Shift+Enter adds a line. While a reply streams, the button stops it. */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ streaming, disabled, onSend, onStop }, ref) {
  const [text, setText] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => box.current?.focus() }));

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  function send() {
    const message = text.trim();
    if (!message || streaming || disabled) return;
    onSend(message);
    setText("");
  }

  return (
    <div className="border-t border-neutral-200 bg-white/90 px-3 py-3 backdrop-blur sm:px-6">
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        <textarea ref={box} rows={1} value={text} maxLength={MAX_LENGTH} disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Message the assistant…" aria-label="Message"
          className="max-h-[200px] min-h-[44px] flex-1 resize-none rounded-xl border border-neutral-300 bg-white px-3.5 py-2.5 text-[15px] outline-none focus:border-brand focus:ring-2 focus:ring-brand/20 disabled:bg-neutral-100" />
        {streaming ? (
          <button type="button" onClick={onStop} aria-label="Stop the reply" title="Stop"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-neutral-800 text-white">
            <StopIcon />
          </button>
        ) : (
          <button type="button" onClick={send} disabled={!text.trim() || disabled} aria-label="Send" title="Send (Enter)"
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-brand text-on-brand disabled:opacity-40">
            <SendIcon />
          </button>
        )}
      </div>
      <p className="mx-auto mt-1.5 hidden max-w-3xl text-xs text-neutral-500 sm:block">Enter to send · Shift+Enter for a new line</p>
    </div>
  );
});
