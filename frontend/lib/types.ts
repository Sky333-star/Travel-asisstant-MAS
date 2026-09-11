/**
 * Wire types mirroring the backend's Pydantic models.
 *
 * These are hand-maintained rather than generated, because the surface is
 * small and the duplication is worth the zero-dependency build. If the backend
 * schema changes, `/openapi.json` is the source of truth to check against.
 */

export type Stage =
  | 'intake'
  | 'analysing'
  | 'clarifying'
  | 'scouting'
  | 'weather'
  | 'recommending'
  | 'awaiting_choice'
  | 'planning'
  | 'validating'
  | 'revising'
  | 'presenting'
  | 'done'
  | 'error';

export type EventType =
  | 'stage'
  | 'message'
  | 'brief'
  | 'recommendation'
  | 'question'
  | 'plan'
  | 'validation'
  | 'tool'
  | 'trace'
  | 'error'
  | 'done';

export interface StreamEvent {
  type: EventType;
  thread_id: string;
  at: string;
  stage?: Stage;
  text?: string;
  payload?: Record<string, unknown>;
}

// ---------------------------------------------------------------- brief

export interface DateWindow {
  start: string | null;
  end: string | null;
  flexible: boolean;
  duration_nights: number | null;
}

export interface Budget {
  amount: number | null;
  currency: string;
  amount_eur: number | null;
  per_person: boolean;
  is_hard_limit: boolean;
}

export interface PartyComposition {
  adults: number;
  children: number;
  infants: number;
}

export interface Constraints {
  accessibility_required: boolean;
  dietary: string[];
  avoid_countries: string[];
  avoid_cities: string[];
  max_flight_hours: number | null;
  max_stops: number | null;
  must_include: string[];
  health_notes: string[];
  visa_free_only: boolean;
}

export interface TravelBrief {
  raw_query: string;
  origin: string | null;
  origin_lat: number | null;
  origin_lon: number | null;
  named_destinations: string[];
  regions: string[];
  dates: DateWindow;
  party: PartyComposition;
  budget: Budget;
  interests: string[];
  climate_preference: string;
  pace: string;
  style: string;
  constraints: Constraints;
  trip_purpose: string | null;
  missing_critical: string[];
  confidence: number;
  extraction_method: 'llm' | 'rules' | 'merged';
  notes: string[];
}

// ------------------------------------------------------- recommendation

export interface WeatherAssessment {
  score: number | null;
  basis: 'forecast' | 'climate_normals' | 'unavailable';
  avg_high_c: number | null;
  avg_low_c: number | null;
  rainy_days: number | null;
  precipitation_mm: number | null;
  reasons: string[];
  air_quality_band: string | null;
}

export interface DestinationCandidate {
  city: string;
  country: string | null;
  iso2: string | null;
  region: string | null;
  latitude: number;
  longitude: number;
  blurb: string | null;
  tags: string[];
  budget_tier: number | null;
  best_months: number[];
  interest_score: number | null;
  season_score: number | null;
  weather: WeatherAssessment;
  affordability_score: number | null;
  composite_score: number | null;
  matched_interests: string[];
  estimated_flight_eur: number | null;
  estimated_nightly_eur: number | null;
  estimated_trip_total_eur: number | null;
  why: string[];
  source: string;
}

export interface Recommendation {
  candidates: DestinationCandidate[];
  headline: string;
  rationale: string;
  follow_up_question: string;
  generated_by: 'llm' | 'rules';
}

// ------------------------------------------------------------------ plan

export interface FlightLeg {
  from_iata: string;
  from_name: string | null;
  from_city: string | null;
  to_iata: string;
  to_name: string | null;
  to_city: string | null;
  carrier_code: string | null;
  carrier_name: string | null;
  flight_number: string | null;
  depart_local: string | null;
  arrive_local: string | null;
  duration_minutes: number | null;
  distance_km: number | null;
}

export interface Layover {
  airport: string;
  city: string | null;
  minutes: number;
}

