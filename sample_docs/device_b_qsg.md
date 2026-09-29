# LegacyLink RG-210 — Quick Start Guide

## Overview

The LegacyLink RG-210 is a DSL/Ethernet gateway. Installation requires a
technician to manually configure WAN settings via the local console or web
interface; the unit does not support remote auto-provisioning.

## What's in the Box

- LegacyLink RG-210 unit
- RJ11 DSL cable
- RJ45 Ethernet cable
- Power adapter
- RS-232-to-USB console cable

## Required Tools

- Phillips (#2) screwdriver, to remove the rear panel and set internal
  jumpers
- RS-232-to-USB console cable (included) and a laptop with a terminal
  emulator (e.g. PuTTY, minicom) for initial configuration
- Small flathead screwdriver, to set the DIP switch bank for WAN mode
  (PPPoE vs. static IP)

## Provisioning

- **Console**: Initial setup is performed over the RS-232 console port at
  9600 baud using a terminal emulator. Technicians log in with the default
  credentials printed on the bottom label and manually enter WAN
  credentials.
- **Web GUI**: Once the unit is powered on, a local configuration page is
  reachable at `http://192.168.1.1` (default static management IP) for
  further configuration after initial console setup.
- **Other**: WAN connection mode (PPPoE vs. static) is selected via a
  physical 4-position DIP switch bank on the rear panel before power-on.
  Incorrect switch settings will prevent WAN negotiation.

DHCP and TR-069 are not supported on this model.

## Key Features

- 2x FXS ports for analog phone service (VoIP)
- Built-in battery backup connector for lifeline phone service
- Parental controls via MAC address filtering

## Specifications

- Wi-Fi Standard: 802.11ac (Wi-Fi 5), dual-band
- WAN Speed: Up to 100 Mbps (ADSL2+/VDSL2)
- Form Factor: Desktop, not wall-mountable

## Cabling & Ports

| Port | Type | Notes |
|------|------|-------|
| DSL | RJ11 | Connects to telco NID |
| LAN1–LAN4 | RJ45 Fast Ethernet | Manual VLAN jumpers on rear panel |
| Console | RS-232 (RJ45-to-DB9) | Requires included adapter cable |
| Power | DC barrel, 12V | Included adapter only |

## LED Indicators

| LED | Meaning |
|-----|---------|
| Power | solid green once the unit finishes its boot self-test |
| Power | blinking green during boot self-test |
| DSL | blinking amber while training/syncing with the DSLAM |
| DSL | solid green once DSL sync is achieved |
| Status | solid red if DSL sync fails after 3 attempts |
| Status | blinking red if an internal self-test fails |

## Installation Steps

1. Power off the unit before removing the rear panel.
2. Set the WAN mode DIP switches per the table in Appendix B.
3. Reattach the rear panel and connect the DSL and power cables.
4. Connect the console cable to a laptop and open a terminal session at
   9600 baud.
5. Log in with the default console credentials and enter WAN account
   credentials as provided by the carrier.
6. Verify the DSL LED reaches solid green before disconnecting the console
   cable.
7. Optionally browse to `http://192.168.1.1` to configure Wi-Fi and LAN
   settings.
