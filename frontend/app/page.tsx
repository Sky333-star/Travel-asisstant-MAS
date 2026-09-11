'use client';

/**
 * The application shell and its conversation state machine.
 *
 * All streaming state lives here in one reducer rather than being scattered
 * across components. The agents emit a dozen event types over a single
 * conversation, and keeping the transitions in one place is what makes the
 * flow -- ask, clarify, recommend, choose, plan, validate, present --
 * readable instead of emergent.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from 'react';

import ChatPanel from '@/components/ChatPanel';
import Composer from '@/components/Composer';
import DestinationChoice from '@/components/DestinationChoice';
import Header from '@/components/Header';
import PlanView from '@/components/PlanView';
import StageTimeline from '@/components/StageTimeline';
import ToolTrace from '@/components/ToolTrace';
import WelcomePanel from '@/components/WelcomePanel';
import { fetchConfig, resumeConversation, sendMessage } from '@/lib/api';
import { speak, stopSpeaking } from '@/lib/voice';
import type {
  AppConfig,
  ChatMessage,
  PendingQuestion,
  Recommendation,
  Stage,
  StreamEvent,
  ToolInvocation,
  TravelBrief,
  TripPlan,
  ValidationReport,
} from '@/lib/types';

interface ConversationState {
  threadId: string | null;
  stage: Stage;
  stageLabel: string;
  streaming: boolean;
  messages: ChatMessage[];
  brief: TravelBrief | null;
  recommendation: Recommendation | null;
  pendingQuestion: PendingQuestion | null;
  plan: TripPlan | null;
  validation: ValidationReport | null;
  tools: ToolInvocation[];
  error: string | null;
  visitedStages: Stage[];
}

type Action =
  | { type: 'submit'; message: string; viaVoice: boolean }
  | { type: 'stream-start' }
  | { type: 'event'; event: StreamEvent }
  | { type: 'stream-end' }
  | { type: 'error'; message: string }
  | { type: 'reset' };

const INITIAL: ConversationState = {
  threadId: null,
  stage: 'intake',
  stageLabel: '',
  streaming: false,
  messages: [],
  brief: null,
  recommendation: null,
  pendingQuestion: null,
  plan: null,
  validation: null,
  tools: [],
  error: null,
  visitedStages: [],
};

let messageCounter = 0;
const nextId = () => `m${(messageCounter += 1)}`;

function reducer(state: ConversationState, action: Action): ConversationState {
  switch (action.type) {
    case 'submit':
      return {
        ...state,
        error: null,
        // A new answer supersedes the outstanding question.
        pendingQuestion: null,
        messages: [
          ...state.messages,
          {
            id: nextId(),
            role: 'user',
            content: action.message,
            at: new Date().toISOString(),
            viaVoice: action.viaVoice,
          },
        ],
      };

    case 'stream-start':
      return { ...state, streaming: true, error: null };

    case 'event': {
      const { event } = action;
      const next: ConversationState = {
        ...state,
        threadId: event.thread_id ?? state.threadId,
      };

      if (event.stage) {
        next.stage = event.stage;
        next.visitedStages = state.visitedStages.includes(event.stage)
          ? state.visitedStages
          : [...state.visitedStages, event.stage];
      }

      switch (event.type) {
        case 'stage':
          next.stageLabel = event.text ?? '';
          break;

        case 'brief':
          next.brief = event.payload as unknown as TravelBrief;
          break;

        case 'recommendation':
          next.recommendation = event.payload as unknown as Recommendation;
          break;

        case 'question':
          next.pendingQuestion = event.payload as unknown as PendingQuestion;
          next.messages = [
            ...state.messages,
            {
              id: nextId(),
              role: 'assistant',
              content: event.text ?? next.pendingQuestion.question,
              at: event.at,
            },
          ];
          break;

        case 'validation':
          next.validation = event.payload as unknown as ValidationReport;
          break;

        case 'plan':
          next.plan = event.payload as unknown as TripPlan;
          break;

        case 'tool':
          next.tools = [...state.tools, event.payload as unknown as ToolInvocation];
          break;

        case 'message':
          if (event.text) {
            next.messages = [
              ...state.messages,
              {
                id: nextId(),
                role: 'assistant',
                content: event.text,
                at: event.at,
              },
            ];
          }
          break;

        case 'error':
          next.error = event.text ?? 'Something went wrong.';
          break;

        default:
          break;
      }
      return next;
    }

    case 'stream-end':
      return { ...state, streaming: false, stageLabel: '' };

    case 'error':
      return { ...state, streaming: false, error: action.message, stageLabel: '' };

    case 'reset':
      return { ...INITIAL };

    default:
      return state;
  }
}

export default function Home() {
  const [state, dispatch] = useReducer(reducer, INITIAL);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [speakReplies, setSpeakReplies] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const lastSpokenRef = useRef<string | null>(null);

  useEffect(() => {
    fetchConfig()
      .then(setConfig)
      .catch(() => setConfig(null));
  }, []);

  // Cancel any in-flight stream when the page unmounts, so a navigation does
  // not leave a reader dangling.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Read out the latest assistant message when voice replies are enabled.
  useEffect(() => {
    if (!speakReplies || state.streaming) return;
    const last = [...state.messages].reverse().find((m) => m.role === 'assistant');
    if (last && last.id !== lastSpokenRef.current) {
      lastSpokenRef.current = last.id;
      speak(last.content);
    }
  }, [speakReplies, state.messages, state.streaming]);

  const consume = useCallback(
    async (stream: AsyncGenerator<StreamEvent>) => {
      dispatch({ type: 'stream-start' });
      try {
        for await (const event of stream) {
          dispatch({ type: 'event', event });
        }
      } catch (error) {
        if ((error as Error).name === 'AbortError') return;
        dispatch({
          type: 'error',
          message:
            error instanceof Error
              ? error.message
              : 'Lost connection to the assistant.',
        });
      } finally {
        dispatch({ type: 'stream-end' });
      }
    },
    [],
  );

  const submit = useCallback(
    async (message: string, viaVoice: boolean) => {
      const text = message.trim();
      if (!text || state.streaming) return;

      stopSpeaking();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      dispatch({ type: 'submit', message: text, viaVoice });

      // A pending question is answered through the resume endpoint, which
      // continues the paused graph rather than starting a new run.
      const stream =
        state.pendingQuestion && state.threadId
          ? resumeConversation(state.threadId, text, null, controller.signal)
          : sendMessage(
              text,
              state.threadId,
              viaVoice ? 'voice' : 'text',
              controller.signal,
            );

      await consume(stream);
    },
    [consume, state.pendingQuestion, state.streaming, state.threadId],
  );

  const chooseDestination = useCallback(
    async (index: number, city: string) => {
      if (!state.threadId || state.streaming) return;

      stopSpeaking();
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      dispatch({ type: 'submit', message: city, viaVoice: false });
      await consume(
        resumeConversation(state.threadId, city, index, controller.signal),
      );
    },
    [consume, state.streaming, state.threadId],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    stopSpeaking();
    lastSpokenRef.current = null;
    dispatch({ type: 'reset' });
  }, []);

  const hasConversation = state.messages.length > 0;
  const choicePending = state.pendingQuestion?.type?.startsWith('destination');

  return (
    <div className="flex min-h-screen flex-col">
      <Header
        config={config}
        speakReplies={speakReplies}
        onToggleSpeak={() => {
          setSpeakReplies((on) => {
            if (on) stopSpeaking();
            return !on;
          });
        }}
        showTrace={showTrace}
        onToggleTrace={() => setShowTrace((on) => !on)}
        onReset={reset}
        canReset={hasConversation}
      />

      <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(360px,440px)_1fr]">
        {/* Left: the conversation */}
        <section className="flex min-h-[60vh] flex-col lg:min-h-0 lg:h-[calc(100vh-5.5rem)]">
          <div className="card flex min-h-0 flex-1 flex-col overflow-hidden">
            <ChatPanel
              messages={state.messages}
              streaming={state.streaming}
              stageLabel={state.stageLabel}
              error={state.error}
            />
            <Composer
              onSubmit={submit}
              disabled={state.streaming}
              placeholder={
                state.pendingQuestion
                  ? 'Type your answer, or use the microphone'
                  : 'Describe the trip you want -- or press the microphone'
              }
              examples={state.pendingQuestion?.examples}
            />
          </div>
        </section>

        {/* Right: what the agents produced */}
        <section className="flex min-h-0 flex-col gap-4 lg:h-[calc(100vh-5.5rem)] lg:overflow-y-auto lg:pr-1">
          {state.streaming || state.visitedStages.length > 0 ? (
            <StageTimeline
              current={state.stage}
              visited={state.visitedStages}
              streaming={state.streaming}
              label={state.stageLabel}
            />
          ) : null}

          {choicePending && state.pendingQuestion ? (
            <DestinationChoice
              question={state.pendingQuestion}
              recommendation={state.recommendation}
              onChoose={chooseDestination}
              disabled={state.streaming}
            />
          ) : null}

          {state.plan ? (
            <PlanView plan={state.plan} validation={state.validation} />
          ) : null}

          {!hasConversation ? <WelcomePanel config={config} onPick={submit} /> : null}

          {showTrace ? <ToolTrace tools={state.tools} brief={state.brief} /> : null}
        </section>
      </main>
    </div>
  );
}
