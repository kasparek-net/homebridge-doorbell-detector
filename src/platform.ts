/**
 * DoorbellML Platform — orchestrates sidecar, WebSocket bridge, and accessory.
 */

import type {
  API,
  DynamicPlatformPlugin,
  Logger,
  PlatformAccessory,
  PlatformConfig,
} from 'homebridge';
import { SidecarManager } from './sidecar-manager';
import { WebSocketBridge } from './ws-bridge';
import { DoorbellAccessory } from './accessory';
import type { DoorbellMLConfig } from './types';
import * as path from 'path';
import * as fs from 'fs';

const PLUGIN_NAME = 'homebridge-doorbell-detector';
const PLATFORM_NAME = 'DoorbellML';
const ACCESSORY_UUID_SEED = 'doorbell-detector-primary';

export class DoorbellMLPlatform implements DynamicPlatformPlugin {
  private readonly config: DoorbellMLConfig;
  private readonly sidecar: SidecarManager;
  private readonly wsBridge: WebSocketBridge;
  private readonly cachedAccessories: PlatformAccessory[] = [];

  constructor(
    private readonly log: Logger,
    config: PlatformConfig,
    private readonly api: API,
  ) {
    this.config = config as DoorbellMLConfig;

    const storagePath = this.api.user.storagePath();

    this.sidecar = new SidecarManager(
      this.log,
      storagePath,
      this.config.pythonPath,
    );

    this.wsBridge = new WebSocketBridge(
      this.log,
      this.sidecar,
      this.config.wsPort ?? 8581,
    );

    // Persist auth token for UI server.js to read
    this.writeAuthToken(storagePath);

    this.api.on('didFinishLaunching', () => {
      this.bootstrap().catch((err) => {
        this.log.error('Failed to bootstrap: %s', err.message);
      });
    });

    this.api.on('shutdown', () => {
      this.teardown().catch((err) => {
        this.log.error('Error during teardown: %s', err.message);
      });
    });
  }

  configureAccessory(accessory: PlatformAccessory): void {
    this.cachedAccessories.push(accessory);
  }

  private writeAuthToken(storagePath: string): void {
    const tokenPath = path.join(storagePath, 'doorbell-detector-token');
    fs.writeFileSync(tokenPath, this.wsBridge.token, { mode: 0o600 });
  }

  private async bootstrap(): Promise<void> {
    // 1. Start Python sidecar
    await this.sidecar.start();

    // 2. Apply config to sidecar
    const method = this.config.detectionMethod ?? 'fingerprint';
    this.sidecar.sendCommand('set_method', { method });
    if (this.config.threshold != null) {
      this.sidecar.sendCommand('set_threshold', { value: this.config.threshold });
    }
    if (this.config.cooldown != null) {
      this.sidecar.sendCommand('set_cooldown', { value: this.config.cooldown });
    }
    if (this.config.audioDevice != null) {
      this.sidecar.sendCommand('set_device', { device_index: this.config.audioDevice });
    }

    // 3. Start WebSocket bridge for Config UI
    this.wsBridge.start();

    // 4. Register doorbell accessory
    this.registerAccessory();

    // 5. Auto-start detection if configured
    if (this.config.autoStart !== false) {
      this.sidecar.sendCommand('start_detection');
    }

    // 6. Handle sidecar restarts
    this.sidecar.on('exit', () => {
      this.log.warn('Sidecar exited — restarting in 5s...');
      setTimeout(() => {
        this.sidecar.start().then(() => {
          this.wsBridge.notifySidecarConnected();
          this.sidecar.sendCommand('set_method', { method });
          if (this.config.autoStart !== false) {
            this.sidecar.sendCommand('start_detection');
          }
        }).catch((err) => {
          this.log.error('Sidecar restart failed: %s', err.message);
        });
      }, 5000);
    });

    this.log.info('DoorbellML platform started (method=%s, ws=127.0.0.1:%d)',
      method, this.config.wsPort ?? 8581);
  }

  private registerAccessory(): void {
    const uuid = this.api.hap.uuid.generate(ACCESSORY_UUID_SEED);

    let accessory = this.cachedAccessories.find((a) => a.UUID === uuid);
    if (!accessory) {
      const name = this.config.name ?? 'Doorbell ML';
      accessory = new this.api.platformAccessory(name, uuid);
      this.api.registerPlatformAccessories(PLUGIN_NAME, PLATFORM_NAME, [accessory]);
      this.log.info('Registered new accessory: %s', name);
    }

    new DoorbellAccessory(this.api, this.log, accessory, this.sidecar);
  }

  private async teardown(): Promise<void> {
    this.wsBridge.stop();
    await this.sidecar.stop();
    this.log.info('DoorbellML platform stopped');
  }
}
