/**
 * Display formatting helpers.
 *
 * Kept in one place so that a price looks the same in a card, a table and a
 * tooltip -- and, more importantly, so that the "estimated" framing around
 * modelled prices is applied consistently rather than remembered case by case.
 */

const EUR = new Intl.NumberFormat('en-GB', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 0,
});

const EUR_PRECISE = new Intl.NumberFormat('en-GB', {
  style: 'currency',
  currency: 'EUR',
  maximumFractionDigits: 2,
});

export function money(amount: number | null | undefined, precise = false): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) return '--';
  return precise ? EUR_PRECISE.format(amount) : EUR.format(amount);
}

export function localMoney(amount: number | null, currency: string | null): string {
  if (amount === null || !currency) return '';
  try {
    return new Intl.NumberFormat('en-GB', {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(amount);
  } catch {
    return `${Math.round(amount).toLocaleString('en-GB')} ${currency}`;
  }
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '--';
  const date = new Date(`${iso.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString('en-GB', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

export function formatDateRange(start: string | null, end: string | null): string {
  if (!start || !end) return '--';
  const from = new Date(`${start.slice(0, 10)}T00:00:00`);
  const to = new Date(`${end.slice(0, 10)}T00:00:00`);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
    return `${start} to ${end}`;
  }
  const sameMonth =
    from.getMonth() === to.getMonth() && from.getFullYear() === to.getFullYear();
  const left = from.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: sameMonth ? undefined : 'short',
  });
  const right = to.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
  return `${left} - ${right}`;
}

/** "2h 35m" from a minute count. */
export function duration(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return '--';
  const hours = Math.floor(minutes / 60);
  const rest = Math.round(minutes % 60);
  if (hours === 0) return `${rest}m`;
  return rest === 0 ? `${hours}h` : `${hours}h ${rest}m`;
}

/** "07:00" from an ISO local datetime string. */
export function clockTime(isoLocal: string | null | undefined): string {
  if (!isoLocal) return '--';
  const parts = isoLocal.split('T');
  return parts[1]?.slice(0, 5) ?? isoLocal;
}

export function temperature(celsius: number | null | undefined): string {
  if (celsius === null || celsius === undefined) return '--';
  return `${Math.round(celsius)}°C`;
}

export function distance(km: number | null | undefined): string {
  if (km === null || km === undefined) return '--';
  return km < 1 ? `${Math.round(km * 1000)} m` : `${km.toFixed(1)} km`;
}

export function stars(count: number | null | undefined): string {
  if (!count) return '';
  return '★'.repeat(Math.min(5, Math.max(1, count)));
}

/** Band a 0-100 score so colour and wording stay consistent everywhere. */
export function scoreBand(score: number | null | undefined): {
  label: string;
  tone: 'excellent' | 'good' | 'mixed' | 'poor' | 'unknown';
} {
  if (score === null || score === undefined) return { label: 'No data', tone: 'unknown' };
  if (score >= 80) return { label: 'Excellent', tone: 'excellent' };
  if (score >= 62) return { label: 'Good', tone: 'good' };
  if (score >= 42) return { label: 'Mixed', tone: 'mixed' };
  return { label: 'Poor', tone: 'poor' };
}

/**
 * How a weather score was derived, phrased for a traveller.
 *
 * This matters more than it looks: presenting a multi-year climate average as
 * "the forecast" is the most common way travel tools mislead people, so the
 * distinction is surfaced wherever a weather number appears.
 */
export function weatherBasisLabel(basis: string): string {
  switch (basis) {
    case 'forecast':
      return 'Live forecast';
    case 'climate_normals':
      return 'Historical averages';
    default:
      return 'No weather data';
  }
}

export function weatherBasisTooltip(basis: string): string {
  switch (basis) {
    case 'forecast':
      return 'From the Open-Meteo forecast, which runs 16 days ahead.';
    case 'climate_normals':
      return 'Your trip is beyond the 16-day forecast horizon, so this is a multi-year average for that month -- not a forecast.';
    default:
      return 'Weather data could not be retrieved for this destination.';
  }
}

export function titleCase(value: string): string {
  return value
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

export function pluralise(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export const STAGE_LABELS: Record<string, string> = {
  intake: 'Reading your message',
  analysing: 'Understanding the request',
  clarifying: 'Asking a question',
  scouting: 'Shortlisting destinations',
  weather: 'Checking the weather',
  recommending: 'Ranking the options',
  awaiting_choice: 'Waiting for your choice',
  planning: 'Building the plan',
  validating: 'Checking the plan',
  revising: 'Revising the plan',
  presenting: 'Writing it up',
  done: 'Done',
  error: 'Something went wrong',
};

/** The stages shown in the progress timeline, in order. */
export const TIMELINE_STAGES = [
  'analysing',
  'scouting',
  'weather',
  'recommending',
  'planning',
  'validating',
  'presenting',
] as const;
