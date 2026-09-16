export default function ToolCallList({ toolCalls }) {
  if (!toolCalls || toolCalls.length === 0) {
    return <p className="loading">No tools were needed for this request.</p>;
  }
  return (
    <div>
      {toolCalls.map((call, i) => (
        <div className="tool-call" key={i}>
          <span className="tool-name">{call.tool_name}</span>
          {" — "}
          {call.status === "succeeded" ? "succeeded" : `failed: ${call.result?.error || "error"}`}
          <div className="meta">
            attempt {call.attempt} · {call.cache_hit ? "cache hit" : "cache miss"} · {call.duration_ms ?? 0} ms
          </div>
        </div>
      ))}
    </div>
  );
}
