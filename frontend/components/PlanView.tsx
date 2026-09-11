'use client';

import { useState } from 'react';

import {
  clockTime,
  distance,
  duration,
  formatDate,
  formatDateRange,
  localMoney,
  money,
  stars,
  titleCase,
} from '@/lib/format';
import type { FlightOption, TripPlan, ValidationReport } from '@/lib/types';
import ValidationPanel from './ValidationPanel';

interface Props {
  plan: TripPlan;
  validation: ValidationReport | null;
}

/** A price that is modelled rather than quoted, marked as such. */
function Estimate({ value, precise = false }: { value: number | null; precise?: boolean }) {
  return (
    <span
      className="estimate"
      title="A modelled estimate built from open data. Not a bookable price -- confirm with the provider."
    >
      {money(value, precise)}
    </span>
  );
}

function FlightCard({ flight }: { flight: FlightOption }) {
  const first = flight.legs[0];
  const last = flight.legs[flight.legs.length - 1];
  if (!first || !last) return null;

  return (
    <div className="sunken rounded-lg p-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[12px] font-semibold uppercase tracking-wide muted">
          {flight.direction === 'outbound' ? 'Outbound' : 'Return'}
        </p>
        <p className="text-[13px] font-semibold">
          <Estimate value={flight.price_total_eur} />
        </p>
      </div>

      <div className="mt-2 flex items-center gap-2.5">
        <div className="text-center">
          <p className="text-[15px] font-semibold leading-none">{first.from_iata}</p>
          <p className="mt-0.5 text-[11px] muted">{clockTime(first.depart_local)}</p>
        </div>

        <div className="flex-1">
          <div className="relative flex items-center">
            <span className="h-px flex-1" style={{ backgroundColor: 'rgb(var(--border))' }} />
            <span className="px-1.5 text-[10px] muted whitespace-nowrap">
              {duration(flight.total_duration_minutes)}
            </span>
            <span className="h-px flex-1" style={{ backgroundColor: 'rgb(var(--border))' }} />
          </div>
          <p className="mt-0.5 text-center text-[10.5px] muted">
            {flight.stops === 0
              ? 'Non-stop'
              : flight.layovers
                  .map((l) => `via ${l.city ?? l.airport} (${l.minutes}m)`)
                  .join(', ')}
          </p>
        </div>

        <div className="text-center">
          <p className="text-[15px] font-semibold leading-none">{last.to_iata}</p>
          <p className="mt-0.5 text-[11px] muted">{clockTime(last.arrive_local)}</p>
        </div>
      </div>

      <p className="mt-2 text-[11px] muted">
        {flight.legs
          .map((leg) => `${leg.carrier_name ?? leg.carrier_code} ${leg.flight_number ?? ''}`.trim())
          .join(' · ')}
        {' · '}
        {titleCase(flight.cabin)}
      </p>
    </div>
  );
}

