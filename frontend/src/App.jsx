import { useEffect, useRef, useState } from "react";
import { API_URL, newSessionId, sendChat } from "./api.js";

const SIMULATIONS = [
  { label: "No failure simulation", value: "" },
  { label: "key_1 → 429 (rate limit)", value: "key_1=429" },
  { label: "key_1 → 500 (server error)", value: "key_1=500" },
  { label: "key_1 → 429, key_2 → 500", value: "key_1=429,key_2=500" },
  { label: "All keys → 429", value: "*=429" },
];

function Meta({ meta }) {
  const cached = meta.resolution_source === "LLM_CACHE";
  return (
    <div className="meta">
      <span className={`chip ${cached ? "chip-cache" : "chip-live"}`} title="Resolution source">
        <span data-testid="meta-source">{meta.resolution_source}</span>
      </span>
      <span className="chip" title="Model">
        <span data-testid="meta-model">{meta.model}</span>
      </span>
      <span className="chip" title="Latency">
        <span data-testid="meta-latency">{meta.latency_ms} ms</span>
      </span>
      <span className="chip" title="Input tokens (this request)">
        in <span data-testid="meta-input-tokens">{meta.input_tokens}</span>
      </span>
      <span className="chip" title="Output tokens (this reply)">
        out <span data-testid="meta-output-tokens">{meta.output_tokens}</span>
      </span>
      <span className="chip chip-muted" title="Key that answered">
        key {meta.key_id ?? "—"}
      </span>
      {meta.fallback_attempts > 0 && (
        <span className="chip chip-warn" title="Keys that failed before this one">
          {meta.fallback_attempts} failover{meta.fallback_attempts > 1 ? "s" : ""}
        </span>
      )}
    </div>
  );
}

export default function App() {
  // A new session on every page load.
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [simulate, setSimulate] = useState("");
  const [pending, setPending] = useState(false);
  const endRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, pending]);

  async function send() {
    const text = input;
    if (pending || !text.trim()) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text.trim() }]);
    setPending(true);
    const result = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure: simulate });
    setPending(false);
    if (result.ok) {
      setMessages((m) => [...m, { role: "assistant", content: result.data.reply, metadata: result.data.metadata }]);
    } else {
      setMessages((m) => [...m, { role: "error", content: result.error }]);
    }
    inputRef.current?.focus();
  }

  function onKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setMessages([]);
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Lyftr Chat</h1>
          <div className="sub">
            session <code>{sessionId}</code> · backend <code>{API_URL}</code>
          </div>
        </div>
        <button className="ghost" onClick={newChat}>New chat</button>
      </header>

      <section className="settings">
        <label>
          System prompt (optional)
          <input
            data-testid="system-prompt-input"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="You are a helpful, concise assistant."
          />
        </label>
        <label>
          Failover test (X-Simulate-Failure)
          <select value={simulate} onChange={(e) => setSimulate(e.target.value)}>
            {SIMULATIONS.map((s) => (
              <option key={s.value} value={s.value}>{s.label}</option>
            ))}
          </select>
        </label>
      </section>

      <main className="chat">
        {messages.length === 0 && (
          <div className="empty">Start a conversation. Each reply shows its source, model, latency and token usage.</div>
        )}
        {messages.map((m, i) =>
          m.role === "assistant" ? (
            <div key={i} className="row left">
              <div className="bubble assistant" data-testid="assistant-message">
                <div className="text">{m.content}</div>
                <Meta meta={m.metadata} />
              </div>
            </div>
          ) : m.role === "user" ? (
            <div key={i} className="row right">
              <div className="bubble user" data-testid="user-message">{m.content}</div>
            </div>
          ) : (
            <div key={i} className="row left">
              <div className="bubble error" role="alert" data-testid="error-message">{m.content}</div>
            </div>
          )
        )}
        {pending && (
          <div className="row left">
            <div className="bubble assistant typing"><span /><span /><span /></div>
          </div>
        )}
        <div ref={endRef} />
      </main>

      <footer className="composer">
        <textarea
          ref={inputRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
          rows={1}
          maxLength={4000}
          autoFocus
        />
        <button data-testid="send-button" onClick={send} disabled={pending || !input.trim()}>
          {pending ? "…" : "Send"}
        </button>
      </footer>
    </div>
  );
}
