'use client';

import { money, scoreBand } from '@/lib/format';
import type { PendingQuestion, Recommendation } from '@/lib/types';

interface Props {
  question: PendingQuestion;
  recommendation: Recommendation | null;
  onChoose: (index: number, city: string) => void;
  disabled: boolean;
}

const TONE_COLOUR: Record<string, string> = {
  excellent: '#15803d',
  good: '#15803d',
  mixed: '#b45309',
  poor: '#b91c1c',
  unknown: 'rgb(var(--text-muted))',
};

/**
 * The destination shortlist, presented as selectable cards.
 *
 * This is the point where the system hands the decision back to the user, so
 * each card shows the three things that actually drive the choice -- fit,
 * weather, and cost -- alongside the agents' reason for ranking it there. The
 * cards are real buttons so the whole flow stays keyboard-operable.
 */
export default function DestinationChoice({
  question,
  recommendation,
  onChoose,
  disabled,
}: Props) {
  const options = question.options ?? [];
  if (options.length === 0) return null;

  const candidates = recommendation?.candidates ?? [];

  return (
    <div className="card p-4 animate-slide-up">
      <div className="mb-3">
        <h2 className="text-[15px] font-semibold">
          {question.headline || recommendation?.headline || 'Shortlisted destinations'}
        </h2>
        {(question.rationale || recommendation?.rationale) ? (
          <p className="mt-1.5 text-[13px] leading-relaxed muted">
            {question.rationale || recommendation?.rationale}
          </p>
        ) : null}
      </div>

      <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
        {options.map((option) => {
          const candidate = candidates.find((c) => c.city === option.city);
          const band = scoreBand(option.weather_score);
          const isTop = option.index === 0;

          return (
            <button
              key={option.index}
              type="button"
              disabled={disabled}
              onClick={() => onChoose(option.index, option.city)}
              className="group card flex flex-col gap-2 p-3 text-left transition-all
                         hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-60"
              style={{
                borderColor: isTop ? 'rgb(var(--accent))' : 'rgb(var(--border))',
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-[14px] font-semibold">{option.city}</p>
                  <p className="truncate text-[11.5px] muted">{option.country}</p>
                </div>
                {isTop ? (
                  <span
                    className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold text-white"
                    style={{ backgroundColor: 'rgb(var(--accent))' }}
                  >
                    Top pick
                  </span>
                ) : null}
              </div>

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px]">
                <span
                  title={
                    candidate?.weather.basis === 'climate_normals'
                      ? 'Beyond the 16-day forecast horizon, so this is a multi-year average for the month.'
                      : 'From the live Open-Meteo forecast.'
                  }
                >
                  <span className="muted">Weather </span>
                  <span style={{ color: TONE_COLOUR[band.tone] }}>
                    {band.label}
                    {option.weather_score !== null
                      ? ` (${Math.round(option.weather_score)})`
                      : ''}
                  </span>
                  {candidate?.weather.basis === 'climate_normals' ? (
                    <span className="muted"> · historical</span>
                  ) : null}
                </span>

                {option.estimated_total_eur !== null ? (
                  <span>
                    <span className="muted">Est. </span>
                    <span className="estimate" title="A modelled estimate from open data, not a bookable price.">
                      {money(option.estimated_total_eur)}
                    </span>
                  </span>
                ) : null}
              </div>

              {candidate?.weather.avg_high_c !== null &&
              candidate?.weather.avg_high_c !== undefined ? (
                <p className="text-[11.5px] muted">
                  Highs {Math.round(candidate.weather.avg_high_c)}°C
                  {candidate.weather.rainy_days
                    ? `, ${Math.round(candidate.weather.rainy_days)} rainy ${
                        candidate.weather.rainy_days === 1 ? 'day' : 'days'
                      }`
                    : ', little rain'}
                </p>
              ) : null}

              {option.why.length > 0 ? (
                <p className="line-clamp-3 text-[12px] leading-snug">
                  {option.why[0]}
                </p>
              ) : null}

              <span
                className="mt-auto pt-1 text-[11.5px] font-medium opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
                style={{ color: 'rgb(var(--accent))' }}
                aria-hidden
              >
                Plan this trip →
              </span>
            </button>
          );
        })}
      </div>

      <p className="mt-3 text-[12px] muted">
        {question.question} You can also just type a city name.
      </p>
    </div>
  );
}
