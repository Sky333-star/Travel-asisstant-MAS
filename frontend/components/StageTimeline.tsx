'use client';

import { STAGE_LABELS, TIMELINE_STAGES } from '@/lib/format';
import type { Stage } from '@/lib/types';

interface Props {
  current: Stage;
  visited: Stage[];
  streaming: boolean;
  label: string;
}

/**
 * A progress timeline over the agent pipeline.
 *
 * This exists because a multi-agent run takes tens of seconds across a dozen
 * tool calls, and a bare spinner tells the user nothing about whether the
 * system is working or wedged. Showing which specialist is running -- and that
 * the weather check really is a separate step from the shortlist -- also makes
 * the architecture legible without a diagram.
 */
export default function StageTimeline({ current, visited, streaming, label }: Props) {
  const currentIndex = TIMELINE_STAGES.indexOf(
    current as (typeof TIMELINE_STAGES)[number],
  );

  const statusOf = (stage: string, index: number) => {
    if (stage === current) return 'active';
    if (visited.includes(stage as Stage)) return 'done';
    // Stages the run has moved past without emitting (e.g. skipped clarify).
    if (currentIndex > -1 && index < currentIndex) return 'done';
    return 'pending';
  };

  return (
    <div className="card p-3.5 animate-fade-in">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[12px] font-semibold uppercase tracking-wide muted">
          Agent pipeline
        </h2>
        {streaming ? (
          <span className="text-[11.5px]" style={{ color: 'rgb(var(--accent))' }}>
            {label || STAGE_LABELS[current] || 'Working'}
          </span>
        ) : (
          <span className="text-[11.5px] muted">
            {current === 'done' ? 'Complete' : STAGE_LABELS[current] ?? ''}
          </span>
        )}
      </div>

      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2">
        {TIMELINE_STAGES.map((stage, index) => {
          const status = statusOf(stage, index);
          return (
            <li key={stage} className="flex items-center gap-1">
              <div
                className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11.5px] transition-colors"
                style={{
                  backgroundColor:
                    status === 'active'
                      ? 'rgb(var(--accent) / 0.12)'
                      : status === 'done'
                        ? 'rgb(var(--surface-sunken))'
                        : 'transparent',
                  color:
                    status === 'active'
                      ? 'rgb(var(--accent))'
                      : status === 'done'
                        ? 'rgb(var(--text))'
                        : 'rgb(var(--text-muted))',
                  border:
                    status === 'pending'
                      ? '1px dashed rgb(var(--border))'
                      : '1px solid transparent',
                }}
                aria-current={status === 'active' ? 'step' : undefined}
              >
                <span aria-hidden className="flex h-3.5 w-3.5 items-center justify-center">
                  {status === 'done' ? (
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M20 6L9 17l-5-5" />
                    </svg>
                  ) : status === 'active' ? (
                    <span
                      className="h-2 w-2 rounded-full animate-pulse-soft"
                      style={{ backgroundColor: 'rgb(var(--accent))' }}
                    />
                  ) : (
                    <span
                      className="h-1.5 w-1.5 rounded-full"
                      style={{ backgroundColor: 'rgb(var(--border))' }}
                    />
                  )}
                </span>
                {STAGE_LABELS[stage]}
              </div>

              {index < TIMELINE_STAGES.length - 1 ? (
                <span aria-hidden className="muted text-[10px]">
                  ›
                </span>
              ) : null}
            </li>
          );
        })}
      </ol>

      {current === 'awaiting_choice' && !streaming ? (
        <p className="mt-3 text-[12px]" style={{ color: 'rgb(var(--accent))' }}>
          Waiting for you to pick a destination.
        </p>
      ) : null}
      {current === 'revising' ? (
        <p className="mt-3 text-[12px]" style={{ color: '#b45309' }}>
          The validator rejected the first plan; the planner is trying again.
        </p>
      ) : null}
    </div>
  );
}
