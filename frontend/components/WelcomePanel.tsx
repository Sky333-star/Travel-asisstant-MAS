'use client';

import type { AppConfig } from '@/lib/types';

interface Props {
  config: AppConfig | null;
  onPick: (message: string, viaVoice: boolean) => void;
}

/**
 * The empty state.
 *
 * The example prompts are chosen to exercise different parts of the pipeline:
 * one with a hard constraint, one beyond the forecast horizon, one with an
 * accessibility requirement, one deliberately underspecified so the clarifier
 * fires. They double as a demo script.
 */
const EXAMPLES = [
  {
    title: 'Warm and walkable',
    prompt:
      "I'm flying from London with my partner for 6 nights in early October. "
      + 'We want somewhere warm in Europe with great food and a walkable old town. '
      + 'Budget is about 2000 euros for the two of us.',
    note: 'Exercises weather scoring and budget validation',
  },
  {
    title: 'Accessible city break',
    prompt:
      'From Berlin, 4 nights in May. I use a wheelchair so I need step-free '
      + 'accommodation and short walking days. Museums and architecture please.',
    note: 'Exercises accessibility constraints in the validator',
  },
  {
    title: 'Far-out planning',
    prompt:
      'Flying from Madrid next August for 10 nights. Somewhere in southeast Asia '
      + 'with beaches and diving, around 1800 euros each.',
    note: 'Beyond the forecast horizon -- falls back to climate averages',
  },
  {
    title: 'Deliberately vague',
    prompt: 'I want to go somewhere warm and cheap.',
    note: 'Triggers the clarifier: no origin, no dates',
  },
];

export default function WelcomePanel({ config, onPick }: Props) {
  return (
    <div className="space-y-4 animate-fade-in">
      <div className="card p-5">
        <h2 className="text-[16px] font-semibold">How this works</h2>
        <p className="mt-1.5 text-[13px] leading-relaxed muted">
          Nine specialist agents share the work. One reads your request, another
          shortlists destinations, a third scores the real weather for your
          dates, and a fourth builds flights, lodging and a day-by-day plan. A
          critic then checks that plan against your budget, constraints and the
          forecast, and sends it back to be rebuilt if it does not hold up.
        </p>

        <ol className="mt-3.5 grid gap-2 sm:grid-cols-2">
          {[
            ['Understand', 'Your request becomes a structured brief -- origin, dates, budget, constraints.'],
            ['Shortlist & score', 'Candidate cities are ranked on interest fit, real weather and cost.'],
            ['You choose', 'The system recommends; the decision stays yours.'],
            ['Plan & validate', 'Flights, a real hotel and an itinerary -- then checked, and revised if needed.'],
          ].map(([title, body], index) => (
            <li key={title} className="sunken flex gap-2.5 rounded-lg p-3">
              <span
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
                style={{
                  backgroundColor: 'rgb(var(--accent) / 0.12)',
                  color: 'rgb(var(--accent))',
                }}
              >
                {index + 1}
              </span>
              <span>
                <span className="block text-[12.5px] font-medium">{title}</span>
                <span className="block text-[11.5px] muted">{body}</span>
              </span>
            </li>
          ))}
        </ol>
      </div>

      <div className="card p-5">
        <h2 className="text-[16px] font-semibold">Try one of these</h2>
        <p className="mt-1 text-[12.5px] muted">
          Each example puts a different part of the system under load.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {EXAMPLES.map((example) => (
            <button
              key={example.title}
              type="button"
              onClick={() => onPick(example.prompt, false)}
              className="sunken rounded-lg border p-3 text-left transition-colors hover:border-[rgb(var(--accent))]"
              style={{ borderColor: 'rgb(var(--border))' }}
            >
              <p className="text-[13px] font-medium">{example.title}</p>
              <p className="mt-1 line-clamp-3 text-[11.5px] muted">{example.prompt}</p>
              <p className="mt-1.5 text-[10.5px]" style={{ color: 'rgb(var(--accent))' }}>
                {example.note}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div className="card p-5">
        <h2 className="text-[16px] font-semibold">Where the data comes from</h2>
        <dl className="mt-2.5 space-y-1.5 text-[12.5px]">
          {[
            ['Weather', 'Open-Meteo forecasts and ERA5 climate archive -- real, keyless'],
            ['Places & hotels', 'OpenStreetMap via Nominatim and Overpass -- real properties'],
            ['Airports', 'OurAirports open dataset -- real airports and codes'],
            ['Exchange rates', 'Frankfurter / European Central Bank -- real rates'],
            ['Prices', 'Modelled estimates -- no keyless API publishes live fares'],
          ].map(([label, value]) => (
            <div key={label} className="flex flex-wrap justify-between gap-x-3">
              <dt className="muted">{label}</dt>
              <dd className="text-right">{value}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 border-t pt-2.5 text-[11.5px] leading-relaxed muted"
           style={{ borderColor: 'rgb(var(--border))' }}>
          {config?.pricing_disclaimer
            ?? 'Flight and lodging prices are modelled estimates built from open data, not live bookable fares.'}
          {' '}
          Airports and properties are real; the prices attached to them are
          calculated, and marked with a dotted underline wherever they appear.
        </p>
      </div>
    </div>
  );
}
