'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

import { createSpeechInput, detectVoiceTier, type SpeechInput, type VoiceTier } from '@/lib/voice';

interface Props {
  onSubmit: (message: string, viaVoice: boolean) => void;
  disabled: boolean;
  placeholder: string;
  examples?: string[];
}

/**
 * The message composer, with voice input.
 *
 * The voice interaction is press-to-talk rather than always-listening: an
 * assistant that holds the microphone open is both a privacy problem and a
 * reliability one. While recording, interim transcript text appears in the
 * textarea so the user can see what was heard before it is sent.
 */
export default function Composer({ onSubmit, disabled, placeholder, examples }: Props) {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [tier, setTier] = useState<VoiceTier>('unavailable');
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const speechRef = useRef<SpeechInput | null>(null);
  // Tracks whether the pending submission came from speech, so the backend can
  // apply transcript clean-up to it.
  const viaVoiceRef = useRef(false);

  useEffect(() => setTier(detectVoiceTier()), []);

  // Grow the textarea with its content, up to a limit.
  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = 'auto';
    element.style.height = `${Math.min(element.scrollHeight, 160)}px`;
  }, [text]);

  const submit = useCallback(() => {
    const value = text.trim();
    if (!value || disabled) return;
    onSubmit(value, viaVoiceRef.current);
    setText('');
    viaVoiceRef.current = false;
  }, [disabled, onSubmit, text]);

  const startVoice = useCallback(() => {
    setVoiceError(null);
    speechRef.current = createSpeechInput({
      onStart: () => setRecording(true),
      onStop: () => setRecording(false),
      onInterim: (partial) => setText(partial),
      onFinal: (final) => {
        viaVoiceRef.current = true;
        setText(final);
        // Focus rather than auto-send: speech recognition mishears, and
        // sending an unreviewed transcript is how you end up planning a trip
        // to the wrong city.
        requestAnimationFrame(() => inputRef.current?.focus());
      },
      onError: (message) => {
        setRecording(false);
        setVoiceError(message);
      },
    });
    void speechRef.current.start();
  }, []);

  const stopVoice = useCallback(() => {
    speechRef.current?.stop();
    setRecording(false);
  }, []);

  const toggleVoice = () => (recording ? stopVoice() : startVoice());

  useEffect(() => () => speechRef.current?.stop(), []);

  return (
    <div
      className="border-t p-3"
      style={{ borderColor: 'rgb(var(--border))' }}
    >
      {examples && examples.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-1.5">
          <span className="text-[11px] muted">For example:</span>
          {examples.slice(0, 3).map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => setText(example)}
              className="chip transition-colors hover:!text-[rgb(var(--text))]"
              disabled={disabled}
            >
              {example}
            </button>
          ))}
        </div>
      ) : null}

      {voiceError ? (
        <p role="alert" className="mb-2 text-[11.5px]" style={{ color: '#b45309' }}>
          {voiceError}
        </p>
      ) : null}

      <div className="flex items-end gap-2">
        <textarea
          ref={inputRef}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter is a newline, as everywhere else.
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
          rows={1}
          disabled={disabled}
          placeholder={recording ? 'Listening...' : placeholder}
          aria-label="Message"
          className="sunken flex-1 resize-none rounded-xl border px-3.5 py-2.5 text-[13.5px]
                     outline-none transition-colors placeholder:text-[rgb(var(--text-muted))]
                     disabled:opacity-60"
          style={{
            borderColor: recording ? 'rgb(var(--accent))' : 'rgb(var(--border))',
            color: 'rgb(var(--text))',
          }}
        />

        {tier !== 'unavailable' ? (
          <button
            type="button"
            onClick={toggleVoice}
            disabled={disabled}
            aria-pressed={recording}
            aria-label={recording ? 'Stop recording' : 'Start voice input'}
            title={
              tier === 'web-speech'
                ? 'Speak your request (browser speech recognition)'
                : 'Speak your request (recorded and transcribed on the server)'
            }
            className="btn flex h-[42px] w-[42px] shrink-0 items-center justify-center rounded-xl border transition-colors"
            style={{
              borderColor: recording ? 'transparent' : 'rgb(var(--border))',
              backgroundColor: recording ? '#b91c1c' : 'transparent',
              color: recording ? '#fff' : 'rgb(var(--text-muted))',
            }}
          >
            {recording ? (
              <span className="relative flex h-3.5 w-3.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-sm bg-white opacity-60" />
                <span className="relative inline-flex h-3.5 w-3.5 rounded-sm bg-white" />
              </span>
            ) : (
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
                <path d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" />
              </svg>
            )}
          </button>
        ) : null}

        <button
          type="button"
          onClick={submit}
          disabled={disabled || !text.trim()}
          className="btn-primary h-[42px] w-[42px] shrink-0 !p-0"
          aria-label="Send message"
        >
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>

      <p className="mt-1.5 text-[10.5px] muted">
        {recording
          ? 'Recording. Press the square to stop, then review before sending.'
          : 'Enter to send, Shift+Enter for a new line.'}
      </p>
    </div>
  );
}
