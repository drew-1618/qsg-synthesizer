# Getting Started with the Skyreach AP-630

The Skyreach AP-630 is a Wi-Fi 7 indoor access point for ceiling or wall mounting.

## Package contents
- AP-630 access point
- Mounting bracket and T-bar clips
- Wall anchors and screws

## What you'll need
- Phillips screwdriver
- Drill with a 6 mm bit (wall mounting only)
- PoE+ (802.3at) switch or injector
- Cat6 Ethernet cable

## Mounting the access point
1. Attach the bracket to the ceiling rail with the T-bar clips.
2. Run the Ethernet cable through the bracket opening.
3. Connect the cable to the ETH0/PoE port.
4. Slide the AP onto the bracket and turn it clockwise until it clicks.

## Initial configuration
1. Make sure the switch port has internet access and a DHCP server on its VLAN.
2. Power the AP from the PoE+ switch.
3. The AP obtains an IP address via DHCP and connects to the Skyreach cloud controller automatically.
4. Claim the AP in the Skyreach portal with the serial number on its label.
5. Wait for the status LED to turn solid blue.

If no DHCP server is available, connect a computer directly and browse to 192.168.0.1 to set a static address.

## LEDs

| LED | Color | Meaning |
|---|---|---|
| Status | Blinking amber | Booting |
| Status | Solid blue | Connected to the cloud controller |
| Status | Blinking red | No uplink |

## Ports
- ETH0/PoE: 2.5GBASE-T RJ45, PoE+ input
- ETH1: 1000BASE-T RJ45
- Console: RJ45 serial, 115200 baud (for support use)

## Specifications
- Wi-Fi standard: 802.11be (Wi-Fi 7), tri-band
- Power draw: 25.5 W max
- Dimensions: 220 x 220 x 45 mm
