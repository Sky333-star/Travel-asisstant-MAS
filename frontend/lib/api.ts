/**
 * Backend client.
 *
 * The chat endpoints stream Server-Sent Events, but they are POST requests, so
 * the browser's `EventSource` (which is GET-only) cannot be used. Instead the
 * response body is read as a stream and framed manually. That is a few more
 * lines than `EventSource`, and in exchange we get to POST a JSON body, set
 * headers, and abort cleanly when the user navigates away.
 */

import type { AppConfig, StreamEvent } from './types';

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export interface SessionSnapshotResponse {
  thread_id: string;
  stage: string;
  awaiting_input: boolean;
  pending_question: { fields: string[]; question: string; examples: string[] } | null;
  brief: unknown;
  recommendation: unknown;
  plan: unknown;
  validation: unknown;
  messages: { role: string; content: string }[];
  revision: number;
  updated_at: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * Parse an SSE byte stream into events.
 *
 * Chunk boundaries fall wherever the network puts them, so a frame can be
 * split across reads. The buffer keeps the trailing partial frame until its
 * terminating blank line arrives -- getting this wrong produces events that
 * silently vanish under load.
 */
async function* parseSSE(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const reader = response.body?.getReader();
  if (!reader) throw new ApiError('Response has no readable body.');

  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let separator = buffer.indexOf('\n\n');
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);

        for (const line of frame.split('\n')) {
          if (line.startsWith('data: ')) {
            try {
              yield JSON.parse(line.slice(6)) as StreamEvent;
            } catch {
              // A malformed frame should not kill the stream.
            }
          }
        }
        separator = buffer.indexOf('\n\n');
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* already released */
    }
  }
}

async function openStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const response = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status);
  }
  return response;
}

/** Start or continue a conversation. Yields events as the agents work. */
export async function* sendMessage(
  message: string,
  threadId: string | null,
  inputMode: 'text' | 'voice' = 'text',
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await openStream(
    '/api/chat',
    { message, thread_id: threadId, input_mode: inputMode },
    signal,
  );
  yield* parseSSE(response, signal);
}

/**
 * Answer a pending question and resume the paused graph.
 *
 * `selectedIndex` is sent when the user clicked a destination card, so the
 * backend does not have to interpret free text it does not need to.
 */
export async function* resumeConversation(
  threadId: string,
  value: string,
  selectedIndex: number | null = null,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await openStream(
    '/api/chat/resume',
    { thread_id: threadId, value, selected_index: selectedIndex },
    signal,
  );
  yield* parseSSE(response, signal);
}

export async function fetchConfig(): Promise<AppConfig> {
  const response = await fetch(`${BASE}/api/config`);
  if (!response.ok) throw new ApiError('Could not load configuration.', response.status);
  return response.json();
}

export async function fetchSession(threadId: string): Promise<SessionSnapshotResponse> {
  const response = await fetch(`${BASE}/api/session/${encodeURIComponent(threadId)}`);
  if (!response.ok) {
    throw new ApiError('Session not found or expired.', response.status);
  }
  return response.json();
}

export async function fetchHealth(): Promise<{
  status: string;
  llm_available: boolean;
  llm_model: string | null;
  mcp_servers: Record<string, string>;
  observability: Record<string, boolean>;
}> {
  const response = await fetch(`${BASE}/health`);
  if (!response.ok) throw new ApiError('Backend unreachable.', response.status);
  return response.json();
}

/** Server-side transcription fallback for browsers without Web Speech. */
export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData();
  form.append('audio', blob, 'recording.webm');

  const response = await fetch(`${BASE}/api/voice/transcribe`, {
    method: 'POST',
    body: form,
  });

  if (!response.ok) {
    let detail = 'Transcription failed.';
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* non-JSON */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()).text as string;
}
