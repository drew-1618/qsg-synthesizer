# NovaFiber GX400 — Quick Start Guide

## Overview

The NovaFiber GX400 is a carrier-grade XGS-PON fiber gateway designed for
zero-touch field deployment. Most installs require no on-site configuration.

## What's in the Box

- NovaFiber GX400 unit
- Fiber patch cord (SC/APC)
- Power adapter
- Quick Start card with QR code

No additional tools are required for a standard install. A smartphone
camera is used to scan the setup QR code.

## Provisioning

The GX400 auto-provisions on power-up:

- **TR-069 (CWMP)**: The unit calls home to the carrier ACS automatically
  once fiber and power are connected. All WAN, VoIP, and Wi-Fi profiles are
  pushed remotely — no technician configuration required.
- **DHCP**: LAN-side clients receive addresses automatically from the
  gateway's built-in DHCP server.
- **QR / Mobile App**: Installers (or end customers) may scan the QR code
  printed on the unit's label with the NovaFiber Connect app to confirm
  activation status and run a guided self-install flow.
- **Web GUI**: An advanced local web interface is available at
  `http://192.168.10.1` for technicians who need to override auto-provisioned
  settings, but it is not required for a standard install.

No console port is present on this model; there is no local CLI access.

## Cabling & Ports

| Port | Type | Notes |
|------|------|-------|
| PON | SC/APC fiber | Connects to carrier ONT drop cable |
| LAN1–LAN4 | RJ45 Gigabit Ethernet | Auto-negotiating |
| Wi-Fi | 802.11ax (internal) | No external antenna cabling |
| Power | DC barrel, 12V | Included adapter only |

## LED Indicators

| LED | Meaning |
|-----|---------|
| Power/Status | pulsing green during startup and ACS handshake |
| Power/Status | solid green once fully online and provisioned |
| PON | blinking amber 2Hz while acquiring optical signal |
| PON | solid green once optical link is established |
| Alert | solid red if optical signal is lost or provisioning fails |

## Installation Steps

1. Connect the fiber patch cord to the PON port.
2. Connect power.
3. Wait for the Power/Status LED to turn solid green (typically under 3
   minutes).
4. Optionally scan the QR code with the NovaFiber Connect app to verify
   service.

No screwdriver, console cable, or laptop is required for a standard
residential or SMB install.
