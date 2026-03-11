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
      const config = await this.getPluginConfig();
      const pluginConfig = config[0] || {};
      const port = pluginConfig.wsPort || 8581;

      // Read auth token from Homebridge storage
      let token = '';
      try {
        const storagePath = this.homebridgeStoragePath;
        const tokenPath = path.join(storagePath, 'doorbell-detector-token');
        token = fs.readFileSync(tokenPath, 'utf8').trim();
      } catch (e) {
        this.log.warn('Could not read auth token:', e.message);
      }

      return { port, token };
    });

    this.ready();
  }
}

(() => new DoorbellMLUiServer())();
