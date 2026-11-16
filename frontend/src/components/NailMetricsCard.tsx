import type { TimelineRead } from "../api/types";

export function NailMetricsCard({ timeline }: { timeline: TimelineRead | null }) {
  const today = timeline?.frames.find((frame) => frame.kind === "today") ?? timeline?.frames.at(-1);
  return (
    <section className="panel" aria-labelledby="metrics-heading">
      <h2 id="metrics-heading">Wellness</h2>
      <dl className="metrics">
        <div><dt>Nail bed</dt><dd>{mm(today?.nail_bed_length_mm)}</dd></div>
        <div><dt>Free margin</dt><dd>{mm(today?.free_margin_length_mm)}</dd></div>
        <div><dt>Total length</dt><dd>{mm(today?.total_length_mm)}</dd></div>
        <div><dt>Observed growth</dt><dd>{timeline ? `${timeline.v_observed_mm_day.toFixed(3)} mm/day` : "—"}</dd></div>
        <div><dt>With treatments</dt><dd>{timeline ? `${timeline.v_effective_mm_day.toFixed(3)} mm/day` : "—"}</dd></div>
      </dl>
      <ul className="insights">
        {(timeline?.insights ?? []).map((insight) => (
          <li key={insight.code}>
            <p>{insight.message}</p>
            <p className="disclaimer" role="note">{insight.disclaimer}</p>
          </li>
        ))}
      </ul>
      {timeline && timeline.insights.length === 0 && (
        <p className="disclaimer" role="note">{timeline.disclaimer}</p>
      )}
    </section>
  );
}

function mm(value: number | null | undefined) {
  return value == null ? "—" : `${value.toFixed(2)} mm`;
}
