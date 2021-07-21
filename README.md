# Cisco PNP without APIC/DNAC

Credits to : 
- https://github.com/dmfigol/cisco-pnp-server
- https://github.com/pmeffre/ciscopnp

# Execution
```shell
python3 main.py
```

# How to use

You have to create a config directory (./configs) which must contain:

 - "Serial Number".cfg

Configure your DHCP to point a blank Device to the PnP Server via DHCP Option 43

value: 5A1D;K4;B2;I198.18.168.3;J8080

- 5: DHCP sub-option for PnP
- A: feature-code for Active
- 1: Version
- D: Debug On

- K: Defines the Transport Protocol as 4 = HTTP
- B: Defines the Server Adress as 2 = IPv4
- I: is your Servers IP
- J: Server Port

Reference: [Cisco Devnet Open-PnP](https://developer.cisco.com/site/open-plug-n-play/learn/learn-open-pnp-protocol/)

Can be used in combination with: [oliverl-21/ansible-role-ios_config](https://github.com/oliverl-21/ansible-role-ios_config) via PnP Flow with a dynamic CSV Inventory

