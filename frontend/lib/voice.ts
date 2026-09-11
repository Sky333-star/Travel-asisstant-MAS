/**
 * Voice input and output.
 *
 * TWO-TIER SPEECH INPUT
 * ---------------------
 * 1. **Web Speech API** where available (Chrome, Edge, Safari). It is free,
 *    runs without a round trip, and streams interim results so the user sees
 *    their words appear as they speak -- which is what makes voice input feel
 *    responsive rather than laggy.
 * 2. **MediaRecorder + server Whisper** everywhere else (notably Firefox).
 *    The clip is recorded locally and posted to `/api/voice/transcribe`.
 *
 * The caller does not choose: `createSpeechInput()` returns whichever tier is
 * supported, behind one interface.
 *
 * Speech output uses `speechSynthesis`, which is universally available and
 * costs nothing.
 */

import { transcribeAudio } from './api';

// The Web Speech API is not in the standard TS DOM lib, so declare the parts
// we use rather than pulling in a dependency for a handful of members.
interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}
interface SpeechRecognitionResult {
  readonly length: number;
  isFinal: boolean;
  [index: number]: SpeechRecognitionAlternative;
}
interface SpeechRecognitionResultList {
  readonly length: number;
  [index: number]: SpeechRecognitionResult;
}
interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}
interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message?: string;
}
interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}
type SpeechRecognitionConstructor = new () => SpeechRecognitionLike;

declare global {
  interface Window {
    SpeechRecognition?: SpeechRecognitionConstructor;
    webkitSpeechRecognition?: SpeechRecognitionConstructor;
  }
}

export type VoiceTier = 'web-speech' | 'recorder' | 'unavailable';

export interface SpeechInputCallbacks {
  /** Live partial transcript, for showing words as they are spoken. */
  onInterim?: (text: string) => void;
  /** The finished transcript. */
  onFinal: (text: string) => void;
  onError?: (message: string) => void;
  onStart?: () => void;
  onStop?: () => void;
}

export interface SpeechInput {
  tier: VoiceTier;
  start: () => Promise<void>;
  stop: () => void;
  isRecording: () => boolean;
}

export function detectVoiceTier(): VoiceTier {
  if (typeof window === 'undefined') return 'unavailable';
  if (window.SpeechRecognition || window.webkitSpeechRecognition) return 'web-speech';
  if (typeof navigator !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
    return 'recorder';
  }
  return 'unavailable';
}

function createWebSpeechInput(
  callbacks: SpeechInputCallbacks,
  locale: string,
): SpeechInput {
  const Constructor = (window.SpeechRecognition ??
    window.webkitSpeechRecognition) as SpeechRecognitionConstructor;

  let recognition: SpeechRecognitionLike | null = null;
  let recording = false;
  let finalText = '';

  return {
    tier: 'web-speech',
    isRecording: () => recording,

    async start() {
      if (recording) return;
      finalText = '';
      recognition = new Constructor();
      recognition.lang = locale;
      // `continuous` lets someone describe a whole trip without the browser
      // cutting them off at the first pause.
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;

      recognition.onstart = () => {
        recording = true;
        callbacks.onStart?.();
      };

      recognition.onresult = (event: SpeechRecognitionEvent) => {
        let interim = '';
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i];
          if (!result) continue;
          const alternative = result[0];
          if (!alternative) continue;
          if (result.isFinal) {
            finalText += `${alternative.transcript} `;
          } else {
            interim += alternative.transcript;
          }
        }
        if (interim) callbacks.onInterim?.((finalText + interim).trim());
      };

      recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        recording = false;
        // "no-speech" and "aborted" are normal outcomes, not failures worth
        // showing the user an error banner for.
        if (event.error === 'no-speech' || event.error === 'aborted') {
          callbacks.onStop?.();
          return;
        }
        const messages: Record<string, string> = {
          'not-allowed': 'Microphone access was denied. Enable it in your browser settings.',
          'service-not-allowed': 'Speech recognition is blocked by your browser.',
          network: 'Speech recognition needs a network connection.',
          'audio-capture': 'No microphone was found.',
        };
        callbacks.onError?.(messages[event.error] ?? `Speech error: ${event.error}`);
      };

      recognition.onend = () => {
        recording = false;
        const text = finalText.trim();
        if (text) callbacks.onFinal(text);
        callbacks.onStop?.();
      };

      try {
        recognition.start();
      } catch (error) {
        recording = false;
        callbacks.onError?.(
          error instanceof Error ? error.message : 'Could not start speech recognition.',
        );
      }
    },

    stop() {
      if (recognition && recording) {
        // `stop()` (not `abort()`) flushes the final result first.
        recognition.stop();
      }
    },
  };
}

function createRecorderInput(callbacks: SpeechInputCallbacks): SpeechInput {
  let recorder: MediaRecorder | null = null;
  let stream: MediaStream | null = null;
  let chunks: Blob[] = [];
  let recording = false;

  const releaseMicrophone = () => {
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
  };

  return {
    tier: 'recorder',
    isRecording: () => recording,

    async start() {
      if (recording) return;
      chunks = [];
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch {
        callbacks.onError?.(
          'Microphone access was denied. Enable it in your browser settings.',
        );
        return;
      }

      // Opus in WebM is what Whisper handles best and what browsers produce.
      const preferred = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
      const mimeType = preferred.find((type) => MediaRecorder.isTypeSupported(type));

      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data);
      };

      recorder.onstop = async () => {
        recording = false;
        releaseMicrophone();
        callbacks.onStop?.();

        const blob = new Blob(chunks, { type: mimeType ?? 'audio/webm' });
        if (blob.size < 1200) {
          callbacks.onError?.('That recording was too short to transcribe.');
          return;
        }
        callbacks.onInterim?.('Transcribing...');
        try {
          const text = await transcribeAudio(blob);
          if (text) callbacks.onFinal(text);
          else callbacks.onError?.('No speech was detected.');
        } catch (error) {
          callbacks.onError?.(
            error instanceof Error ? error.message : 'Transcription failed.',
          );
        }
      };

      recorder.start();
      recording = true;
      callbacks.onStart?.();
    },

    stop() {
      if (recorder && recording) recorder.stop();
    },
  };
}

export function createSpeechInput(
  callbacks: SpeechInputCallbacks,
  locale = 'en-GB',
): SpeechInput {
  const tier = detectVoiceTier();
  if (tier === 'web-speech') return createWebSpeechInput(callbacks, locale);
  if (tier === 'recorder') return createRecorderInput(callbacks);

  return {
    tier: 'unavailable',
    isRecording: () => false,
    async start() {
      callbacks.onError?.('Voice input is not supported in this browser.');
    },
    stop() {
      /* nothing to stop */
    },
  };
}

// -------------------------------------------------------------- speech out

export function canSpeak(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

/**
 * Read text aloud.
 *
 * Long agent replies are trimmed: nobody wants a synthesised voice reading a
 * seven-day itinerary line by line, and the plan is on screen anyway.
 */
export function speak(text: string, locale = 'en-GB'): void {
  if (!canSpeak() || !text.trim()) return;

  window.speechSynthesis.cancel();

  const spoken = text.length > 600 ? `${text.slice(0, 600).trimEnd()}...` : text;
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.lang = locale;
  utterance.rate = 1.02;
  utterance.pitch = 1.0;

  const voices = window.speechSynthesis.getVoices();
  const match =
    voices.find((voice) => voice.lang === locale && voice.localService) ??
    voices.find((voice) => voice.lang.startsWith(locale.split('-')[0] ?? 'en'));
  if (match) utterance.voice = match;

  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}
