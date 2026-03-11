/**
 * Config UI X server-side handler.
 *
 * Serves the WebSocket port + auth token to the frontend.
 * Token is read from Homebridge storage dir (written by platform.ts).
 */

const { HomebridgePluginUiServer } = require('@homebridge/plugin-ui-utils');
const fs = require('fs');
const path = require('path');

class DoorbellMLUiServer extends HomebridgePluginUiServer {
  constructor() {
    super();

    this.onRequest('/ws-config', async () => {
      let port = 8581;

      // Try to read port from Homebridge config
      try {
        const storagePath = this.homebridgeStoragePath ||
          process.env.UIX_STORAGE_PATH ||
          '/homebridge';
        const configPath = path.join(storagePath, 'config.json');
        const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
        const platforms = config.platforms || [];
        const doorbell = platforms.find(p => p.platform === 'DoorbellML');
        if (doorbell && doorbell.wsPort) {
          port = doorbell.wsPort;
        }
      } catch (e) {
        // Use default port
      }

      // Read auth token from Homebridge storage
      let token = '';
      try {
        const storagePath = this.homebridgeStoragePath ||
          process.env.UIX_STORAGE_PATH ||
          '/homebridge';
        const tokenPath = path.join(storagePath, 'doorbell-detector-token');
        token = fs.readFileSync(tokenPath, 'utf8').trim();
      } catch (e) {
        // Token not yet written — sidecar may not have started
      }

      return { port, token };
    });

    this.ready();
  }
}

(() => new DoorbellMLUiServer())();
