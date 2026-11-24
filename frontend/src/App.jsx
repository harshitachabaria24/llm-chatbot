import React, { useEffect, useRef, useState } from "react";
import { newSessionId, sendChat } from "./api.js";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const SOURCE_LABEL = { LLM_CACHE: "Served from cache", LIVE_INFERENCE: "Live model call" };

function Metric({ label, testid, children }) {
  return (
    <div className="metric">
      <dt>{label}</dt>
      <dd data-testid={testid}>{children}</dd>
    </div>
  );
}

function AssistantMessage({ item }) {
  const m = item.metadata;
  const cached = m.resolution_source === "LLM_CACHE";
  return (
    <article className={`turn assistant ${cached ? "is-cache" : "is-live"}`} data-testid="assistant-message">
      <div className="reply">
  <ReactMarkdown remarkPlugins={[remarkGfm]}>
    {item.content}
  </ReactMarkdown>
</div>
      <dl className="meta">
        <div className="metric source">
          <dt>Source</dt>
          <dd>
            <span className="source-badge" data-testid="meta-source">{m.resolution_source}</span>
            <span className="source-note">{SOURCE_LABEL[m.resolution_source]}</span>
          </dd>
        </div>
        <Metric label="Model" testid="meta-model">{m.model}</Metric>
        <Metric label="Latency" testid="meta-latency">{m.latency_ms} ms</Metric>
        <Metric label="Input tokens" testid="meta-input-tokens">{m.input_tokens}</Metric>
        <Metric label="Output tokens" testid="meta-output-tokens">{m.output_tokens}</Metric>
        <Metric label="Key" testid="meta-key">{m.key_id ?? "none"}</Metric>
        <Metric label="Failed keys before this" testid="meta-fallbacks">{m.fallback_attempts}</Metric>
      </dl>
    </article>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId); // a new session on every page load
  const [items, setItems] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, loading]);

  async function send() {
    const message = input.trim();
    if (!message || loading) return;
    setInput("");
    setItems((prev) => [...prev, { kind: "user", content: message }]);
    setLoading(true);
    try {
      const data = await sendChat({ sessionId, message, systemPrompt, simulateFailure: simulate });
      setItems((prev) => [
        ...prev,
        { kind: "assistant", content: data.reply, metadata: data.metadata },
      ]);
    } catch (err) {
      setItems((prev) => [...prev, { kind: "error", content: err.message }]);
    } finally {
      setLoading(false);
    }
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="app">
      <header className="top">
        <div>
          <h1>Resilient chat</h1>
          <p className="sub">Every reply shows where it came from, what it cost and how long it took.</p>
        </div>
        <p className="session">Session <code>{sessionId}</code></p>
      </header>

      <details className="settings">
        <summary>Session settings</summary>
        <label htmlFor="sys">System prompt</label>
        <textarea
          id="sys"
          data-testid="system-prompt-input"
          rows={2}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="Optional. Tells the assistant how to behave in this session."
        />
        <label htmlFor="sim">Simulate key failures</label>
        <input
          id="sim"
          data-testid="simulate-failure-input"
          value={simulate}
          onChange={(e) => setSimulate(e.target.value)}
          placeholder="For testing failover, e.g. key_1=429 or *=429"
        />
      </details>

      <main className="thread" aria-live="polite">
        {items.length === 0 && (
          <p className="empty">Ask anything to start. Send the same first message in a new tab to see a cache hit.</p>
        )}
        {items.map((item, i) => {
          if (item.kind === "user") {
            return (
              <div key={i} className="turn user">
                <p>{item.content}</p>
              </div>
            );
          }
          if (item.kind === "error") {
            return (
              <div key={i} className="turn error" role="alert">
                <p>{item.content}</p>
              </div>
            );
          }
          return <AssistantMessage key={i} item={item} />;
        })}
        {loading && <p className="pending">Waiting for a reply…</p>}
        <div ref={endRef} />
      </main>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <textarea
          data-testid="chat-input"
          aria-label="Message"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message. Enter sends, Shift+Enter adds a line."
        />
        <button type="submit" data-testid="send-button" disabled={loading}>
          Send
        </button>
      </form>
    </div>
  );
}
