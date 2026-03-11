/**
 * WebSocket bridge — forwards Python sidecar audio/detection stream
 * to Homebridge Config UI X custom dashboard.
 *
 * Security:
 * - Binds to 127.0.0.1 only (not reachable from LAN)
 * - Requires auth token on connection (URL param ?token=...)
 * - Token generated per-session by the platform
 */

import { WebSocketServer, WebSocket } from 'ws';
import { IncomingMessage } from 'http';
import { URL } from 'url';
import * as crypto from 'crypto';
import type { Logger } from 'homebridge';
import type { SidecarManager } from './sidecar-manager';
import type { SidecarMessage } from './types';

const FORWARDED_TYPES = new Set([
  'audio_frame',
  'detection',
  'status',
  'method_changed',
  'recording_started',
  'recording_complete',
  'training_started',
  'training_progress',
  'training_complete',
  'training_failed',
  'devices',
  'sample_counts',
  'samples_deleted',
  'config_updated',
  'device_set',
  'level',
  'sample_list',
  'sample_audio',
  'sample_deleted',
  'sidecar_status',
  'error',
]);

export class WebSocketBridge {
  private wss: WebSocketServer | null = null;
  private clients = new Set<WebSocket>();
  private readonly authToken: string;

  constructor(
    private readonly log: Logger,
    private readonly sidecar: SidecarManager,
    private readonly port: number,
  ) {
    // Generate random auth token for this session
    this.authToken = crypto.randomBytes(32).toString('hex');
  }

  get token(): string {
    return this.authToken;
  }

  start(): void {
    this.wss = new WebSocketServer({
      port: this.port,
      host: '127.0.0.1',  // Localhost only — not reachable from LAN
      verifyClient: (info, cb) => this.verifyClient(info, cb),
    });
    this.log.info('WebSocket bridge listening on 127.0.0.1:%d (auth required)', this.port);

    // Track sidecar lifecycle and forward to UI clients
    this.sidecar.on('exit', () => {
      this.broadcast({ type: 'sidecar_status', connected: false, error: 'Sidecar process exited' });
    });
    this.sidecar.on('disconnected', () => {
      this.broadcast({ type: 'sidecar_status', connected: false, error: 'Socket disconnected' });
    });

    this.wss.on('connection', (ws) => {
      this.clients.add(ws);
      this.log.debug('UI client authenticated and connected (total: %d)', this.clients.size);

      // Send sidecar status and request current state
      if (this.sidecar.isConnected) {
        ws.send(JSON.stringify({ type: 'sidecar_status', connected: true }));
        this.sidecar.sendCommand('get_status');
      } else {
        ws.send(JSON.stringify({ type: 'sidecar_status', connected: false, error: 'Sidecar not connected' }));
      }

      ws.on('message', (raw) => {
        try {
          const msg = JSON.parse(raw.toString());
          if (msg.command) {
            this.sidecar.send(msg);
          }
        } catch {
          this.log.warn('Invalid message from UI: %s', raw.toString().slice(0, 200));
        }
      });

      ws.on('close', () => {
        this.clients.delete(ws);
        this.log.debug('UI client disconnected (total: %d)', this.clients.size);
      });

      ws.on('error', (err) => {
        this.log.warn('UI WebSocket error: %s', err.message);
        this.clients.delete(ws);
      });
    });

    // Forward sidecar messages to all UI clients
    this.sidecar.on('message', (msg: SidecarMessage) => {
      if (!FORWARDED_TYPES.has(msg.type)) return;
      if (this.clients.size === 0) return;

      const data = JSON.stringify(msg);
      for (const ws of this.clients) {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(data);
        }
      }
    });
  }

  private verifyClient(
    info: { origin: string; secure: boolean; req: IncomingMessage },
    cb: (result: boolean, code?: number, message?: string) => void,
  ): void {
    try {
      const url = new URL(info.req.url || '/', `http://${info.req.headers.host}`);
      const token = url.searchParams.get('token');

      if (token && crypto.timingSafeEqual(
        Buffer.from(token),
        Buffer.from(this.authToken),
      )) {
        cb(true);
      } else {
        this.log.warn('WebSocket auth rejected (invalid or missing token)');
        cb(false, 401, 'Unauthorized');
      }
    } catch {
      cb(false, 400, 'Bad request');
    }
  }

  stop(): void {
    for (const ws of this.clients) {
      ws.close();
    }
    this.clients.clear();
    if (this.wss) {
      this.wss.close();
      this.wss = null;
    }
  }

  /** Notify that sidecar reconnected (called by platform after restart). */
  notifySidecarConnected(): void {
    this.broadcast({ type: 'sidecar_status', connected: true });
  }

  private broadcast(msg: Record<string, unknown>): void {
    if (this.clients.size === 0) return;
    const data = JSON.stringify(msg);
    for (const ws of this.clients) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    }
  }

  get clientCount(): number {
    return this.clients.size;
  }
}