export interface FlightOption {
  id: string;
  direction: 'outbound' | 'inbound';
  stops: number;
  legs: FlightLeg[];
  layovers: Layover[];
  total_duration_minutes: number | null;
  price_per_person_eur: number | null;
  price_total_eur: number | null;
  cabin: string;
  pricing: 'estimated' | 'live';
  data_basis: string;
}

export interface HotelOption {
  id: string;
  name: string;
  lodging_type: string;
  stars: number | null;
  stars_source: 'osm_tag' | 'inferred';
  latitude: number | null;
  longitude: number | null;
  distance_from_centre_km: number | null;
  address: string | null;
  website: string | null;
  phone: string | null;
  amenities: string[];
  wheelchair_accessible: boolean;
  estimated_nightly_eur: number | null;
  estimated_total_eur: number | null;
  rooms: number;
  walkability_score: number | null;
  nearest_transit_m: number | null;
  pricing: 'estimated' | 'live';
  data_basis: string;
}

export interface Activity {
  name: string;
  kind: string | null;
  latitude: number | null;
  longitude: number | null;
  start_time: string | null;
  duration_minutes: number | null;
  notes: string | null;
  opening_hours: string | null;
  website: string | null;
  wheelchair: string | null;
  walk_from_previous_km: number | null;
  osm_id: string | null;
}

export interface DayPlan {
  day_number: number;
  day_date: string;
  title: string;
  activities: Activity[];
  total_walking_km: number;
  notes: string[];
}

export interface CostBreakdown {
  flights_eur: number;
  lodging_eur: number;
  living_eur: number;
  activities_eur: number;
  total_eur: number;
  per_person_eur: number;
  budget_eur: number | null;
  over_under_eur: number | null;
  currency_note: string | null;
  local_currency: string | null;
  total_local: number | null;
}

export interface CountryBriefing {
  country: string | null;
  currency_code: string | null;
  currency_name: string | null;
  languages: string[];
  timezone: string | null;
  drives_on: string | null;
  calling_code: string | null;
  visa_note: string;
}

export type Severity = 'blocker' | 'warning' | 'info';

export interface ValidationIssue {
  code: string;
  severity: Severity;
  message: string;
  detail: string | null;
  fix_hint: string;
  magnitude: number | null;
}

export interface ValidationReport {
  passed: boolean;
  score: number;
  issues: ValidationIssue[];
  checks_run: string[];
  revision: number;
  summary: string;
}

export interface TripPlan {
  destination: string;
  country: string | null;
  latitude: number | null;
  longitude: number | null;
  start_date: string | null;
  end_date: string | null;
  nights: number;
  travellers: number;
  revision: number;
  outbound_flight: FlightOption | null;
  inbound_flight: FlightOption | null;
  flight_alternatives: FlightOption[];
  hotel: HotelOption | null;
  hotel_alternatives: HotelOption[];
  days: DayPlan[];
  costs: CostBreakdown;
  weather: Record<string, unknown>;
  briefing: CountryBriefing;
  highlights: string[];
  packing_notes: string[];
  warnings: string[];
  validation: ValidationReport | null;
  narrative: string;
  disclaimer: string;
}

// --------------------------------------------------------------- runtime

export interface PendingQuestion {
  type: string;
  question: string;
  fields?: string[];
  examples?: string[];
  headline?: string;
  rationale?: string;
  options?: {
    index: number;
    city: string;
    country: string | null;
    score: number | null;
    weather_score: number | null;
    estimated_total_eur: number | null;
    why: string[];
  }[];
}

export interface ToolInvocation {
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  duration_ms: number | null;
  ok: boolean;
  error: string | null;
  result_summary: string | null;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  at: string;
  viaVoice?: boolean;
}

export interface AppConfig {
  llm_available: boolean;
  llm_model: string | null;
  default_currency: string;
  max_plan_revisions: number;
  enabled_mcp_servers: string[];
  pricing_disclaimer: string;
}
