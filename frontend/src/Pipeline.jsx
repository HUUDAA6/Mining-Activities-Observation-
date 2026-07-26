import { useEffect, useState, useCallback, useRef } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:9000';

const _NOISE = /^(?:::(?:group|endgroup)::|DAG bundles loaded:|Filling up the DagBag|Pushing xcom|(?:Starting|Marking|Task exited|Received SIG|Executing <Task|Running <TaskInstance|Exiting executor)|\[[\d\-T:.+Z ]{10,}\]\s*\{[^}]*\}\s*(?:DEBUG|INFO)\s*-\s*(?:Starting attempt|Marking task|Task exited|Received SIG|Executing|Exiting|Running on host|DagBag))/i;
const _AIRFLOW_PREFIX = /^\[[\d\-T:.+Z ]{10,}\]\s*\{[^}]*\}\s*(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*[-–]\s*/;

function cleanLogLines(rawLines) {
  const out = [];
  for (const raw of (rawLines || [])) {
    const stripped = raw.trim();
    if (!stripped) continue;
    if (_NOISE.test(stripped)) continue;
    const cleaned = stripped.replace(_AIRFLOW_PREFIX, '').trim();
    if (cleaned) out.push(cleaned);
  }
  return out;
}

/* ---- state metadata ---- */
const STATE_META = {
  success:         { label: 'Success',        color: '#10b981' },
  failed:          { label: 'Failed',          color: '#ef4444' },
  running:         { label: 'Running',         color: '#3b82f6' },
  queued:          { label: 'Queued',          color: '#f59e0b' },
  scheduled:       { label: 'Scheduled',       color: '#8b5cf6' },
  skipped:         { label: 'Skipped',         color: '#9ca3af' },
  upstream_failed: { label: 'Upstream failed', color: '#f97316' },
  up_for_retry:    { label: 'Retrying',        color: '#f59e0b' },
  restarting:      { label: 'Restarting',      color: '#3b82f6' },
};
const PENDING_META = { label: 'Pending', color: '#9ca3af' };

function stateMeta(s) {
  return STATE_META[s] || PENDING_META;
}

const fmtDate = (s) => {
  if (!s) return '—';
  try {
    return new Date(s).toLocaleString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  } catch { return s; }
};

const fmtDuration = (start, end) => {
  if (!start) return '—';
  const ms = (end ? new Date(end) : new Date()) - new Date(start);
  if (ms < 0) return '—';
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  if (m >= 60) return `${Math.floor(m / 60)}h ${m % 60}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
};

export default function PipelineDashboard() {
  const [runs, setRuns]           = useState([]);
  const [total, setTotal]         = useState(0);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [expandedRun, setExpanded] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/jobs?limit=25`)
      .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then((d) => { if (!cancelled) { setRuns(d.runs || []); setTotal(d.total || 0); } })
      .catch((e) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reloadKey]);

  const running   = runs.filter((r) => r.state === 'running').length;
  const succeeded = runs.filter((r) => r.state === 'success').length;
  const failed    = runs.filter((r) => r.state === 'failed').length;

  const handleToggle = useCallback((id) => {
    setExpanded((cur) => (cur === id ? null : id));
  }, []);

  return (
    <div className="gdash">
      <div className="gdash-scroll">

        {/* ---- Page header ---- */}
        <div className="gdash-head">
          <div>
            <h1 className="gdash-title">Pipeline Runs</h1>
            <p className="gdash-subtitle">
              Global mining pipeline ·{' '}
              {loading ? 'loading…' : `${total} total runs`}
            </p>
          </div>
          <div className="gdash-head-actions">
            <button
              className="gdash-ghost-btn"
              onClick={() => { setReloadKey((k) => k + 1); setExpanded(null); }}
              disabled={loading}
            >
              {loading ? 'Loading…' : '↻ Refresh'}
            </button>
          </div>
        </div>

        {/* ---- KPI strip ---- */}
        <div className="pdash-kpi-strip">
          <PipelineKpi label="Total Tracked" value={loading ? '—' : total} />
          <PipelineKpi label="Running Now"   value={loading ? '—' : running}   tone="running"  />
          <PipelineKpi label="Succeeded"     value={loading ? '—' : succeeded} tone="success"  />
          <PipelineKpi label="Failed"        value={loading ? '—' : failed}    tone="failed"   />
        </div>

        {/* ---- Loading skeleton ---- */}
        {loading && (
          <div className="gdash-card gdash-skel" style={{ height: 320 }} />
        )}

        {/* ---- Error ---- */}
        {error && !loading && (
          <div className="gdash-error">
            Could not reach the pipeline API: {error}
            <button
              className="gdash-ghost-btn"
              onClick={() => setReloadKey((k) => k + 1)}
            >
              Retry
            </button>
          </div>
        )}

        {/* ---- Empty ---- */}
        {!loading && !error && runs.length === 0 && (
          <div className="gdash-empty">
            No pipeline runs found. Trigger a run from the Map view.
          </div>
        )}

        {/* ---- Run table ---- */}
        {!loading && !error && runs.length > 0 && (
          <section className="gdash-card">
            <div className="gdash-card-head">
              <h3 className="gdash-card-title">Run History</h3>
              <span className="gdash-card-sub">
                Click a row to inspect task statuses and logs
              </span>
            </div>
            <div className="gdash-table-wrap">
              <table className="gdash-table pdash-runs-table">
                <thead>
                  <tr>
                    <th>Site / Run</th>
                    <th>State</th>
                    <th>Started</th>
                    <th>Duration</th>
                    <th style={{ width: 32 }} />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <RunRows
                      key={run.run_id}
                      run={run}
                      expanded={expandedRun === run.run_id}
                      onToggle={handleToggle}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        <footer className="gdash-footer">
          Powered by AriasTechSolutions. All rights reserved 2026.
        </footer>
      </div>
    </div>
  );
}

/* ---- KPI mini card ---- */
function PipelineKpi({ label, value, tone }) {
  const COLOR = { running: 'var(--brand)', success: 'var(--gain)', failed: 'var(--loss)' };
  return (
    <div className="pdash-kpi">
      <span className="pdash-kpi-value" style={{ color: COLOR[tone] || 'var(--ink)' }}>
        {value}
      </span>
      <span className="pdash-kpi-label">{label}</span>
    </div>
  );
}

function RunRows({ run, expanded, onToggle }) {
  const [detail, setDetail]    = useState(null);
  const [detailLoading, setDL] = useState(false);
  const [selectedTask, setST]  = useState(null);
  const [logs, setLogs]        = useState(null);
  const [logsLoading, setLL]   = useState(false);
  const [acting, setActing]    = useState(null); // 'stop' | 'retry' | 'restart' | null

  const fetchDetail = useCallback(() => {
    setDL(true);
    fetch(`${API_BASE}/api/jobs/${run.run_id}`)
      .then((r) => r.json())
      .then((d) => setDetail(d))
      .catch(() => {})
      .finally(() => setDL(false));
  }, [run.run_id]);

  /* Fetch task detail when a row is expanded */
  useEffect(() => {
    if (!expanded) { setST(null); setLogs(null); return; }
    fetchDetail();
  }, [expanded, fetchDetail]);

  const doAction = useCallback((endpoint, label) => {
    setActing(label);
    fetch(`${API_BASE}/api/jobs/${run.run_id}/${endpoint}`, { method: 'POST' })
      .catch(() => {})
      .finally(() => {
        setActing(null);
        setTimeout(fetchDetail, 1500); // give Airflow time to update
      });
  }, [run.run_id, fetchDetail]);

  const loadLogs = useCallback((task) => {
    if (!task.state) return;
    setST(task);
    // Success / skipped: no log fetch needed
    if (task.state === 'success' || task.state === 'skipped') {
      setLogs({ _success: true });
      return;
    }
    setLogs(null);
    setLL(true);
    const try_n = task.try_number || 1;
    fetch(`${API_BASE}/api/jobs/${run.run_id}/tasks/${task.task_id}/logs?try_number=${try_n}`)
      .then((r) => r.json())
      .then((d) => setLogs({ ...d, lines: cleanLogLines(d.lines) }))
      .catch(() => setLogs({ lines: [] }))
      .finally(() => setLL(false));
  }, [run.run_id]);

  const m = stateMeta(run.state);

  return (
    <>
      <tr
        className={`pdash-run-row${expanded ? ' is-expanded' : ''}`}
        onClick={() => onToggle(run.run_id)}
      >
        <td>
          <span className="pdash-run-name">{run.area_name || run.run_id}</span>
          <span className="pdash-run-id">{run.run_id}</span>
        </td>
        <td>
          <span
            className="gdash-badge pdash-state-badge"
            style={{
              color: m.color,
              background: `color-mix(in srgb, ${m.color} 14%, transparent)`,
            }}
          >
            <span
              className={`pdash-state-dot${run.state === 'running' ? ' is-pulse' : ''}`}
              style={{ background: m.color }}
            />
            {m.label}
          </span>
        </td>
        <td className="gdash-td-muted">{fmtDate(run.start_date)}</td>
        <td className="gdash-td-muted">{fmtDuration(run.start_date, run.end_date)}</td>
        <td className="pdash-chevron-cell">
          <span className={`pdash-chevron${expanded ? ' is-open' : ''}`}>›</span>
        </td>
      </tr>

      {expanded && (
        <tr className="pdash-detail-row">
          <td colSpan={5}>
            <div className="pdash-detail">
              {detailLoading && <p className="pdash-detail-msg">Loading task details…</p>}

              {!detailLoading && detail && (
                <>
                  {/* ---- Action bar ---- */}
                  <RunActionBar
                    detail={detail}
                    acting={acting}
                    onStop={()    => doAction('stop',         'stop')}
                    onRetry={()   => doAction('retry-failed', 'retry')}
                    onRestart={() => doAction('restart',      'restart')}
                  />

                  {/* ---- Progress ---- */}
                  <ProgressBar progress={detail.progress} />

                  {/* ---- Task grid ---- */}
                  <TaskGrid
                    tasks={detail.tasks}
                    selectedTask={selectedTask}
                    onTaskClick={loadLogs}
                  />

                  {/* ---- Log viewer ---- */}
                  {selectedTask ? (
                    <LogViewer task={selectedTask} logs={logs} loading={logsLoading} />
                  ) : (
                    <p className="pdash-detail-hint">
                      Click a task card above to view its log output.
                    </p>
                  )}
                </>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

/* ---- Action bar shown at the top of the expanded detail ---- */
function RunActionBar({ detail, acting, onStop, onRetry, onRestart }) {
  const state = detail.state;
  const isActive   = ['running', 'queued', 'scheduled', 'restarting'].includes(state);
  const isFailed   = state === 'failed';
  const isTerminal = ['success', 'failed'].includes(state);

  return (
    <div className="pdash-action-bar">
      <div className="pdash-action-meta">
        {detail.current && (
          <span className="pdash-action-current">
            <span className="pdash-action-dot is-running" />
            {detail.current}
          </span>
        )}
        {detail.failed && (
          <span className="pdash-action-failed">
            <span className="pdash-action-dot is-failed" />
            {detail.failed}
          </span>
        )}
        {isTerminal && !detail.current && !detail.failed && (
          <span className="pdash-action-done">
            {state === 'success' ? 'All tasks completed successfully.' : 'Run ended.'}
          </span>
        )}
      </div>

      <div className="pdash-action-btns">
        {isActive && (
          <button
            className="gdash-ghost-btn pdash-btn-danger"
            onClick={(e) => { e.stopPropagation(); onStop(); }}
            disabled={!!acting}
          >
            {acting === 'stop' ? 'Stopping…' : '■ Stop run'}
          </button>
        )}
        {isFailed && (
          <button
            className="gdash-ghost-btn"
            onClick={(e) => { e.stopPropagation(); onRetry(); }}
            disabled={!!acting}
          >
            {acting === 'retry' ? 'Retrying…' : '↩ Retry failed'}
          </button>
        )}
        <button
          className="gdash-ghost-btn"
          onClick={(e) => { e.stopPropagation(); onRestart(); }}
          disabled={!!acting}
        >
          {acting === 'restart' ? 'Restarting…' : '↺ Restart'}
        </button>
      </div>
    </div>
  );
}

/* ---- Progress bar ---- */
function ProgressBar({ progress }) {
  if (!progress) return null;
  const { completed, total, percent } = progress;
  return (
    <div className="pdash-progress">
      <div className="pdash-progress-track">
        <div className="pdash-progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="pdash-progress-label">
        {completed} / {total} tasks · {percent}%
      </span>
    </div>
  );
}

/* ---- Task grid ---- */
function TaskGrid({ tasks, selectedTask, onTaskClick }) {
  return (
    <div className="pdash-task-grid">
      {tasks.map((task) => {
        const m = stateMeta(task.state);
        const isSel = selectedTask?.task_id === task.task_id;
        return (
          <button
            key={task.task_id}
            className={`pdash-task-btn${isSel ? ' is-selected' : ''}${!task.state ? ' is-pending' : ''}`}
            style={{ '--tc': m.color }}
            onClick={(e) => { e.stopPropagation(); onTaskClick(task); }}
            title={`${task.label} — ${m.label}`}
          >
            <span className="pdash-task-dot" />
            <span className="pdash-task-label">{task.label}</span>
            <span className="pdash-task-state">{m.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/* ---- extract the most useful error lines from a log ---- */
function errorHint(lines) {
  if (!lines || !lines.length) return [];
  let tbIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (/traceback/i.test(lines[i])) { tbIdx = i; break; }
  }
  const slice = tbIdx >= 0 ? lines.slice(tbIdx) : lines;
  const relevant = slice.filter((l) =>
    /\b(error|exception|failed|cannot|unable|no such|invalid|unexpected|not found)\b/i.test(l)
  );
  if (relevant.length) return relevant.slice(-4);
  return slice.slice(-3);
}

/* ---- Task detail panel — state-aware ---- */
function LogViewer({ task, logs, loading }) {
  const m = stateMeta(task.state);
  const isSuccess = task.state === 'success' || task.state === 'skipped';
  const isFailed  = task.state === 'failed'  || task.state === 'upstream_failed';
  const dur = (task.start_date && task.end_date)
    ? fmtDuration(task.start_date, task.end_date)
    : null;

  return (
    <div className="pdash-log-panel">
      <div className="pdash-log-header">
        <span className="pdash-log-title">{task.label}</span>
        <span className="pdash-log-state" style={{ color: m.color }}>{m.label}</span>
      </div>

      {/* Success */}
      {isSuccess && (
        <div className="pdash-result is-success">
          <span className="pdash-result-icon" aria-hidden>&#10003;</span>
          <div className="pdash-result-detail">
            <span className="pdash-result-msg">Completed successfully</span>
            {dur && <span className="pdash-result-sub">Duration &middot; {dur}</span>}
          </div>
        </div>
      )}

      {/* Loading */}
      {!isSuccess && loading && (
        <div className="pdash-log-body"><span className="pdash-log-meta">Loading logs&hellip;</span></div>
      )}

      {/* Failed — error hint */}
      {!isSuccess && !loading && isFailed && (
        <div className="pdash-result is-error">
          <span className="pdash-result-icon" aria-hidden>&#10007;</span>
          <div className="pdash-result-detail">
            <span className="pdash-result-msg">Task failed</span>
            {dur && <span className="pdash-result-sub">After {dur}</span>}
            {errorHint(logs?.lines).map((line, i) => (
              <div key={i} className="pdash-error-hint-line">{line}</div>
            ))}
            {!logs?.lines?.length && (
              <span className="pdash-result-sub">No log detail available.</span>
            )}
          </div>
        </div>
      )}

      {/* Running / queued — live log */}
      {!isSuccess && !loading && !isFailed && (
        <div className="pdash-log-body">
          {!logs?.lines?.length && (
            <span className="pdash-log-meta">No log output yet.</span>
          )}
          {logs?.lines?.map((line, i) => (
            <div key={i} className="pdash-log-line">{line || ' '}</div>
          ))}
        </div>
      )}
    </div>
  );
}
