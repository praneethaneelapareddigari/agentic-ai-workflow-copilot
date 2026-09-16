import { useState } from "react";
import Head from "next/head";
import StageTimeline from "../components/StageTimeline";
import ToolCallList from "../components/ToolCallList";
import { createWorkflow } from "../lib/apiClient";

const EXAMPLES = [
  "What's the weather in Tokyo and convert 200 USD to GBP",
  "Look up the stock price of AAPL",
  "Summarize this text and check its sentiment: I love this product, it's amazing!",
  "Show me enterprise customers in the database",
];

export default function Home() {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [workflow, setWorkflow] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!message.trim() || loading) return;
    setLoading(true);
    setError(null);
    setWorkflow(null);
    try {
      const wf = await createWorkflow(message, conversationId);
      setWorkflow(wf);
      setConversationId(wf.conversation_id);
      setMessage("");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container">
      <Head>
        <title>Agentic AI Workflow Copilot</title>
      </Head>

      <div className="header">
        <h1>🤖 Agentic AI Workflow Copilot</h1>
        <span className="badge">8-stage pipeline · 15 tools</span>
      </div>

      <form className="input-row" onSubmit={handleSubmit}>
        <input
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="Ask me to do something — I'll plan it and invoke the right tools…"
        />
        <button type="submit" disabled={loading}>
          {loading ? "Running…" : "Send"}
        </button>
      </form>

      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            type="button"
            className="example-chip"
            onClick={() => setMessage(ex)}
          >
            {ex}
          </button>
        ))}
      </div>

      {error && <div className="error-box">{error}</div>}

      {loading && <p className="loading">Running the 8-stage pipeline…</p>}

      {workflow && (
        <>
          <div className="card">
            <h2>Pipeline stages</h2>
            <StageTimeline steps={workflow.steps} />
          </div>

          <div className="card">
            <h2>Tools invoked</h2>
            <ToolCallList toolCalls={workflow.tool_calls} />
          </div>

          <div className="card">
            <h2>Response</h2>
            <div className="final-response">
              {workflow.final_response || workflow.error || "No response generated."}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
