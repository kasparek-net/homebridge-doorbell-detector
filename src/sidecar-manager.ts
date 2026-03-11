/**
 * Manages the Python sidecar process and Unix socket communication.
 *
 * Lifecycle: spawn Python → connect Unix socket → ndjson bidirectional.
 * Socket placed in Homebridge storage dir (not /tmp/) to prevent symlink attacks.
 */

import { ChildProcess, spawn } from 'child_process';
import { createConnection, Socket } from 'net';
import { EventEmitter } from 'events';
import * as path from 'path';
import * as fs from 'fs';
import type { Logger } from 'homebridge';
import type { SidecarCommand, SidecarMessage } from './types';

const PYTHON_DIR = path.resolve(__dirname, '..', 'python');
const VENV_DIR = path.resolve(PYTHON_DIR, '.venv');
const CONNECT_RETRY_MS = 500;
const CONNECT_MAX_RETRIES = 20;

export class SidecarManager extends EventEmitter {
  private proc: ChildProcess | null = null;
  private socket: Socket | null = null;
  private buffer = '';
  private connected = false;
  private readonly socketPath: string;

  constructor(
    private readonly log: Logger,
    private readonly storagePath: string,
    private readonly pythonPath?: string,
  ) {
    super();
    // Socket in Homebridge storage dir — only Homebridge user has access
    this.socketPath = path.join(storagePath, 'doorbell-detector.sock');
  }

  // ── lifecycle ──────────────────────────────────────────────────

  async start(): Promise<void> {
    const python = this.resolvePython();
    this.log.info('Starting Python sidecar: %s', python);
    this.log.info('Socket path: %s', this.socketPath);

    const sidecarScript = path.join(PYTHON_DIR, 'sidecar.py');

    this.proc = spawn(python, [sidecarScript], {
      env: {
        ...process.env,
        DOORBELL_SOCKET: this.socketPath,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    this.proc.stdout!.on('data', (data: Buffer) => {
      for (const line of data.toString().split('\n')) {
        if (line.trim()) this.log.debug('[python] %s', line.trim());
      }
    });

    this.proc.stderr!.on('data', (data: Buffer) => {
      for (const line of data.toString().split('\n')) {
        if (line.trim()) this.log.warn('[python] %s', line.trim());
      }
    });

    this.proc.on('exit', (code, signal) => {
      this.log.warn('Python sidecar exited (code=%s, signal=%s)', code, signal);
      this.connected = false;
      this.socket = null;
      this.proc = null;
      this.emit('exit', code, signal);
    });

    await this.connectSocket();
  }

  async stop(): Promise<void> {
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
      this.connected = false;
    }
    if (this.proc) {
      this.proc.kill('SIGTERM');
      await new Promise<void>((resolve) => {
        const timeout = setTimeout(() => {
          if (this.proc) this.proc.kill('SIGKILL');
          resolve();
        }, 3000);
        this.proc!.on('exit', () => {
          clearTimeout(timeout);
          resolve();
        });
      });
      this.proc = null;
    }
  }

  // ── socket connection ──────────────────────────────────────────

  private async connectSocket(): Promise<void> {
    for (let attempt = 0; attempt < CONNECT_MAX_RETRIES; attempt++) {
      try {
        await this.tryConnect();
        this.log.info('Connected to Python sidecar socket');
        return;
      } catch {
        await new Promise((r) => setTimeout(r, CONNECT_RETRY_MS));
      }
    }
    throw new Error('Failed to connect to Python sidecar socket');
  }

  private tryConnect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = createConnection({ path: this.socketPath }, () => {
        this.socket = socket;
        this.connected = true;
        this.buffer = '';

        socket.on('data', (data: Buffer) => this.onData(data));
        socket.on('close', () => {
          this.connected = false;
          this.emit('disconnected');
        });
        socket.on('error', (err) => {
          this.log.error('Socket error: %s', err.message);
        });

        resolve();
      });
      socket.on('error', reject);
    });
  }

  private onData(data: Buffer): void {
    this.buffer += data.toString();
    let newlineIdx: number;
    while ((newlineIdx = this.buffer.indexOf('\n')) !== -1) {
      const line = this.buffer.slice(0, newlineIdx);
      this.buffer = this.buffer.slice(newlineIdx + 1);
      if (line) {
        try {
          const msg: SidecarMessage = JSON.parse(line);
          this.emit('message', msg);
          this.emit(msg.type, msg);
        } catch {
          this.log.warn('Invalid JSON from sidecar: %s', line.slice(0, 200));
        }
      }
    }
  }

  // ── send commands ──────────────────────────────────────────────

  send(command: SidecarCommand): void {
    if (!this.connected || !this.socket) {
      this.log.warn('Cannot send command — sidecar not connected');
      return;
    }
    this.socket.write(JSON.stringify(command) + '\n');
  }

  sendCommand(command: string, params: Record<string, unknown> = {}): void {
    this.send({ command, ...params });
  }

  get isConnected(): boolean {
    return this.connected;
  }

  // ── python resolution ──────────────────────────────────────────

  private resolvePython(): string {
    if (this.pythonPath) return this.pythonPath;

    const venvPython = path.join(VENV_DIR, 'bin', 'python');
    if (fs.existsSync(venvPython)) return venvPython;

    return 'python3';
  }
}
