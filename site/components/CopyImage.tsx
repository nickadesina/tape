"use client";

import { useState } from "react";

export function CopyImage({ src }: { src: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      const blob = fetch(src).then((r) => {
        if (!r.ok) throw new Error(String(r.status));
        return r.blob();
      });
      // Safari wants the ClipboardItem built synchronously inside the click.
      await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <button type="button" onClick={copy}>
      {state === "copied" ? "copied" : state === "failed" ? "copy failed: open the png" : "copy image"}
    </button>
  );
}
