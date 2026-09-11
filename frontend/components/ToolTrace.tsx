'use client';

import { titleCase } from '@/lib/format';
import type { ToolInvocation, TravelBrief } from '@/lib/types';

interface Props {
  tools: ToolInvocation[];
  brief: TravelBrief | null;
}

const SERVER_COLOUR: Record<string, string> = {
  weather: '#0891b2',
  geo: '#15803d',
  flights: '#1b6ff5',
  hotels: '#7c3aed',
  currency: '#b45309',
};

/**
 * A live view of what the agents actually did.
 *
 * Multi-agent systems are usually opaque: you type a request and a result
 * appears. This panel shows every MCP tool call as it happens, which turns the
 * architecture from a claim into something observable -- useful when
 * debugging, and the fastest way to demonstrate that five separate MCP servers
 * are genuinely being consulted.
 */
export default function ToolTrace({ tools, brief }: Props) {
  const byServer = tools.reduce<Record<string, number>>((acc, tool) => {
    acc[tool.server] = (acc[tool.server] ?? 0) + 1;
    return acc;
  }, {});

  const failures = tools.filter((tool) => !tool.ok).length;

  return (
    <div className="card p-4 animate-fade-in">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-[12px] font-semibold uppercase tracking-wide muted">
          MCP tool trace
        </h3>
        <span className="text-[11.5px] muted">
          {tools.length} {tools.length === 1 ? 'call' : 'calls'}
          {failures > 0 ? ` · ${failures} failed` : ''}
        </span>
      </div>

      {brief ? (
        <div className="sunken mb-3 rounded-lg p-3">
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide muted">
            Extracted brief ({brief.extraction_method})
          </p>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11.5px]">
            <dt className="muted">Origin</dt>
            <dd>{brief.origin ?? '--'}</dd>
            <dt className="muted">Dates</dt>
            <dd>
              {brief.dates.start ?? '--'} to {brief.dates.end ?? '--'}
            </dd>
            <dt className="muted">Party</dt>
            <dd>
              {brief.party.adults} adults
              {brief.party.children ? `, ${brief.party.children} children` : ''}
            </dd>
            <dt className="muted">Budget</dt>
            <dd>
              {brief.budget.amount
                ? `${brief.budget.amount} ${brief.budget.currency} ${
                    brief.budget.per_person ? 'pp' : 'total'
                  }`
                : '--'}
            </dd>
            <dt className="muted">Interests</dt>
            <dd>{brief.interests.join(', ') || '--'}</dd>
            <dt className="muted">Style / pace</dt>
            <dd>
              {brief.style} / {brief.pace}
            </dd>
            {brief.regions.length > 0 ? (
              <>
                <dt className="muted">Regions</dt>
                <dd>{brief.regions.length} sub-regions</dd>
              </>
            ) : null}
            <dt className="muted">Confidence</dt>
            <dd>{Math.round(brief.confidence * 100)}%</dd>
          </dl>
        </div>
      ) : null}

      {Object.keys(byServer).length > 0 ? (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {Object.entries(byServer).map(([server, count]) => (
            <span
              key={server}
              className="chip"
              style={{ color: SERVER_COLOUR[server] ?? 'rgb(var(--text-muted))' }}
            >
              <span
                aria-hidden
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: SERVER_COLOUR[server] ?? 'currentColor' }}
              />
              {server} × {count}
            </span>
          ))}
        </div>
      ) : null}

      {tools.length === 0 ? (
        <p className="text-[12.5px] muted">
          No tool calls yet. Ask for a trip and every MCP call the agents make
          will appear here as it happens.
        </p>
      ) : (
        <ol className="max-h-[380px] space-y-1 overflow-y-auto pr-1">
          {tools.map((tool, index) => (
            <li
              key={`${tool.server}-${tool.tool}-${index}`}
              className="sunken flex items-start gap-2 rounded-md px-2.5 py-1.5"
            >
              <span
                aria-hidden
                className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
                style={{
                  backgroundColor: tool.ok
                    ? (SERVER_COLOUR[tool.server] ?? '#15803d')
                    : '#b91c1c',
                }}
              />
              <div className="min-w-0 flex-1">
                <p className="font-mono text-[11.5px]">
                  <span style={{ color: SERVER_COLOUR[tool.server] ?? 'inherit' }}>
                    {tool.server}
                  </span>
                  <span className="muted">.</span>
                  {tool.tool}
                </p>
                {tool.result_summary ? (
                  <p className="truncate text-[11px] muted">{tool.result_summary}</p>
                ) : null}
                {tool.error ? (
                  <p className="text-[11px]" style={{ color: '#b91c1c' }}>
                    {tool.error}
                  </p>
                ) : null}
              </div>
              {tool.duration_ms ? (
                <span className="shrink-0 text-[10.5px] tabular-nums muted">
                  {Math.round(tool.duration_ms)}ms
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      )}

      <p className="mt-3 border-t pt-2.5 text-[11px] muted"
         style={{ borderColor: 'rgb(var(--border))' }}>
        Each server runs as a separate process and is reached over the Model
        Context Protocol. {titleCase('weather')}, geo, flights, hotels and
        currency are independent -- one failing degrades a feature rather than
        the system.
      </p>
    </div>
  );
}
