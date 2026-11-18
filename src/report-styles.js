export const REPORT_STYLES = `
:root {
  color-scheme: light;
  --ink: #152238;
  --muted: #64748b;
  --line: #dbe4ef;
  --panel: #ffffff;
  --canvas: #f3f7fb;
  --blue: #2563eb;
  --green: #0f9f6e;
  --red: #dc2626;
  --amber: #d97706;
  --purple: #7c3aed;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at 10% -10%, #dceafe 0, transparent 34rem),
    linear-gradient(180deg, #f9fbfe 0%, var(--canvas) 100%);
  font: 14px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
}
code, .mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; }
code { padding: 1px 5px; border-radius: 5px; background: #edf2f8; font-size: 0.9em; }
main { width: min(1240px, calc(100% - 32px)); margin: 0 auto; padding: 42px 0 72px; }
.hero { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 28px; align-items: end; }
.eyebrow { color: var(--blue); font-size: 11px; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; }
h1 { margin: 8px 0 8px; font-size: clamp(30px, 5vw, 52px); line-height: 1.03; letter-spacing: -.04em; }
.subtitle { max-width: 720px; margin: 0; color: var(--muted); font-size: 16px; }
.run-meta { padding: 13px 16px; border: 1px solid var(--line); border-radius: 12px; background: #ffffffbb; color: var(--muted); font-size: 12px; text-align: right; }
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 30px 0; }
.kpi { padding: 17px 18px; border: 1px solid var(--line); border-radius: 14px; background: #ffffffe8; box-shadow: 0 12px 36px #23405c0a; }
.kpi b { display: block; font-size: 28px; letter-spacing: -.04em; }
.kpi span { color: var(--muted); font-size: 12px; }
.kpi.ok b { color: var(--green); }
.global-message { margin: -12px 0 24px; padding: 12px 15px; border-radius: 10px; background: #e8f8f1; color: #08734f; border: 1px solid #bdebd9; }
.scenario { margin: 16px 0; border: 1px solid var(--line); border-radius: 18px; background: #ffffffdd; box-shadow: 0 18px 60px #183a5f0c; overflow: clip; }
.scenario > summary { display: flex; justify-content: space-between; gap: 20px; padding: 22px 24px; cursor: pointer; list-style: none; }
.scenario > summary::-webkit-details-marker { display: none; }
.scenario > summary::after { content: "+"; display: grid; place-items: center; flex: 0 0 30px; height: 30px; margin-left: 4px; border: 1px solid var(--line); border-radius: 50%; color: var(--blue); font-size: 20px; }
.scenario[open] > summary::after { content: "−"; }
.scenario > summary h2 { display: inline; margin: 0 0 0 8px; font-size: 21px; letter-spacing: -.02em; }
.scenario > summary p { margin: 5px 0 0; color: var(--muted); }
.scenario-stats { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; padding-right: 6px; }
.scenario-stats span { padding: 4px 9px; border-radius: 999px; background: #eef4fa; color: #43546c; font-size: 11px; white-space: nowrap; }
.scenario-stats .outcome { background: #e8f0ff; color: #1d4ed8; }
.scenario-body { padding: 0 24px 26px; }
.scenario-body section { padding-top: 18px; border-top: 1px solid #e8eef5; margin-top: 18px; }
h3 { margin: 0 0 12px; font-size: 13px; letter-spacing: .04em; text-transform: uppercase; color: #41536a; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 10px 12px; border-bottom: 1px solid #e8eef5; text-align: left; vertical-align: top; }
th { background: #f6f9fc; color: #52657d; font-size: 11px; letter-spacing: .05em; text-transform: uppercase; }
tr:last-child td { border-bottom: 0; }
td small { display: block; color: var(--muted); margin-top: 2px; }
.number { font-variant-numeric: tabular-nums; font-weight: 800; text-align: center; }
.transit-number { color: var(--purple); }
.muted, .empty { color: #8796a8; }
.timeline-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: #fbfdff; }
.timeline { display: block; width: 100%; min-width: 760px; height: auto; }
.timeline .axis, .timeline .grid-line { stroke: #dce5ef; stroke-width: 1; }
.timeline .stage-label { fill: #40536b; font-size: 10px; font-weight: 700; }
.timeline .stage-count { fill: #8796a8; font-size: 9px; }
.timeline .frame-label { fill: #1e3552; font-size: 10px; font-weight: 800; }
.timeline .frame-caption { fill: #8796a8; font-size: 8px; }
.timeline .lifeline { stroke-width: 1; stroke-dasharray: 2 5; }
.timeline .lifeline.live, .timeline .life-span.live { stroke: #19a974; }
.timeline .lifeline.transit, .timeline .life-span.transit { stroke: #8b5cf6; }
.timeline .lifeline.closed, .timeline .life-span.closed { stroke: #adc0d3; }
.timeline .life-span { stroke-width: 4; stroke-linecap: round; opacity: .55; }
.timeline .event-mark { cursor: help; }
.timeline .event-mark text { fill: white; font-size: 7px; font-weight: 900; text-anchor: middle; pointer-events: none; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; padding: 10px 2px 0; color: var(--muted); font-size: 11px; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.legend i { width: 8px; height: 8px; border-radius: 50%; }
.diagnostics { display: grid; gap: 9px; }
.diagnostic { padding: 13px 15px; border: 1px solid #f0d8ad; border-left: 4px solid var(--amber); border-radius: 10px; background: #fffbf3; }
.diagnostic.error { border-color: #f3c2c2; border-left-color: var(--red); background: #fff7f7; }
.diagnostic p { margin: 6px 0 9px; }
.diagnostic-head { display: flex; align-items: center; gap: 8px; }
.diagnostic-code { font-family: monospace; font-size: 11px; font-weight: 700; }
.diagnostic-label { font-weight: 700; }
.diagnostic-severity { margin-left: auto; color: var(--muted); font-size: 10px; text-transform: uppercase; }
.diagnostic-location { display: flex; gap: 16px; flex-wrap: wrap; color: var(--muted); font-size: 11px; }
.all-clear { margin: 0; padding: 14px; border: 1px solid #bdebd9; border-radius: 10px; color: #08734f; background: #f0fbf6; }
.cleanup { padding: 15px; border: 1px solid #bdebd9; border-radius: 12px; background: #f4fcf8; }
.cleanup.fail { border-color: #f3c2c2; background: #fff7f7; }
.cleanup-status { display: flex; align-items: center; gap: 8px; color: #08734f; }
.cleanup.fail .cleanup-status { color: var(--red); }
.status-dot { width: 9px; height: 9px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 4px #0f9f6e22; }
.cleanup-status span:last-child { margin-left: auto; color: var(--muted); font-size: 11px; }
.cleanup dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 14px 0 9px; }
.cleanup dl div { padding: 9px 11px; border-radius: 8px; background: #ffffffaa; }
.cleanup dt { color: var(--muted); font-size: 10px; }
.cleanup dd { margin: 0; font-weight: 700; font-size: 12px; overflow-wrap: anywhere; }
.cleanup p { margin: 0; color: var(--muted); font-size: 11px; }
.frames { display: grid; gap: 7px; }
.frame { border: 1px solid var(--line); border-radius: 10px; background: white; }
.frame > summary { display: flex; gap: 10px; align-items: center; padding: 10px 12px; cursor: pointer; list-style: none; }
.frame > summary::-webkit-details-marker { display: none; }
.frame > summary::before { content: "›"; color: var(--blue); transition: transform .15s ease; }
.frame[open] > summary::before { transform: rotate(90deg); }
.frame-id { font-family: monospace; font-weight: 800; }
.owner { margin-left: auto; color: var(--muted); font-size: 11px; }
.state { padding: 2px 7px; border-radius: 999px; font-size: 10px; font-weight: 700; }
.state.live { color: #08734f; background: #e7f8f0; }
.state.transit { color: #6d28d9; background: #f0eaff; }
.state.closed { color: #63758a; background: #edf2f7; }
.event-total { color: #8796a8; font-size: 10px; }
.frame-body { padding: 0 12px 13px 29px; }
.frame-meta { display: flex; gap: 15px; flex-wrap: wrap; padding: 8px 0; color: var(--muted); font-size: 11px; }
.event-table { border: 1px solid #e7edf4; border-radius: 8px; overflow: hidden; }
.event-table th, .event-table td { padding: 6px 8px; font-size: 11px; }
.stage-cell { font-family: monospace; color: #52657d; }
.event-pill { padding: 2px 6px; border-radius: 5px; background: #eef3f8; font-size: 10px; font-weight: 700; }
.event-warning { color: #a16207; background: #fff3d6; }
.event-close { color: #b91c1c; background: #feecec; }
.event-create, .event-clone { color: #1d4ed8; background: #eaf1ff; }
.event-transfer-out, .event-transfer-in { color: #6d28d9; background: #f1ebff; }
.stage-context { margin: 8px 0 0; color: var(--muted); font-size: 11px; }
footer { margin-top: 24px; color: #7d8ca0; font-size: 11px; }
@media (max-width: 760px) {
  main { width: min(calc(100% - 20px), 1240px); padding-top: 25px; }
  .hero { grid-template-columns: 1fr; }
  .run-meta { text-align: left; }
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .scenario > summary { padding: 17px; flex-wrap: wrap; }
  .scenario-stats { justify-content: flex-start; }
  .scenario-body { padding: 0 14px 18px; }
  .cleanup dl { grid-template-columns: repeat(2, 1fr); }
  .owner, .event-total { display: none; }
  .frame-meta { gap: 7px; }
}
`;
