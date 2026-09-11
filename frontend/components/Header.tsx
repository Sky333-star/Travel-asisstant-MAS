'use client';

import { useEffect, useState } from 'react';

import type { AppConfig } from '@/lib/types';

interface Props {
  config: AppConfig | null;
  speakReplies: boolean;
  onToggleSpeak: () => void;
  showTrace: boolean;
  onToggleTrace: () => void;
  onReset: () => void;
  canReset: boolean;
}

/**
 * The application bar.
 *
 * It carries one piece of information that is genuinely load-bearing: whether
 * an LLM is configured. Without a key the system still works on its
 * deterministic path, and saying so plainly is better than letting someone
 * wonder why the prose reads flatter than they expected.
 */
export default function Header({
  config,
  speakReplies,
  onToggleSpeak,
  showTrace,
  onToggleTrace,
  onReset,
  canReset,
}: Props) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem('wayfarer-theme', next ? 'dark' : 'light');
    } catch {
      /* private mode */
    }
  };

  return (
    <header
      className="sticky top-0 z-20 border-b backdrop-blur"
      style={{
        borderColor: 'rgb(var(--border))',
        backgroundColor: 'rgb(var(--surface) / 0.85)',
      }}
    >
      <div className="mx-auto flex w-full max-w-[1600px] items-center gap-3 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span
            aria-hidden
            className="flex h-8 w-8 items-center justify-center rounded-lg text-white"
            style={{ backgroundColor: 'rgb(var(--accent))' }}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M3 11l19-9-9 19-2-8-8-2z" />
            </svg>
          </span>
          <div className="leading-tight">
            <h1 className="text-[15px] font-semibold">Wayfarer</h1>
            <p className="text-[11px] muted">Multi-agent travel planning</p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          {config ? (
            <span
              className="chip hidden sm:inline-flex"
              title={
                config.llm_available
                  ? `Language reasoning by ${config.llm_model}`
                  : 'No HUGGINGFACE_API_KEY is set. The agents are running on the deterministic rule-based path -- fully functional, but the writing is plainer.'
              }
            >
              <span
                aria-hidden
                className="h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: config.llm_available ? '#15803d' : '#b45309' }}
              />
              {config.llm_available ? 'LLM active' : 'Rule-based mode'}
            </span>
          ) : null}

          <button
            type="button"
            onClick={onToggleTrace}
            className="btn-ghost !px-2.5 !py-1.5"
            aria-pressed={showTrace}
            title="Show every MCP tool call the agents made"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M4 6h16M4 12h16M4 18h10" />
            </svg>
            <span className="hidden md:inline">Trace</span>
          </button>

          <button
            type="button"
            onClick={onToggleSpeak}
            className="btn-ghost !px-2.5 !py-1.5"
            aria-pressed={speakReplies}
            title={speakReplies ? 'Stop reading replies aloud' : 'Read replies aloud'}
          >
            {speakReplies ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M11 5L6 9H2v6h4l5 4V5z" />
                <path d="M19.07 4.93a10 10 0 010 14.14M15.54 8.46a5 5 0 010 7.07" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M11 5L6 9H2v6h4l5 4V5z" />
                <path d="M23 9l-6 6M17 9l6 6" />
              </svg>
            )}
            <span className="hidden md:inline">{speakReplies ? 'Voice on' : 'Voice off'}</span>
          </button>

          <button
            type="button"
            onClick={toggleTheme}
            className="btn-ghost !px-2.5 !py-1.5"
            title={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
          >
            {dark ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <circle cx="12" cy="12" r="4" />
                <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" />
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
              </svg>
            )}
          </button>

          {canReset ? (
            <button type="button" onClick={onReset} className="btn-ghost !px-2.5 !py-1.5">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M3 12a9 9 0 109-9 9 9 0 00-6.36 2.64L3 8" />
                <path d="M3 3v5h5" />
              </svg>
              <span className="hidden md:inline">New trip</span>
            </button>
          ) : null}
        </div>
      </div>
    </header>
  );
}
