'use client';

import { useEffect, useRef } from 'react';

import type { ChatMessage } from '@/lib/types';

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
  stageLabel: string;
  error: string | null;
}

/** One message. Assistant text preserves line breaks; the agents use them. */
function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';

  return (
    <div
      className={`flex animate-slide-up ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div className={`max-w-[88%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div
          className={`rounded-2xl px-3.5 py-2.5 text-[13.5px] leading-relaxed ${
            isUser ? 'text-white' : 'surface'
          }`}
          style={isUser ? { backgroundColor: 'rgb(var(--accent))' } : undefined}
        >
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
        {isUser && message.viaVoice ? (
          <p className="mt-1 pr-1 text-right text-[10.5px] muted">
            <span aria-hidden>🎙</span> spoken
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default function ChatPanel({ messages, streaming, stageLabel, error }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);

  // Auto-scroll, but only while the user is already at the bottom. Yanking
  // someone back down while they are reading an earlier message is worse than
  // making them scroll.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const onScroll = () => {
      const distance =
        container.scrollHeight - container.scrollTop - container.clientHeight;
      pinnedRef.current = distance < 90;
    };
    container.addEventListener('scroll', onScroll, { passive: true });
    return () => container.removeEventListener('scroll', onScroll);
  }, []);

  useEffect(() => {
    if (pinnedRef.current) {
      endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages, stageLabel, error]);

  return (
    <div
      ref={containerRef}
      className="flex-1 space-y-3 overflow-y-auto p-4"
      role="log"
      aria-live="polite"
      aria-label="Conversation"
    >
      {messages.length === 0 ? (
        <div className="flex h-full items-center justify-center px-6 text-center">
          <div>
            <p className="text-sm font-medium">Where would you like to go?</p>
            <p className="mt-1.5 text-[13px] muted">
              Describe the trip in your own words. Tell me where you are flying
              from and roughly when, and I will handle the rest.
            </p>
          </div>
        </div>
      ) : null}

      {messages.map((message) => (
        <Bubble key={message.id} message={message} />
      ))}

      {streaming ? (
        <div className="flex justify-start animate-fade-in">
          <div className="surface flex items-center gap-2.5 rounded-2xl px-3.5 py-2.5">
            <span className="flex gap-1" aria-hidden>
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="h-1.5 w-1.5 rounded-full animate-pulse-soft"
                  style={{
                    backgroundColor: 'rgb(var(--accent))',
                    animationDelay: `${i * 180}ms`,
                  }}
                />
              ))}
            </span>
            <span className="text-[12.5px] muted">
              {stageLabel || 'Thinking'}
            </span>
          </div>
        </div>
      ) : null}

      {error ? (
        <div
          role="alert"
          className="rounded-lg border px-3 py-2.5 text-[12.5px]"
          style={{ borderColor: '#b91c1c55', backgroundColor: '#b91c1c12', color: '#b91c1c' }}
        >
          {error}
        </div>
      ) : null}

      <div ref={endRef} />
    </div>
  );
}
