# **Brightline ONT-2410 Installation Guide**

**Before You Begin**

- Fiber optic cleaning kit

- Optical power meter (recommended)

- #2 Phillips screwdriver for wall mounting

## **Interfaces**

|**Interface**|**Connector**|**Description**|
|---|---|---|
|PON|SC/APC|Connects to the fiber drop|
|GE1–GE4|RJ45|Gigabit Ethernet to customer equipment|
|POTS1–POTS2|RJ11|Analog telephone|
|DC 12V|Barrel jack|Power input|

## **LED Status**

|**LED**|**Status**|**Description**|
|---|---|---|
|PWR|Solid green|Power is on|
||Off|No power|
|PON|Blinking green|Ranging with the OLT|
||Solid green|Registered with the OLT|
|LOS|Blinking red|Optical signal lost|
|ALARM|Solid amber|Hardware fault|

## **Installation**

- 1 Mount the ONT on the wall with the supplied screws.

- 2 Clean the fiber connector and connect the drop to the PON port.

- 3 Connect power. The PWR LED lights solid green.

- 4 Wait for the PON LED to turn solid green. The ONT registers with the OLT and downloads its configuration from the carrier's ACS over TR-069.

- 5 Confirm the LOS LED is off.

## **Local management**

For troubleshooting, browse to http://192.168.100.1 from a computer on GE1.
