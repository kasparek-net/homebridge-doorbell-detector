/**
 * Shared TypeScript types for homebridge-doorbell-detector.
 */

import type { PlatformConfig } from 'homebridge';

// ── Plugin config ────────────────────────────────────────────────

export interface DoorbellMLConfig extends PlatformConfig {
  detectionMethod?: 'fingerprint' | 'ml';
  threshold?: number;
  cooldown?: number;
  audioDevice?: number;
  wsPort?: number;
  pythonPath?: string;
  autoStart?: boolean;
}

// ── Python sidecar protocol (ndjson) ─────────────────────────────

/** Messages sent TO the Python sidecar. */
export interface SidecarCommand {
  command: string;
  [key: string]: unknown;
}

/** Messages received FROM the Python sidecar. */
export type SidecarMessage =
  | AudioFrameMessage
  | DetectionMessage
  | StatusMessage
  | MethodChangedMessage
  | RecordingCompleteMessage
  | TrainingProgressMessage
  | TrainingCompleteMessage
  | TrainingFailedMessage
  | ErrorMessage
  | DevicesMessage
  | SampleCountsMessage
  | LevelMessage
  | TestDoorbellMessage
  | SampleListMessage
  | SampleAudioMessage
  | SampleDeletedMessage
  | SidecarStatusMessage
  | GenericMessage;

export interface AudioFrameMessage {
  type: 'audio_frame';
  method: string;
  confidence: number;
  is_detection: boolean;
  rms: number;
  inference_ms: number;
  timestamp: number;
  waveform_b64: string;
  spectrogram_b64: string;
  spectrogram_shape: number[];
}

export interface DetectionMessage {
  type: 'detection';
  method: string;
  confidence: number;
  timestamp: number;
}

export interface StatusMessage {
  type: 'status';
  detecting: boolean;
  mic_active?: boolean;
  method?: string;
  fingerprint_loaded?: boolean;
  fingerprint_count?: number;
  ml_loaded?: boolean;
  ml_metadata?: Record<string, unknown> | null;
  threshold?: number;
  cooldown?: number;
  counts?: Record<string, number>;
}

export interface MethodChangedMessage {
  type: 'method_changed';
  method: string;
  is_loaded: boolean;
}

export interface RecordingCompleteMessage {
  type: 'recording_complete';
  label: string;
  path: string;
  counts: Record<string, number>;
  fingerprint_count: number | null;
  quality?: {
    rms: number;
    rms_db: number;
    peak: number;
    clipped_ratio: number;
  };
}

export interface TrainingProgressMessage {
  type: 'training_progress';
  epoch: number;
  total_epochs: number;
  loss: number;
  accuracy: number;
}

export interface TrainingCompleteMessage {
  type: 'training_complete';
  metrics: Record<string, unknown>;
}

export interface TrainingFailedMessage {
  type: 'training_failed';
  error: string;
}

export interface ErrorMessage {
  type: 'error';
  command?: string;
  error: string;
}

export interface DevicesMessage {
  type: 'devices';
  devices: Array<{ index: number; name: string }>;
}

export interface SampleCountsMessage {
  type: 'sample_counts';
  counts: Record<string, number>;
}

export interface LevelMessage {
  type: 'level';
  rms: number;
  peak: number;
}

export interface TestDoorbellMessage {
  type: 'test_doorbell';
}

export interface SampleListMessage {
  type: 'sample_list';
  samples: Array<{
    id: string;
    label: string;
    filename: string;
    duration_s: number;
    size_bytes: number;
  }>;
}

export interface SampleAudioMessage {
  type: 'sample_audio';
  label: string;
  filename: string;
  wav_b64: string;
}

export interface SampleDeletedMessage {
  type: 'sample_deleted';
  label: string;
  filename: string;
  counts: Record<string, number>;
}

export interface SidecarStatusMessage {
  type: 'sidecar_status';
  connected: boolean;
  error?: string;
}

export interface GenericMessage {
  type: string;
  [key: string]: unknown;
}
