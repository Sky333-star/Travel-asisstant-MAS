'use client';

import { titleCase } from '@/lib/format';
import type { Severity, ValidationReport } from '@/lib/types';

interface Props {
  report: ValidationReport;
}

const SEVERITY_STYLE: Record<Severity, { colour: string; label: string }> = {
  blocker: { colour: '#b91c1c', label: 'Blocking' },
  warning: { colour: '#b45309', label: 'Worth knowing' },
  info: { colour: 'rgb(var(--text-muted))', label: 'Note' },
};

/**
 * The validator's verdict on the plan.
 *
 * Showing this is a deliberate product decision. Most planners present a plan
 * as finished and let the user discover the problems; this one states what it
 * checked, what it found, and how many attempts it took. A plan that admits
 * "this is 240 EUR over your budget" is more useful than one that quietly is.
 */
export default function ValidationPanel({ report }: Props) {
  const blockers = report.issues.filter((i) => i.severity === 'blocker');
  const warnings = report.issues.filter((i) => i.severity === 'warning');
  const infos = report.issues.filter((i) => i.severity === 'info');

  const tone = report.passed
    ? blockers.length === 0 && warnings.length === 0
      ? '#15803d'
      : '#b45309'
    : '#b91c1c';

  return (
    <div className="card overflow-hidden">
      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5"
        style={{ backgroundColor: `${tone}12` }}
      >
        <span
          aria-hidden
          className="flex h-5 w-5 items-center justify-center rounded-full text-white"
          style={{ backgroundColor: tone }}
        >
          {report.passed ? (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M20 6L9 17l-5-5" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 8v5M12 17h.01" />
            </svg>
          )}
        </span>

        <p className="text-[13px] font-medium" style={{ color: tone }}>
          {report.summary || (report.passed ? 'Plan validated' : 'Plan has issues')}
        </p>

        <span className="ml-auto flex items-center gap-2 text-[11.5px] muted">
          <span title="Overall plan quality, reduced by each issue found.">
            Score {Math.round(report.score)}/100
          </span>
          {report.revision > 0 ? (
            <span title="The validator rejected earlier drafts and the planner revised them.">
              · {report.revision} {report.revision === 1 ? 'revision' : 'revisions'}
            </span>
          ) : null}
        </span>
      </div>

      {report.issues.length > 0 ? (
        <ul className="divide-y" style={{ borderColor: 'rgb(var(--border))' }}>
          {[...blockers, ...warnings, ...infos].map((issue, index) => {
            const style = SEVERITY_STYLE[issue.severity];
            return (
              <li
                key={`${issue.code}-${index}`}
                className="flex gap-2.5 px-4 py-2.5"
                style={{ borderColor: 'rgb(var(--border))' }}
              >
                <span
                  aria-hidden
                  className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: style.colour }}
                />
                <div className="min-w-0">
                  <p className="text-[12.5px]">
                    <span className="font-medium" style={{ color: style.colour }}>
                      {style.label}:{' '}
                    </span>
                    {issue.message}
                  </p>
                  {issue.detail ? (
                    <p className="mt-0.5 text-[11.5px] muted">{issue.detail}</p>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}

      {report.checks_run.length > 0 ? (
        <div
          className="flex flex-wrap items-center gap-1.5 border-t px-4 py-2.5"
          style={{ borderColor: 'rgb(var(--border))' }}
        >
          <span className="text-[11px] muted">Checks run:</span>
          {report.checks_run.map((check) => (
            <span key={check} className="chip">
              {check === 'llm_critique' ? 'LLM review' : titleCase(check)}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
