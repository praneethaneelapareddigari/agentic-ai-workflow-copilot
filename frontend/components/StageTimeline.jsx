export default function StageTimeline({ steps }) {
  if (!steps || steps.length === 0) {
    return <p className="loading">Waiting for pipeline to start…</p>;
  }
  return (
    <div className="stage-list">
      {steps.map((step) => (
        <div className="stage-row" key={step.sequence}>
          <span className={`dot ${step.status}`} />
          <span className="name">
            {step.sequence}. {step.name.replace(/_/g, " ")}
          </span>
          <span className="duration">
            {step.status === "succeeded" || step.status === "failed"
              ? `${step.duration_ms ?? 0} ms`
              : step.status}
          </span>
        </div>
      ))}
    </div>
  );
}
