import React, { useCallback, useEffect, useRef, useState } from 'react';
import JobProgress from './JobProgress';

const STATE_TONE = {
  success:      'success',
  failed:       'failed',
  running:      'running',
  queued:       'pending',
  scheduled:    'pending',
  up_for_retry: 'retry',
  skipped:      'skipped',
};

function shortRunId(id = '') {
  return id.slice(-6);
}

export default function JobsPanel({
  jobs,
  onCloseJob,
  onJobSuccess,
  onJobStateChange,
  mcOpen      = false,
  sidebarOpen = false,
}) {
  const [activeId,   setActiveId]   = useState(jobs[0]?.runId || null);
  const [minimized,  setMinimized]  = useState(false);
  const [pillPos,    setPillPos]    = useState(null);
  const dragRef = useRef({ dragging: false, offX: 0, offY: 0, moved: false });

  useEffect(() => {
    if (!jobs.length) {
      setActiveId(null);
      return;
    }
    if (!activeId || !jobs.find((j) => j.runId === activeId)) {
      setActiveId(jobs[jobs.length - 1].runId);
    }
  }, [jobs, activeId]);

  if (!jobs.length) return null;

  const activeJob   = jobs.find((j) => j.runId === activeId) || jobs[0];
  const shiftClass  = `${sidebarOpen ? 'sidebar-open' : ''} ${mcOpen ? 'mc-open' : ''}`.trim();

  const aggregate = (() => {
    const counts = { running: 0, failed: 0, success: 0, pending: 0, retry: 0 };
    let totalPct = 0;
    for (const j of jobs) {
      const tone = STATE_TONE[j.state] || 'pending';
      counts[tone] = (counts[tone] || 0) + 1;
      totalPct += Number(j.percent) || 0;
    }
    const avgPct = jobs.length ? Math.round(totalPct / jobs.length) : 0;
    let primaryTone = 'running';
    if (counts.failed)      primaryTone = 'failed';
    else if (counts.running) primaryTone = 'running';
    else if (counts.retry)   primaryTone = 'retry';
    else if (counts.pending) primaryTone = 'pending';
    else if (counts.success === jobs.length) primaryTone = 'success';
    return { counts, avgPct, primaryTone };
  })();

  const onPillPointerDown = useCallback((e) => {
    if (e.target.closest('.job-pill-dismiss')) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const baseX  = pillPos?.x ?? (window.innerWidth  - 200);
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

  if (minimized) {
    const pillStyle = pillPos
      ? { left: `${pillPos.x}px`, top: `${pillPos.y}px`, right: 'auto', bottom: 'auto' }
      : { bottom: '16px' };
    const hasFailure = aggregate.counts.failed > 0;
    return (
      <div
        className={`job-pill job-pill-${aggregate.primaryTone} ${hasFailure ? 'has-failure' : ''}`}
        style={pillStyle}
        onPointerDown={onPillPointerDown}
        onClick={onPillClick}
        title="Click to open · drag to move"
        role="button"
        tabIndex={0}
      >
        {hasFailure && <span className="job-pill-alert" aria-hidden>!</span>}
        {jobs.length > 1 && <span className="job-pill-badge">{jobs.length}</span>}
        <span className="job-pill-ring" aria-hidden>
          <svg width="36" height="36" viewBox="0 0 36 36">
            <circle cx="18" cy="18" r="15" className="job-pill-ring-track" />
            <circle
              cx="18" cy="18" r="15"
              className="job-pill-ring-fill"
              strokeDasharray={2 * Math.PI * 15}
              strokeDashoffset={2 * Math.PI * 15 * (1 - (aggregate.avgPct / 100))}
            />
          </svg>
          <span className="job-pill-pct">{aggregate.avgPct}%</span>
        </span>
        <span className="job-pill-text">
          <span className="job-pill-headline">
            {jobs.length === 1
              ? (activeJob.areaName || 'Analysis')
              : `${jobs.length} analyses`}
          </span>
          <span className="job-pill-sub">
            {aggregate.counts.running ? `${aggregate.counts.running} running · ` : ''}
            {aggregate.counts.failed  ? `${aggregate.counts.failed} failed · `  : ''}
            {aggregate.counts.success ? `${aggregate.counts.success} done`      : ''}
          </span>
        </span>
      </div>
    );
  }

  return (
    <div className={`job-panel jobs-multi ${shiftClass}`} style={{ top: '16px' }}>
      <div className="job-panel-head">
        <div className="job-panel-eyebrow">
          <span className={`job-state-dot job-state-${STATE_TONE[activeJob.state] || 'pending'}`} />
          <span>
            {activeJob.areaName || `Run ${shortRunId(activeJob.runId)}`}
            {jobs.length > 1 && <span className="job-panel-count">  ·  {jobs.length} jobs</span>}
          </span>
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
          <button
            className="job-panel-close"
            onClick={() => onCloseJob(activeJob.runId)}
            aria-label="Close current job"
            title="Close this job"
          >×</button>
        </div>
      </div>

      <div className="job-tabs" role="tablist">
        {jobs.map((j) => {
          const tone   = STATE_TONE[j.state] || 'pending';
          const active = j.runId === activeId;
          return (
            <button
              key={j.runId}
              role="tab"
              className={`job-tab job-tab-${tone} ${active ? 'is-active' : ''}`}
              aria-selected={active}
              onClick={() => setActiveId(j.runId)}
              title={`Run ${j.runId}`}
            >
              <span className={`job-state-dot job-state-${tone}`} />
              <span className="job-tab-label">
                {j.areaName || `Run ${shortRunId(j.runId)}`}
              </span>
              <span
                className="job-tab-close"
                role="button"
                tabIndex={0}
                onClick={(e) => { e.stopPropagation(); onCloseJob(j.runId); }}
                aria-label={`Close ${j.areaName || j.runId}`}
                title="Close this tab"
              >×</span>
            </button>
          );
        })}
      </div>

      <div className="job-tab-stack">
        {jobs.map((j) => (
          <JobProgress
            key={j.runId}
            job={j}
            tabbedMode
            active={j.runId === activeId}
            mcOpen={mcOpen}
            sidebarOpen={sidebarOpen}
            onClose={() => onCloseJob(j.runId)}
            onSuccess={(payload) => onJobSuccess?.(j, payload)}
            onStateChange={(state) => onJobStateChange?.(j.runId, state)}
          />
        ))}
      </div>
    </div>
  );
}