export default function PlanView({ plan, validation }: Props) {
  const [openDay, setOpenDay] = useState<number | null>(1);
  const report = validation ?? plan.validation;

  return (
    <div className="space-y-4 animate-slide-up">
      {/* ---------------- Header ---------------- */}
      <div className="card overflow-hidden">
        <div
          className="px-4 py-3.5"
          style={{ backgroundColor: 'rgb(var(--accent) / 0.08)' }}
        >
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="text-[19px] font-semibold leading-tight">
                {plan.destination}
                {plan.country ? (
                  <span className="muted font-normal">, {plan.country}</span>
                ) : null}
              </h2>
              <p className="mt-0.5 text-[12.5px] muted">
                {formatDateRange(plan.start_date, plan.end_date)} · {plan.nights} nights ·{' '}
                {plan.travellers} {plan.travellers === 1 ? 'traveller' : 'travellers'}
                {plan.revision > 0
                  ? ` · revised ${plan.revision}×`
                  : ''}
              </p>
            </div>
            <div className="text-right">
              <p className="text-[19px] font-semibold leading-tight">
                <Estimate value={plan.costs.total_eur} />
              </p>
              <p className="text-[11.5px] muted">
                {money(plan.costs.per_person_eur)} per person
              </p>
            </div>
          </div>
        </div>

        {plan.narrative ? (
          <p className="border-t px-4 py-3 text-[13.5px] leading-relaxed"
             style={{ borderColor: 'rgb(var(--border))' }}>
            {plan.narrative}
          </p>
        ) : null}
      </div>

      {report ? <ValidationPanel report={report} /> : null}

      {/* ---------------- Flights ---------------- */}
      {plan.outbound_flight || plan.inbound_flight ? (
        <div className="card p-4">
          <h3 className="mb-2.5 text-[12px] font-semibold uppercase tracking-wide muted">
            Flights
          </h3>
          <div className="grid gap-2.5 sm:grid-cols-2">
            {plan.outbound_flight ? <FlightCard flight={plan.outbound_flight} /> : null}
            {plan.inbound_flight ? <FlightCard flight={plan.inbound_flight} /> : null}
          </div>
        </div>
      ) : null}

      {/* ---------------- Hotel ---------------- */}
      {plan.hotel ? (
        <div className="card p-4">
          <h3 className="mb-2.5 text-[12px] font-semibold uppercase tracking-wide muted">
            Accommodation
          </h3>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-[14.5px] font-semibold">
                {plan.hotel.name}{' '}
                <span style={{ color: '#b45309' }} title={
                  plan.hotel.stars_source === 'osm_tag'
                    ? 'Star rating from OpenStreetMap.'
                    : 'Star rating inferred from the property type and amenities -- not officially rated.'
                }>
                  {stars(plan.hotel.stars)}
                </span>
              </p>
              <p className="mt-0.5 text-[12px] muted">
                {titleCase(plan.hotel.lodging_type)}
                {plan.hotel.distance_from_centre_km !== null
                  ? ` · ${distance(plan.hotel.distance_from_centre_km)} from the centre`
                  : ''}
                {plan.hotel.address ? ` · ${plan.hotel.address}` : ''}
              </p>

              {plan.hotel.amenities.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {plan.hotel.amenities.map((amenity) => (
                    <span key={amenity} className="chip">
                      {titleCase(amenity)}
                    </span>
                  ))}
                </div>
              ) : null}

              {plan.hotel.walkability_score !== null ? (
                <p className="mt-2 text-[11.5px] muted">
                  Walkability {plan.hotel.walkability_score}/100
                  {plan.hotel.nearest_transit_m !== null
                    ? ` · nearest transit ${plan.hotel.nearest_transit_m} m`
                    : ''}
                </p>
              ) : null}
            </div>

            <div className="text-right">
              <p className="text-[15px] font-semibold">
                <Estimate value={plan.hotel.estimated_nightly_eur} />
              </p>
              <p className="text-[11.5px] muted">per night</p>
              {plan.hotel.website ? (
                <a
                  href={plan.hotel.website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1 inline-block text-[11.5px] underline"
                  style={{ color: 'rgb(var(--accent))' }}
                >
                  Website
                </a>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}

      {/* ---------------- Itinerary ---------------- */}
      {plan.days.length > 0 ? (
        <div className="card p-4">
          <h3 className="mb-2.5 text-[12px] font-semibold uppercase tracking-wide muted">
            Day by day
          </h3>
          <div className="space-y-1.5">
            {plan.days.map((day) => {
              const open = openDay === day.day_number;
              return (
                <div
                  key={day.day_number}
                  className="overflow-hidden rounded-lg border"
                  style={{ borderColor: 'rgb(var(--border))' }}
                >
                  <button
                    type="button"
                    onClick={() => setOpenDay(open ? null : day.day_number)}
                    aria-expanded={open}
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-[rgb(var(--surface-sunken))]"
                  >
                    <span
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[11px] font-semibold"
                      style={{
                        backgroundColor: 'rgb(var(--accent) / 0.12)',
                        color: 'rgb(var(--accent))',
                      }}
                    >
                      {day.day_number}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13.5px] font-medium">
                        {day.title || 'Free day'}
                      </span>
                      <span className="block text-[11px] muted">
                        {formatDate(day.day_date)}
                        {day.activities.length > 0
                          ? ` · ${day.activities.length} ${
                              day.activities.length === 1 ? 'stop' : 'stops'
                            }`
                          : ''}
                        {day.total_walking_km > 0
                          ? ` · ${distance(day.total_walking_km)} walking`
                          : ''}
                      </span>
                    </span>
                    <svg
                      width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
                      strokeLinejoin="round" aria-hidden
                      className={`shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
                      style={{ color: 'rgb(var(--text-muted))' }}
                    >
                      <path d="M6 9l6 6 6-6" />
                    </svg>
                  </button>

                  {open ? (
                    <div className="border-t px-3 py-2.5" style={{ borderColor: 'rgb(var(--border))' }}>
                      {day.notes.length > 0 ? (
                        <ul className="mb-2 space-y-0.5">
                          {day.notes.map((note) => (
                            <li key={note} className="text-[11.5px] muted">
                              {note}
                            </li>
                          ))}
                        </ul>
                      ) : null}

                      {day.activities.length === 0 ? (
                        <p className="text-[12.5px] muted">
                          Nothing scheduled -- a deliberate gap to explore on your own.
                        </p>
                      ) : (
                        <ol className="space-y-2">
                          {day.activities.map((activity, index) => (
                            <li key={`${activity.osm_id ?? activity.name}-${index}`} className="flex gap-2.5">
                              <span className="w-11 shrink-0 pt-0.5 text-[11.5px] tabular-nums muted">
                                {activity.start_time ?? ''}
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="block text-[13px] font-medium">
                                  {activity.name}
                                </span>
                                <span className="block text-[11px] muted">
                                  {[
                                    activity.kind ? titleCase(activity.kind) : null,
                                    activity.duration_minutes
                                      ? duration(activity.duration_minutes)
                                      : null,
                                    activity.walk_from_previous_km
                                      ? `${distance(activity.walk_from_previous_km)} walk`
                                      : null,
                                    activity.opening_hours,
                                  ]
                                    .filter(Boolean)
                                    .join(' · ')}
                                </span>
                                {activity.notes ? (
                                  <span className="mt-0.5 block text-[11px]" style={{ color: 'rgb(var(--accent))' }}>
                                    {activity.notes}
                                  </span>
                                ) : null}
                              </span>
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* ---------------- Costs ---------------- */}
      <div className="card p-4">
        <h3 className="mb-2.5 text-[12px] font-semibold uppercase tracking-wide muted">
          Estimated cost
        </h3>
        <dl className="space-y-1.5 text-[13px]">
          {[
            ['Flights', plan.costs.flights_eur],
            ['Accommodation', plan.costs.lodging_eur],
            ['Food & local transport', plan.costs.living_eur],
            ['Activities', plan.costs.activities_eur],
          ].map(([label, value]) => (
            <div key={label as string} className="flex justify-between">
              <dt className="muted">{label as string}</dt>
              <dd>{money(value as number)}</dd>
            </div>
          ))}
          <div
            className="flex justify-between border-t pt-1.5 font-semibold"
            style={{ borderColor: 'rgb(var(--border))' }}
          >
            <dt>Total</dt>
            <dd><Estimate value={plan.costs.total_eur} /></dd>
          </div>
          {plan.costs.budget_eur !== null ? (
            <div className="flex justify-between">
              <dt className="muted">Against your budget of {money(plan.costs.budget_eur)}</dt>
              <dd
                style={{
                  color:
                    (plan.costs.over_under_eur ?? 0) <= 0 ? '#15803d' : '#b45309',
                }}
              >
                {(plan.costs.over_under_eur ?? 0) <= 0 ? 'Under by ' : 'Over by '}
                {money(Math.abs(plan.costs.over_under_eur ?? 0))}
              </dd>
            </div>
          ) : null}
          {plan.costs.total_local !== null && plan.costs.local_currency ? (
            <p className="pt-1 text-[11.5px] muted">
              About {localMoney(plan.costs.total_local, plan.costs.local_currency)} at
              today&apos;s ECB rate.
            </p>
          ) : null}
        </dl>
      </div>

      {/* ---------------- Briefing ---------------- */}
      <div className="grid gap-4 sm:grid-cols-2">
        {plan.highlights.length > 0 || plan.packing_notes.length > 0 ? (
          <div className="card p-4">
            {plan.highlights.length > 0 ? (
              <>
                <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide muted">
                  Highlights
                </h3>
                <ul className="mb-3 space-y-1">
                  {plan.highlights.map((item) => (
                    <li key={item} className="flex gap-1.5 text-[12.5px]">
                      <span aria-hidden style={{ color: 'rgb(var(--accent))' }}>·</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}

            {plan.packing_notes.length > 0 ? (
              <>
                <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide muted">
                  What to pack
                </h3>
                <ul className="space-y-1">
                  {plan.packing_notes.map((item) => (
                    <li key={item} className="flex gap-1.5 text-[12.5px]">
                      <span aria-hidden style={{ color: 'rgb(var(--accent))' }}>·</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </div>
        ) : null}

        <div className="card p-4">
          <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide muted">
            Know before you go
          </h3>
          <dl className="space-y-1 text-[12.5px]">
            {plan.briefing.currency_code ? (
              <div className="flex justify-between gap-2">
                <dt className="muted">Currency</dt>
                <dd className="text-right">
                  {plan.briefing.currency_name} ({plan.briefing.currency_code})
                </dd>
              </div>
            ) : null}
            {plan.briefing.languages.length > 0 ? (
              <div className="flex justify-between gap-2">
                <dt className="muted">Language</dt>
                <dd className="text-right">{plan.briefing.languages.join(', ')}</dd>
              </div>
            ) : null}
            {plan.briefing.timezone ? (
              <div className="flex justify-between gap-2">
                <dt className="muted">Timezone</dt>
                <dd className="text-right">{plan.briefing.timezone}</dd>
              </div>
            ) : null}
            {plan.briefing.drives_on ? (
              <div className="flex justify-between gap-2">
                <dt className="muted">Drives on</dt>
                <dd className="text-right">{titleCase(plan.briefing.drives_on)}</dd>
              </div>
            ) : null}
            {plan.briefing.calling_code ? (
              <div className="flex justify-between gap-2">
                <dt className="muted">Dialling code</dt>
                <dd className="text-right">{plan.briefing.calling_code}</dd>
              </div>
            ) : null}
          </dl>
          <p className="mt-2.5 border-t pt-2 text-[11px] muted"
             style={{ borderColor: 'rgb(var(--border))' }}>
            {plan.briefing.visa_note}
          </p>
        </div>
      </div>

      {plan.warnings.length > 0 ? (
        <div className="card p-4">
          <h3 className="mb-2 text-[12px] font-semibold uppercase tracking-wide muted">
            Assumptions I made
          </h3>
          <ul className="space-y-1">
            {plan.warnings.map((warning) => (
              <li key={warning} className="text-[12.5px] muted">
                {warning}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="px-1 pb-2 text-[11px] leading-relaxed muted">{plan.disclaimer}</p>
    </div>
  );
}
