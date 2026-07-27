import { useEffect, useRef, useState } from "react";
import { postGenieAsk } from "../api/client";
import type { GenieAskMeta } from "../types";

// Consume the /api/genie/ask SSE stream (SPEC §11). Mirrors GridSense's
// useGenieStream: a `meta` event (matched question + chart + follow-ups +
// source), then `token` events, then `done`. Always replaces state with new
// objects — no mutations.

export interface GenieStreamState {
  meta: GenieAskMeta | null;
  text: string;
  streaming: boolean;
  error: string | null;
  done: boolean;
}

export interface GenieRequest {
  question: string;
  /** Bumped on resubmit so the same question reruns the effect. */
  nonce: number;
}

const INITIAL: GenieStreamState = {
  meta: null,
  text: "",
  streaming: false,
  error: null,
  done: false,
};

export function useGenieStream(request: GenieRequest | null): GenieStreamState {
  const [state, setState] = useState<GenieStreamState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!request) {
      setState(INITIAL);
      return;
    }

    const controller = new AbortController();
    abortRef.current?.abort();
    abortRef.current = controller;

    setState({ meta: null, text: "", streaming: true, error: null, done: false });

    async function run() {
      try {
        const res = await postGenieAsk(request!.question);
        if (!res.ok || !res.body) {
          throw new Error(`Stream failed: ${res.status} ${res.statusText}`);
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (controller.signal.aborted) return;

          buffer += decoder.decode(value, { stream: true });
          const events = buffer.split("\n\n");
          buffer = events.pop() ?? "";

          for (const ev of events) {
            const line = ev.split("\n").find((l) => l.startsWith("data:"));
            if (!line) continue;
            const payload = line.slice(5).trim();
            if (!payload) continue;

            try {
              const parsed = JSON.parse(payload) as
                | { type: "meta"; payload: GenieAskMeta }
                | { type: "token"; token: string }
                | { type: "done" };

              if (parsed.type === "meta") {
                const meta = parsed.payload;
                setState((prev) => ({ ...prev, meta }));
              } else if (parsed.type === "token") {
                const tok = parsed.token;
                setState((prev) => ({ ...prev, text: prev.text + tok }));
              } else if (parsed.type === "done") {
                setState((prev) => ({ ...prev, streaming: false, done: true }));
              }
            } catch {
              // Ignore malformed event.
            }
          }
        }
      } catch (e: unknown) {
        if (controller.signal.aborted) return;
        const msg = e instanceof Error ? e.message : String(e);
        setState((prev) => ({ ...prev, streaming: false, error: msg }));
      }
    }

    run();
    return () => {
      controller.abort();
    };
  }, [request?.question, request?.nonce]);

  return state;
}
