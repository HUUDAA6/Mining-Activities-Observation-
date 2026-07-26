import React, { useCallback, useEffect, useRef, useState } from 'react';
import { prettifyError } from './errorMessages';
import ConfirmDialog from './ConfirmDialog';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:9000';

const _NOISE = /^(?:::(?:group|endgroup)::|DAG bundles loaded:|Filling up the DagBag|Pushing xcom|(?:Starting|Marking|Task exited|Received SIG|Executing <Task|Running <TaskInstance|Exiting executor)|\[[\d\-T:.+Z ]{10,}\]\s*\{[^}]*\}\s*(?:DEBUG|INFO)\s*-\s*(?:Starting attempt|Marking task|Task exited|Received SIG|Executing|Exiting|Running on host|DagBag))/i;
const _AIRFLOW_PREFIX = /^\[[\d\-T:.+Z ]{10,}\]\s*\{[^}]*\}\s*(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*[-–]\s*/;

function cleanLogLines(rawLines) {
  const out = [];
  for (const raw of rawLines) {
    const stripped = raw.trim();
    if (!stripped) continue;
    if (_NOISE.test(stripped)) continue;
    const cleaned = stripped.replace(_AIRFLOW_PREFIX, '').trim();
    if (cleaned) out.push(cleaned);
  }
  return out;
}

const TERMINAL = new Set(['success', 'failed']);

const STATE_TONE = {
  success:      'success',
  failed:       'failed',
  running:      'running',
  queued:       'pending',
  scheduled:    'pending',
  up_for_retry: 'retry',
  skipped:      'skipped',
};

const FAILURE_STATES = new Set(['failed', 'up_for_retry']);

function fmtElapsed(start, end) {
  if (!start) return '—';
  const t0 = new Date(start).getTime();
  const t1 = end ? new Date(end).getTime() : Date.now();
  const s = Math.max(0, Math.round((t1 - t0) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  if (m < 60) return `${m}m ${rs.toString().padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${(m % 60).toString().padStart(2, '0')}m`;
}

export default function JobProgress({
  job,
  onClose,
  onSuccess,
  onStateChange,
  mcOpen      = false,
  sidebarOpen = false,
  index       = 0,
  stackSize   = 1,
  tabbedMode  = false,
  active      = true,
}) {
  const { runId, areaName } = job;
  const [status,       setStatus]       = useState(null);
  const [error,        setError]        = useState(null);
  const [reconnecting, setReconnecting] = useState(false);
  const [minimized,    setMinimized]    = useState(false);
  const [pillPos,    setPillPos]    = useState(null);
  const [logsByTask, setLogsByTask] = useState({});
  const [openLogFor, setOpenLogFor] = useState(null);
  const [toast,      setToast]      = useState(null);
  const [, force]                   = useState(0);
  const tickRef         = useRef(null);
  const successFiredRef = useRef(false);
  const failedSeenRef   = useRef(new globalThis.Set());
  const retrySeenRef    = useRef(new globalThis.Set());
  const dragRef         = useRef({ dragging: false, offX: 0, offY: 0, moved: false });

  useEffect(() => {
    if (!runId) return undefined;
    let closed = false;
    const url = `${API_BASE}/api/jobs/${encodeURIComponent(runId)}/events`;
    const es = new EventSource(url);

    const handleStatus = (e) => {
      try {
        const data = JSON.parse(e.data);
        setStatus(data);
        setReconnecting(false);
        setError(null);
      } catch (err) {
        console.error('bad SSE payload', err);
      }
    };

    es.addEventListener('status', handleStatus);
    es.addEventListener('status', (e) => {
      try { onStateChange?.(JSON.parse(e.data).state); } catch { /* ignore */ }
    });
    es.addEventListener('done', (e) => {
      try {
        const data = JSON.parse(e.data);
        setStatus(data);
        if (data.state === 'success' && !successFiredRef.current) {
          successFiredRef.current = true;
          onSuccess?.(data);
        }
      } catch (err) {
        console.error('bad SSE done payload', err);
      }
      if (!closed) es.close();
    });
    es.addEventListener('error', (e) => {
      try {
        if (e.data) {
          const data = JSON.parse(e.data);
          if (data.transient) {
            // Airflow briefly unreachable while the run keeps going : show a quiet badge, not the full failure card.
            setReconnecting(true);
          } else {
            setError(data.detail || 'SSE error');
          }
        } else if (es.readyState === EventSource.CLOSED) {
          // EventSource auto-reconnects; treat a dropped pipe as transient.
          setReconnecting(true);
        }
      } catch {}
    });

    return () => {
      closed = true;
      es.close();
    };
  }, [runId, onSuccess]);

  // detect new task-level failures 
  useEffect(() => {
    if (!status?.tasks) return;
    for (const t of status.tasks) {
      const key = `${t.task_id}#${t.try_number ?? 0}`;
      if (t.state === 'failed' && !failedSeenRef.current.has(key)) {
        failedSeenRef.current.add(key);
        setToast({
          tone:    'failed',
          title:   `${t.label} failed`,
          taskId:  t.task_id,
          tryNum:  t.try_number || 1,
        });
      } else if (t.state === 'up_for_retry' && !retrySeenRef.current.has(key)) {
        retrySeenRef.current.add(key);
        setToast({
          tone:    'retry',
          title:   `${t.label} retrying`,
          taskId:  t.task_id,
          tryNum:  t.try_number || 1,
        });
      }
    }
  }, [status]);

  // auto-dismiss toast after 8 s
  useEffect(() => {
    if (!toast) return undefined;
    const id = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(id);
  }, [toast]);

  const fetchLog = useCallback(async (taskId, tryNum) => {
    const key = `${taskId}#${tryNum || 1}`;
    setLogsByTask((m) => ({ ...m, [key]: { loading: true } }));
    try {
      const res = await fetch(
        `${API_BASE}/api/jobs/${encodeURIComponent(runId)}/tasks/${encodeURIComponent(taskId)}/logs?try_number=${tryNum || 1}&tail=200`,
      );
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(text || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setLogsByTask((m) => ({ ...m, [key]: { lines: cleanLogLines(data.lines || []) } }));
    } catch (e) {
      setLogsByTask((m) => ({ ...m, [key]: { error: e.message || String(e) } }));
    }
  }, [runId]);

  const toggleLog = useCallback((task) => {
    if (!task?.task_id) return;
    const key = `${task.task_id}#${task.try_number || 1}`;
    if (openLogFor === key) {
      setOpenLogFor(null);
      return;
    }
    setOpenLogFor(key);
    if (task.state === 'success' || task.state === 'skipped') {
      setLogsByTask((m) => ({ ...m, [key]: { _success: true } }));
      return;
    }
    if (!logsByTask[key] || logsByTask[key].error) {
      fetchLog(task.task_id, task.try_number || 1);
    }
  }, [openLogFor, logsByTask, fetchLog]);

  // tick once a second so the elapsed counter updates while a task is running
  useEffect(() => {
    if (status && TERMINAL.has(status.state)) {
      if (tickRef.current) {
        clearInterval(tickRef.current);
        tickRef.current = null;
      }
      return undefined;
    }
    tickRef.current = setInterval(() => force((n) => n + 1), 1000);
    return () => {
      if (tickRef.current) clearInterval(tickRef.current);
      tickRef.current = null;
    };
  }, [status]);

  const percent = status?.progress?.percent ?? 0;
  const runState = status?.state ?? 'queued';
  const stateTone = STATE_TONE[runState] || 'pending';
  const finished = TERMINAL.has(runState);
  const headline = (() => {
    if (runState === 'success')  return 'Analysis complete';
    if (runState === 'failed')   return `Failed at ${status?.failed ?? 'pipeline step'}`;
    if (status?.current)         return status.current;
    return 'Starting analysis…';
  })();

  const shiftClass = `${sidebarOpen ? 'sidebar-open' : ''} ${mcOpen ? 'mc-open' : ''}`.trim();

  const [actionPending, setActionPending] = useState(null);
  const [actionError,   setActionError]   = useState(null);
  const [confirm,       setConfirm]       = useState(null);

  const callAction = useCallback(async (path, label) => {
    setActionPending(label);
    setActionError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/jobs/${encodeURIComponent(runId)}/${path}`,
        { method: 'POST' },
      );
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(text || `HTTP ${res.status}`);
      }
      failedSeenRef.current = new globalThis.Set();
      retrySeenRef.current  = new globalThis.Set();
      successFiredRef.current = false;
    } catch (e) {
      setActionError(e.message || String(e));
    } finally {
      setActionPending(null);
    }
  }, [runId]);

  const retryFailed   = useCallback(() => callAction('retry-failed', 'Retrying'), [callAction]);
  const restartRun    = useCallback(() => callAction('restart',      'Restarting'), [callAction]);
  const resumeRun     = useCallback(() => callAction('resume',       'Resuming'),   [callAction]);
  const stopRun = useCallback(() => {
    setConfirm({
      title:   'Stop this pipeline run?',
      detail:  'Running tasks will finish what they started and no new tasks will be scheduled. You can continue the run later from where it stopped.',
      confirmLabel: 'Stop pipeline',
      tone:    'danger',
      onConfirm: () => {
        setConfirm(null);
        callAction('stop', 'Stopping');
      },
    });
  }, [callAction]);

  const hasFailedTask  = (status?.tasks || []).some((t) => FAILURE_STATES.has(t.state));
  const hasUnfinished  = (status?.tasks || []).some((t) => t.state !== 'success' && t.state !== 'skipped');
  const canRestart     = TERMINAL.has(runState) || hasFailedTask;
  const canStop        = !TERMINAL.has(runState);
  const canResume      = runState === 'failed' && hasUnfinished;

  const onPillPointerDown = useCallback((e) => {
    if (e.target.closest('.job-pill-dismiss')) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const baseX  = pillPos?.x ?? (window.innerWidth  - 168);
    const baseY  = pillPos?.y ?? (window.innerHeight - 80);
    dragRef.current = {
      dragging: true,
      offX:     startX - baseX,
      offY:     startY - baseY,
      moved:    false,
    };
    const onMove = (ev) => {
      if (!dragRef.current.dragging) return;
      const nx = ev.clientX - dragRef.current.offX;
      const ny = ev.clientY - dragRef.current.offY;
      if (Math.abs(ev.clientX - startX) + Math.abs(ev.clientY - startY) > 4) {
        dragRef.current.moved = true;
      }
      setPillPos({
        x: Math.max(8, Math.min(window.innerWidth  - 56, nx)),
        y: Math.max(8, Math.min(window.innerHeight - 48, ny)),
      });
    };
    const onUp = () => {
      dragRef.current.dragging = false;
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup',   onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup',   onUp);
  }, [pillPos]);

  const onPillClick = useCallback(() => {
    if (dragRef.current.moved) {
      dragRef.current.moved = false;
      return;
    }
    setMinimized(false);
  }, []);

  if (tabbedMode) {
    return (
      <div className={`job-tab-content ${active ? 'is-active' : ''}`}>
        <h3 className="job-panel-title">{headline}</h3>

        <div className="job-progress">
          <div className={`job-progress-bar job-progress-bar-${stateTone}`} style={{ width: `${percent}%` }} />
        </div>
        <div className="job-progress-meta">
          <span>{status?.progress?.completed ?? 0} / {status?.progress?.total ?? 15} steps</span>
          <span>{fmtElapsed(status?.start_date, status?.end_date)}</span>
        </div>

        {reconnecting && !error && (
          <div className="job-reconnect" role="status">
            <span className="job-reconnect-dot" aria-hidden />
            Reconnecting to Airflow… the run is still going.
          </div>
        )}

        {error && (
          (() => {
            const pretty = prettifyError(error);
            return (
              <div className="job-panel-error">
                <strong>{pretty.title}</strong>
                <span className="job-panel-error-detail">{pretty.detail}</span>
              </div>
            );
          })()
        )}

        <ol className="job-steps">
          {(status?.tasks || []).map((t) => {
            const tone        = STATE_TONE[t.state] || 'idle';
            const failureLike = FAILURE_STATES.has(t.state);
            const key         = `${t.task_id}#${t.try_number || 1}`;
            const opened      = openLogFor === key;
            const log         = logsByTask[key];
            const inspectable = failureLike || t.state === 'success' || t.state === 'running';
            return (
              <React.Fragment key={t.task_id}>
                <li
                  className={`job-step job-step-${tone} ${inspectable ? 'is-clickable' : ''} ${opened ? 'is-open' : ''}`}
                  onClick={inspectable ? () => toggleLog(t) : undefined}
                  role={inspectable ? 'button' : undefined}
                  tabIndex={inspectable ? 0 : -1}
                  title={inspectable ? 'Click to view logs' : ''}
                >
                  <span className="job-step-marker" aria-hidden>
                    {t.state === 'success'      && <CheckIcon />}
                    {t.state === 'failed'       && <XIcon />}
                    {t.state === 'up_for_retry' && <RetryIcon />}
                    {t.state === 'skipped'      && <DashIcon />}
                    {t.state === 'running'      && <span className="job-step-spinner" />}
                    {(t.state === 'queued' || t.state === 'scheduled') && <DotIcon />}
                    {!t.state && <DotIcon />}
                  </span>
                  <span className="job-step-label">
                    {t.label}
                    {t.state === 'up_for_retry' && <span className="job-step-tag">retry {t.try_number || 1}</span>}
                  </span>
                  <span className="job-step-elapsed">
                    {t.start_date ? fmtElapsed(t.start_date, t.end_date) : ''}
                  </span>
                </li>
                {opened && (
                  <li className="job-step-log">
                    {(!log || log.loading) && <span className="job-log-info">Loading logs…</span>}
                    {log?.error && (() => {
                      const pretty = prettifyError(log.error);
                      return (
                        <div className="job-log-error-block">
                          <strong>{pretty.title}</strong>
                          <span>{pretty.detail}</span>
                          <button
                            className="job-log-refresh"
                            onClick={(e) => { e.stopPropagation(); fetchLog(t.task_id, t.try_number || 1); }}
                          >Try again</button>
                        </div>
                      );
                    })()}
                    {log?._success && (
                      <div className="job-log-success">
                        <span className="job-log-success-icon" aria-hidden>✓</span>
                        <span>Completed successfully</span>
                        {t.start_date && <span className="job-log-meta">{fmtElapsed(t.start_date, t.end_date)}</span>}
                      </div>
                    )}
                    {log?.lines && (() => {
                      const hints = errorHint(log.lines);
                      const isFailed = t.state === 'failed' || t.state === 'up_for_retry';
                      return isFailed ? (
                        <div className="job-log-fail">
                          <div className="job-log-fail-head">
                            <span className="job-log-fail-icon" aria-hidden>✗</span>
                            <span>Task failed{t.start_date ? ` · ${fmtElapsed(t.start_date, t.end_date)}` : ''}</span>
                          </div>
                          {hints.length ? hints.map((l, i) => (
                            <div key={i} className="job-log-fail-hint">{l}</div>
                          )) : (
                            <div className="job-log-fail-hint job-log-fail-hint-muted">No additional details available</div>
                          )}
                          <button className="job-log-refresh" onClick={(e) => { e.stopPropagation(); fetchLog(t.task_id, t.try_number || 1); }}>Refresh</button>
                        </div>
                      ) : (
                        <>
                          {t.state === 'running' && (
                            <div className="job-log-live-head">
                              <span className="job-log-live-dot" aria-hidden />
                              <span>Live output</span>
                            </div>
                          )}
                          <div className="job-log-lines">
                            {log.lines.length
                              ? log.lines.map((l, i) => <div key={i} className="job-log-line">{l}</div>)
                              : <span className="job-log-info">No output yet</span>}
                          </div>
                          <button className="job-log-refresh" onClick={(e) => { e.stopPropagation(); fetchLog(t.task_id, t.try_number || 1); }}>Refresh</button>
                        </>
                      );
                    })()}
                  </li>
                )}
              </React.Fragment>
            );
          })}
        </ol>

        {toast && (
          <div className={`job-toast job-toast-${toast.tone}`}>
            <span className="job-toast-icon" aria-hidden>
              {toast.tone === 'failed' ? <XIcon /> : <RetryIcon />}
            </span>
            <span className="job-toast-text">{toast.title}</span>
            <button
              type="button"
              className="job-toast-cta"
              onClick={() => { toggleLog({ task_id: toast.taskId, try_number: toast.tryNum }); setToast(null); }}
            >View log</button>
            <button
              type="button"
              className="job-toast-close"
              onClick={() => setToast(null)}
              aria-label="Dismiss"
            >×</button>
          </div>
        )}

        {(canRestart || canStop || canResume || actionError) && (
          <div className="job-panel-footer">
            {actionError && (() => {
              const pretty = prettifyError(actionError);
              return (
                <span className="job-panel-action-error" title={pretty.raw || actionError}>
                  <strong>{pretty.title}</strong> — {pretty.detail}
                </span>
              );
            })()}
            {canStop && (
              <button
                className="draw-card-btn draw-card-btn-danger"
                onClick={stopRun}
                disabled={!!actionPending}
                title="Stop the pipeline run"
              >
                {actionPending === 'Stopping' ? 'Stopping…' : 'Stop pipeline'}
              </button>
            )}
            {canResume && (
              <button
                className="draw-card-btn draw-card-btn-primary"
                onClick={resumeRun}
                disabled={!!actionPending}
                title="Resume from the first task that hasn't completed"
              >
                {actionPending === 'Resuming' ? 'Resuming…' : 'Continue'}
              </button>
            )}
            {hasFailedTask && (
              <button
                className="draw-card-btn draw-card-btn-ghost"
                onClick={retryFailed}
                disabled={!!actionPending}
              >
                {actionPending === 'Retrying' ? 'Retrying…' : 'Retry failed task'}
              </button>
            )}
            {canRestart && (
              <button
                className="draw-card-btn draw-card-btn-ghost"
                onClick={restartRun}
                disabled={!!actionPending}
              >
                {actionPending === 'Restarting' ? 'Restarting…' : 'Restart pipeline'}
              </button>
            )}
            {finished && (
              <button
                className="draw-card-btn draw-card-btn-primary"
                onClick={onClose}
              >
                {runState === 'success' ? 'View results' : 'Close'}
              </button>
            )}
          </div>
        )}

        <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} />
      </div>
    );
  }

  if (minimized) {
    const pillStyle = pillPos
      ? { left: `${pillPos.x}px`, top: `${pillPos.y}px`, right: 'auto', bottom: 'auto' }
      : { bottom: `${16 + index * 56}px` };
    const hasFailure   = status?.tasks?.some((t) => FAILURE_STATES.has(t.state)) || runState === 'failed';
    const failureCount = status?.tasks?.filter((t) => FAILURE_STATES.has(t.state)).length || 0;
    return (
      <div
        className={`job-pill job-pill-${stateTone} ${hasFailure ? 'has-failure' : ''}`}
        style={pillStyle}
        onPointerDown={onPillPointerDown}
        onClick={onPillClick}
        title={hasFailure ? 'Failure detected — click to view' : 'Click to open · drag to move'}
        role="button"
        tabIndex={0}
      >
        {hasFailure && <span className="job-pill-alert" aria-hidden>!</span>}
        {failureCount > 1 && (
          <span className="job-pill-badge">{failureCount}</span>
        )}
        <span className="job-pill-ring" aria-hidden>
          <svg width="36" height="36" viewBox="0 0 36 36">
            <circle cx="18" cy="18" r="15" className="job-pill-ring-track" />
            <circle
              cx="18" cy="18" r="15"
              className="job-pill-ring-fill"
              strokeDasharray={2 * Math.PI * 15}
              strokeDashoffset={2 * Math.PI * 15 * (1 - percent / 100)}
            />
          </svg>
          <span className="job-pill-pct">{percent}%</span>
        </span>
        <span className="job-pill-text">
          <span className="job-pill-headline">
            {runState === 'success' ? 'Done'
              : runState === 'failed' ? 'Failed'
              : (status?.current || 'Starting…')}
          </span>
          {areaName && <span className="job-pill-sub">{areaName}</span>}
        </span>
        <button
          type="button"
          className="job-pill-dismiss"
          onClick={(e) => { e.stopPropagation(); onClose?.(); }}
          aria-label="Dismiss"
        >×</button>
      </div>
    );
  }

  const panelStyle = { top: `${16 + index * 16}px`, zIndex: 7 + index };

  return (
    <div className={`job-panel ${shiftClass}`} style={panelStyle}>
      <div className="job-panel-head">
        <div className="job-panel-eyebrow">
          <span className={`job-state-dot job-state-${stateTone}`} />
          <span>{areaName ? `${areaName} · ` : ''}Run {runId.slice(-8)}</span>
        </div>
        <div className="job-panel-head-actions">
          <button
            className="job-panel-min"
            onClick={() => setMinimized(true)}
            aria-label="Minimize"
            title="Minimize to pill"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden>
              <line x1="5" y1="14" x2="19" y2="14" />
            </svg>
          </button>
          <button className="job-panel-close" onClick={onClose} aria-label="Close">×</button>
        </div>
      </div>

      <h3 className="job-panel-title">{headline}</h3>

      <div className="job-progress">
        <div
          className={`job-progress-bar job-progress-bar-${stateTone}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="job-progress-meta">
        <span>{status?.progress?.completed ?? 0} / {status?.progress?.total ?? 15} steps</span>
        <span>{fmtElapsed(status?.start_date, status?.end_date)}</span>
      </div>

      {reconnecting && !error && (
        <div className="job-reconnect" role="status">
          <span className="job-reconnect-dot" aria-hidden />
          Reconnecting to Airflow… the run is still going.
        </div>
      )}

      {error && <div className="job-panel-error">{error}</div>}

      <ol className="job-steps">
        {(status?.tasks || []).map((t) => {
          const tone        = STATE_TONE[t.state] || 'idle';
          const failureLike = FAILURE_STATES.has(t.state);
          const key         = `${t.task_id}#${t.try_number || 1}`;
          const opened      = openLogFor === key;
          const log         = logsByTask[key];
          const inspectable = failureLike || t.state === 'success' || t.state === 'running';
          return (
            <React.Fragment key={t.task_id}>
              <li
                className={`job-step job-step-${tone} ${inspectable ? 'is-clickable' : ''} ${opened ? 'is-open' : ''}`}
                onClick={inspectable ? () => toggleLog(t) : undefined}
                role={inspectable ? 'button' : undefined}
                tabIndex={inspectable ? 0 : -1}
                title={inspectable ? 'Click to view logs' : ''}
              >
                <span className="job-step-marker" aria-hidden>
                  {t.state === 'success'      && <CheckIcon />}
                  {t.state === 'failed'       && <XIcon />}
                  {t.state === 'up_for_retry' && <RetryIcon />}
                  {t.state === 'skipped'      && <DashIcon />}
                  {t.state === 'running'      && <span className="job-step-spinner" />}
                  {(t.state === 'queued' || t.state === 'scheduled') && <DotIcon />}
                  {!t.state && <DotIcon />}
                </span>
                <span className="job-step-label">
                  {t.label}
                  {t.state === 'up_for_retry' && <span className="job-step-tag">retry {t.try_number || 1}</span>}
                </span>
                <span className="job-step-elapsed">
                  {t.start_date ? fmtElapsed(t.start_date, t.end_date) : ''}
                </span>
              </li>
              {opened && (
                <li className="job-step-log" aria-live="polite">
                  {(!log || log.loading) && <span className="job-log-info">Loading logs…</span>}
                  {log?.error && <span className="job-log-error">Failed to load: {log.error}</span>}
                  {log?._success && (
                    <div className="job-log-success">
                      <span className="job-log-success-icon" aria-hidden>✓</span>
                      <span>Completed successfully</span>
                      {t.start_date && <span className="job-log-meta">{fmtElapsed(t.start_date, t.end_date)}</span>}
                    </div>
                  )}
                  {log?.lines && (() => {
                    const hints = errorHint(log.lines);
                    const isFailed = t.state === 'failed' || t.state === 'up_for_retry';
                    return isFailed ? (
                      <div className="job-log-fail">
                        <div className="job-log-fail-head">
                          <span className="job-log-fail-icon" aria-hidden>✗</span>
                          <span>Task failed{t.start_date ? ` · ${fmtElapsed(t.start_date, t.end_date)}` : ''}</span>
                        </div>
                        {hints.length ? hints.map((l, i) => (
                          <div key={i} className="job-log-fail-hint">{l}</div>
                        )) : (
                          <div className="job-log-fail-hint job-log-fail-hint-muted">No additional details available</div>
                        )}
                        <button className="job-log-refresh" onClick={(e) => { e.stopPropagation(); fetchLog(t.task_id, t.try_number || 1); }}>Refresh</button>
                      </div>
                    ) : (
                      <>
                        {t.state === 'running' && (
                          <div className="job-log-live-head">
                            <span className="job-log-live-dot" aria-hidden />
                            <span>Live output</span>
                          </div>
                        )}
                        <div className="job-log-lines">
                          {log.lines.length
                            ? log.lines.map((l, i) => <div key={i} className="job-log-line">{l}</div>)
                            : <span className="job-log-info">No output yet</span>}
                        </div>
                        <button className="job-log-refresh" onClick={(e) => { e.stopPropagation(); fetchLog(t.task_id, t.try_number || 1); }}>Refresh</button>
                      </>
                    );
                  })()}
                </li>
              )}
            </React.Fragment>
          );
        })}
      </ol>

      {toast && (
        <div className={`job-toast job-toast-${toast.tone}`}>
          <span className="job-toast-icon" aria-hidden>
            {toast.tone === 'failed' ? <XIcon /> : <RetryIcon />}
          </span>
          <span className="job-toast-text">{toast.title}</span>
          <button
            type="button"
            className="job-toast-cta"
            onClick={() => {
              toggleLog({ task_id: toast.taskId, try_number: toast.tryNum });
              setToast(null);
            }}
          >View log</button>
          <button
            type="button"
            className="job-toast-close"
            onClick={() => setToast(null)}
            aria-label="Dismiss"
          >×</button>
        </div>
      )}

      {(canRestart || canStop || canResume || actionError) && (
        <div className="job-panel-footer">
          {actionError && (
            <span className="job-panel-action-error" title={actionError}>{actionError}</span>
          )}
          {canStop && (
            <button
              className="draw-card-btn draw-card-btn-danger"
              onClick={stopRun}
              disabled={!!actionPending}
              title="Stop the pipeline run"
            >
              {actionPending === 'Stopping' ? 'Stopping…' : 'Stop pipeline'}
            </button>
          )}
          {canResume && (
            <button
              className="draw-card-btn draw-card-btn-primary"
              onClick={resumeRun}
              disabled={!!actionPending}
              title="Resume from the first task that hasn't completed"
            >
              {actionPending === 'Resuming' ? 'Resuming…' : 'Continue'}
            </button>
          )}
          {hasFailedTask && (
            <button
              className="draw-card-btn draw-card-btn-ghost"
              onClick={retryFailed}
              disabled={!!actionPending}
              title="Re-run only the failed / retrying task and its downstream"
            >
              {actionPending === 'Retrying' ? 'Retrying…' : 'Retry failed task'}
            </button>
          )}
          {canRestart && (
            <button
              className="draw-card-btn draw-card-btn-ghost"
              onClick={restartRun}
              disabled={!!actionPending}
              title="Clear every task and run the pipeline again from scratch"
            >
              {actionPending === 'Restarting' ? 'Restarting…' : 'Restart pipeline'}
            </button>
          )}
          {finished && (
            <button
              className="draw-card-btn draw-card-btn-primary"
              onClick={onClose}
            >
              {runState === 'success' ? 'View results' : 'Close'}
            </button>
          )}
        </div>
      )}

      <ConfirmDialog confirm={confirm} onCancel={() => setConfirm(null)} />
    </div>
  );
}


function errorHint(lines) {
  if (!lines?.length) return [];
  const errIdx = lines.reduce((acc, l, i) => {
    if (/traceback|error:|exception:|failed|critical/i.test(l)) acc.push(i);
    return acc;
  }, []);
  const start = errIdx.length ? errIdx[errIdx.length - 1] : Math.max(0, lines.length - 4);
  return lines.slice(start)
    .filter((l) => /error|exception|failed|traceback|critical/i.test(l))
    .slice(0, 4);
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <polyline points="5 12 10 17 19 7" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden>
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
    </svg>
  );
}

function DashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" aria-hidden>
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function RetryIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 12a9 9 0 0 1 15.5-6.3L21 8" />
      <polyline points="21 3 21 8 16 8" />
      <path d="M21 12a9 9 0 0 1-15.5 6.3L3 16" />
      <polyline points="3 21 3 16 8 16" />
    </svg>
  );
}

function DotIcon() {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden>
      <circle cx="5" cy="5" r="3" fill="currentColor" />
    </svg>
  );
}
