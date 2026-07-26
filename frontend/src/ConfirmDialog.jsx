import React, { useEffect } from 'react';

export default function ConfirmDialog({ confirm, onCancel }) {
  useEffect(() => {
    if (!confirm) return undefined;
    const onKey = (e) => {
      if (e.key === 'Escape') onCancel();
      else if (e.key === 'Enter' && !confirm.actions) confirm.onConfirm?.();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [confirm, onCancel]);

  if (!confirm) return null;

  return (
    <div className="job-confirm-overlay" onClick={onCancel} role="dialog" aria-modal="true">
      <div className="job-confirm-card" onClick={(e) => e.stopPropagation()}>
        <h4 className="job-confirm-title">{confirm.title}</h4>
        <p className="job-confirm-detail">{confirm.detail}</p>
        <div className="job-confirm-actions">
          <button
            type="button"
            className="draw-card-btn draw-card-btn-ghost"
            onClick={onCancel}
          >Cancel</button>

          {confirm.actions
            ? confirm.actions.map((a) => (
                <button
                  key={a.label}
                  type="button"
                  className={`draw-card-btn ${
                    a.tone === 'danger'  ? 'draw-card-btn-danger'  :
                    a.tone === 'ghost'   ? 'draw-card-btn-ghost'   :
                                           'draw-card-btn-primary'
                  }`}
                  onClick={() => a.onClick?.()}
                >{a.label}</button>
              ))
            : (
              <button
                type="button"
                className={`draw-card-btn ${confirm.tone === 'danger' ? 'draw-card-btn-danger' : 'draw-card-btn-primary'}`}
                onClick={() => confirm.onConfirm?.()}
                autoFocus
              >{confirm.confirmLabel || 'Confirm'}</button>
            )}
        </div>
      </div>
    </div>
  );
}
