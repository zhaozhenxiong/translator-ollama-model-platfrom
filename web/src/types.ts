export type TranslationStatus = 'pending' | 'translating' | 'done' | 'failed' | 'skipped';
export type SessionStatus = 'active' | 'completed' | 'error' | string;
export type TranslationProvider = 'ollama' | 'cc_switch';

export interface Session {
  id: string;
  title: string;
  source_language: string;
  target_language: string;
  asr_backend: string;
  asr_model: string;
  ollama_model: string;
  translation_provider: TranslationProvider;
  translation_model: string;
  audio_source_type: 'microphone' | 'application';
  audio_source_bundle_id: string | null;
  audio_source_process_id: number | null;
  audio_source_display_name: string | null;
  created_at: string;
  updated_at: string;
  ended_at: string | null;
  status: SessionStatus;
}

export interface Segment {
  id: string;
  session_id: string;
  sequence: number;
  start_ms: number;
  end_ms: number;
  source_text: string;
  translated_text: string | null;
  detected_language: string | null;
  translation_status: TranslationStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail extends Session {
  segments: Segment[];
}

export interface TranslationProviderConfig {
  id: TranslationProvider;
  label: string;
  enabled: boolean;
  available: boolean;
  default_model: string;
  models: string[];
  error: string | null;
  privacy: 'local' | 'cloud';
}

export interface AppConfig {
  asr_backend: string;
  asr_model: string;
  ollama_model: string;
  default_source_language: string;
  default_target_language: string;
  application_audio_available: boolean;
  translation_providers: TranslationProviderConfig[];
}

export interface ServiceHealth {
  status: string;
  error?: string | null;
}

export type TranslationHealth = ServiceHealth & { model: string; router_available?: boolean };

export interface Health {
  status: string;
  asr: ServiceHealth;
  ollama: TranslationHealth;
  cc_switch?: TranslationHealth;
}

export interface AudioInput {
  deviceId: string;
  label: string;
}

export type AudioSourceType = 'microphone' | 'application';

export interface AudioSourceSelection {
  type: AudioSourceType;
  bundle_id?: string;
  process_id?: number;
  display_name?: string;
}

export interface ApplicationAudioInfo {
  name: string;
  bundle_id: string;
  process_id: number;
}

export interface AudioPermission {
  screen_recording_granted: boolean;
  requested: boolean;
}

export type RecorderState = 'idle' | 'starting' | 'live' | 'paused' | 'stopping' | 'completed' | 'error';
export type ViewMode = 'chat' | 'document';
export type DocumentMode = 'source' | 'translation' | 'bilingual';

export type ServerEvent =
  | { type: 'status'; status: string; message?: string; translation_queue?: number; asr_lag?: number }
  | { type: 'partial'; text: string; asr_lag?: number }
  | { type: 'segment'; segment: Segment }
  | { type: 'translation_update'; segment_id: string; text: string; status: 'translating' }
  | { type: 'translation_final'; segment: Segment }
  | { type: 'translation_error'; segment_id: string; message: string }
  | { type: 'capture_starting'; display_name?: string | null }
  | { type: 'capture_ready'; display_name?: string | null; sample_rate: number; channels: number }
  | { type: 'capture_level'; level: number; paused: boolean }
  | { type: 'capture_warning'; message: string }
  | { type: 'capture_ended'; reason: 'stopped' | 'error' | string }
  | { type: 'capture_error'; message: string }
  | { type: 'session_completed'; session_id: string }
  | { type: 'error'; message: string };

export const LANGUAGES = [
  { code: 'auto', name: '自动检测' },
  { code: 'zh', name: '中文' },
  { code: 'en', name: '英语' },
  { code: 'ja', name: '日语' },
  { code: 'ko', name: '韩语' },
  { code: 'fr', name: '法语' },
  { code: 'de', name: '德语' },
  { code: 'es', name: '西班牙语' },
  { code: 'ru', name: '俄语' },
] as const;

export const TARGET_LANGUAGES = LANGUAGES.filter((language) => language.code !== 'auto');

export function languageName(code: string | null): string {
  if (!code) return '未知语言';
  return LANGUAGES.find((language) => language.code === code)?.name ?? code.toUpperCase();
}

export function translationProviderLabel(provider: TranslationProvider | null | undefined): string {
  return provider === 'cc_switch' ? 'CC Switch' : 'Ollama';
}
