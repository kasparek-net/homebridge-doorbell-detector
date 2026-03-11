/**
 * homebridge-doorbell-detector — plugin entry point.
 */

import type { API } from 'homebridge';
import { DoorbellMLPlatform } from './platform';

const PLUGIN_NAME = 'homebridge-doorbell-detector';
const PLATFORM_NAME = 'DoorbellML';

export default (api: API): void => {
  api.registerPlatform(PLUGIN_NAME, PLATFORM_NAME, DoorbellMLPlatform);
};
