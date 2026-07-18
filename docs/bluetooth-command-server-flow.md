# Bluetooth and BLE Command/Server Flow

## Scope

This document describes how Bluetooth- and BLE-related commands flow through the configurator server, from incoming API calls to DBus, systemd, and BLE GATT operations.

It covers:

- Bluetooth settings and device management via REST API
- BLE provisioning service lifecycle via REST API and systemd
- BLE GATT runtime behavior inside the provisioning process

## Main Components

- API route registration: src/server.py
- Bluetooth HTTP handler: src/handlers/bluetooth_handler.py
- Bluetooth DBus/config backend: src/bluetooth.py
- BLE provisioning HTTP handler: src/handlers/ble_handler.py
- BLE provisioning runtime: src/ble_provisioning.py
- systemd unit: systemd/ble-provisioning.service
- CLI entrypoint mapping: setup.py (config-ble-provision)

## Server Initialization Flow

At server startup (ConfigAPIServer.__init__ in src/server.py):

1. Flask app is created.
2. Handlers are instantiated, including:
   - BluetoothHandler
   - BLEProvisioningHandler
3. Routes are registered in _register_routes.

This means both Bluetooth and BLE lifecycle endpoints are always present when the server boots successfully.

## REST API to Bluetooth Backend Flow

### Endpoints

The following endpoints are registered in src/server.py:

- GET /api/v1/bluetooth/settings
- POST /api/v1/bluetooth/settings
- GET /api/v1/bluetooth/paired-devices
- POST /api/v1/bluetooth/unpair
- GET /api/v1/bluetooth/passkey
- POST /api/v1/bluetooth/passkey
- GET /api/v1/bluetooth/modal
- POST /api/v1/bluetooth/modal

### Settings Read/Write Path

Request flow:

1. Flask route in src/server.py dispatches to BluetoothHandler.
2. BluetoothHandler calls backend helpers in src/bluetooth.py.
3. ConfigFileManager reads/writes ~/.config/hifiberry/bluetooth.conf.
4. JSON response is returned to caller.

Details:

- get_bluetooth_settings builds ConfigFileManager, reads config values, returns normalized JSON keys.
- set_bluetooth_settings filters known keys and persists values via ConfigParser.

### Paired Device Query Path

Request flow:

1. GET /api/v1/bluetooth/paired-devices enters BluetoothHandler.handle_get_paired_devices.
2. Handler calls get_paired_devices in src/bluetooth.py.
3. get_paired_devices opens DBus system bus via dbus_fast MessageBus.
4. It introspects org.bluez at / and uses ObjectManager.GetManagedObjects.
5. Device objects with org.bluez.Device1 and Paired=true are transformed to response objects.

### Unpair Path

Request flow:

1. POST /api/v1/bluetooth/unpair reads address from query args.
2. Handler calls unpair_device in src/bluetooth.py.
3. unpair_device:
   - Introspects org.bluez root and reads managed objects
   - Finds matching Device1 by Address
   - Computes adapter path from device object path
   - Introspects adapter object and calls Adapter1.RemoveDevice(path)
4. Success/failure JSON is returned.

## REST API to BLE Provisioning Service Flow

### Endpoints

The following endpoints are registered in src/server.py:

- GET /api/v1/ble/provisioning/status
- POST /api/v1/ble/provisioning/start
- POST /api/v1/ble/provisioning/stop

### Status Flow

1. Route calls BLEProvisioningHandler.handle_get_status.
2. Handler executes:
   - systemctl is-active ble-provisioning
3. It returns active state and systemctl output-derived state.

### Start Flow

1. Route calls BLEProvisioningHandler.handle_start.
2. Handler creates runtime override:
   - /run/systemd/system/ble-provisioning.service.d/manual.conf
   - Contents clear ExecStartPre
3. Handler reloads unit files:
   - systemctl daemon-reload
4. Handler starts service:
   - systemctl start ble-provisioning

Reason for runtime override:

- The unit has ExecStartPre=/usr/bin/config-ble-provision --check-network.
- Manual start via API bypasses this pre-check by clearing ExecStartPre at runtime.

### Stop Flow

1. Route calls BLEProvisioningHandler.handle_stop.
2. Handler stops service:
   - systemctl stop ble-provisioning
3. Handler removes runtime override directory.
4. Handler reloads unit files:
   - systemctl daemon-reload

## systemd to BLE Runtime Flow

Unit behavior (systemd/ble-provisioning.service):

- ExecStartPre: /usr/bin/config-ble-provision --check-network
- ExecStart: /usr/bin/config-ble-provision --serve

CLI mapping is provided via setup.py console_scripts:

- config-ble-provision -> configurator.ble_provisioning:main

Startup path:

1. systemd invokes config-ble-provision --check-network.
2. ble_provisioning.main checks physical interfaces for IPv4.
3. If network exists, it exits non-zero and service does not start.
4. If no network exists, systemd runs config-ble-provision --serve.
5. BLEProvisioningServer starts GATT service and advertises.

## BLE GATT Runtime Flow

Defined in src/ble_provisioning.py using Bless:

- Service UUID: d5ae7526-9739-4baa-b9c0-5e5c11be9875
- Characteristics:
  - Device identity (read)
  - Network status (read/notify)
  - WiFi scan trigger (write)
  - WiFi scan results (read/notify)
  - WiFi connect (write)
  - WiFi connect status (read/notify)
  - BLE control (write)

### Read Requests

Read callback returns:

- Device identity JSON (hostname/model/version)
- Current network status JSON
- Cached WiFi scan results JSON
- Cached connect status JSON

### Write Requests

- WiFi scan trigger:
  - value 0xFF schedules asynchronous scan
  - results cached and notified through scan results characteristic

- WiFi connect:
  - payload JSON with ssid/passphrase
  - async connect via wifi.connect_to_wifi
  - status transitions to connecting, connected, or failed
  - notifications sent on connect status characteristic
  - network status characteristic updated after connect attempt

- BLE control:
  - payload action stop_ble requests loop stop and service shutdown path

## Error Handling Notes

- BLE HTTP handler returns structured 500 responses with captured exception message.
- Bluetooth DBus functions log DBusError separately and re-raise.
- BLE runtime logs operation errors and generally keeps service alive unless startup/loop fails.

## Important Integration Notes

1. Async boundary in Bluetooth handler:
   - get_paired_devices and unpair_device in src/bluetooth.py are async.
   - Current BluetoothHandler methods call them directly without await.
   - If this is intentional, there must be an external async bridge; otherwise these routes will return coroutine objects instead of executed results.

2. Manual BLE start override behavior:
   - API start intentionally bypasses ExecStartPre network gating.
   - API stop removes override, restoring normal boot-time gating.

3. System authority:
   - BLE lifecycle actions depend on systemctl and service permissions.
   - Bluetooth device actions depend on BlueZ DBus availability and permissions.
