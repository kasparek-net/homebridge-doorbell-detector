/**
 * Homebridge Doorbell accessory — exposes a HAP Doorbell service
 * that triggers ProgrammableSwitchEvent on detection.
 */

import type {
  API,
  Logger,
  PlatformAccessory,
  Service,
  CharacteristicValue,
} from 'homebridge';
import type { SidecarManager } from './sidecar-manager';
import type { DetectionMessage } from './types';

export class DoorbellAccessory {
  private readonly service: Service;

  constructor(
    private readonly api: API,
    private readonly log: Logger,
    private readonly accessory: PlatformAccessory,
    private readonly sidecar: SidecarManager,
  ) {
    const Service = this.api.hap.Service;
    const Characteristic = this.api.hap.Characteristic;

    // Accessory information
    const infoService =
      this.accessory.getService(Service.AccessoryInformation) ||
      this.accessory.addService(Service.AccessoryInformation);
    infoService
      .setCharacteristic(Characteristic.Manufacturer, 'homebridge-doorbell-detector')
      .setCharacteristic(Characteristic.Model, 'ML Doorbell')
      .setCharacteristic(Characteristic.SerialNumber, 'DBML-001');

    // Doorbell service
    this.service =
      this.accessory.getService(Service.Doorbell) ||
      this.accessory.addService(Service.Doorbell, 'Doorbell');

    // Listen for detections from Python sidecar
    this.sidecar.on('detection', (msg: DetectionMessage) => {
      this.triggerDoorbell(msg);
    });

    // Listen for test doorbell from UI
    this.sidecar.on('test_doorbell', () => {
      this.log.info('Test doorbell triggered from UI');
      this.service
        .getCharacteristic(Characteristic.ProgrammableSwitchEvent)
        .updateValue(Characteristic.ProgrammableSwitchEvent.SINGLE_PRESS);
    });
  }

  private triggerDoorbell(msg: DetectionMessage): void {
    const Characteristic = this.api.hap.Characteristic;

    this.log.info(
      'Doorbell triggered! method=%s confidence=%.2f',
      msg.method,
      msg.confidence,
    );

    this.service
      .getCharacteristic(Characteristic.ProgrammableSwitchEvent)
      .updateValue(Characteristic.ProgrammableSwitchEvent.SINGLE_PRESS);
  }
}
