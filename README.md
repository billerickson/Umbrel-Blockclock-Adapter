# Umbrel BLOCKCLOCK adapter

Run a Coinkite BLOCKCLOCK mini without giving it Internet access. This service
collects Bitcoin data on an Umbrel host, makes one allowlisted HTTPS request to
Coinbase, calculates Moscow Time locally, and pushes values to the BLOCKCLOCK
through Coinkite's documented local API.

The BLOCKCLOCK never needs to initiate a connection to the Internet, DNS, or
Umbrel.

## Architecture

```text
Umbrel Mempool :3006 --+
Umbrel Public Pool API :2019 -+--> BLOCKCLOCK adapter --HTTP push--> BLOCKCLOCK mini
Coinbase HTTPS --------+

BLOCKCLOCK-initiated traffic to LAN/WAN: blocked
```

The adapter rotates one value onto the E-Ink display every five minutes.

## Displayed values

| Display | Source |
| --- | --- |
| Block height | `GET http://127.0.0.1:3006/api/blocks/tip/height` |
| Fastest fee | `fastestFee` from `GET http://127.0.0.1:3006/api/v1/fees/recommended` |
| BTC/USD | `GET https://api.coinbase.com/v2/prices/BTC-USD/spot` |
| Moscow Time | `round(100,000,000 / BTC_USD)` |
| Pool hash rate | `totalHashRate` from `GET http://127.0.0.1:2019/api/pool` |
| Blocks found | Length or numeric value of `blocksFound` from the pool response |

The Public Pool metrics are optional and can be disabled through `ENABLED_METRICS`.

## Example network

All addresses below are examples. Replace them with reservations that do not
conflict with your network.

| Purpose | Example |
| --- | --- |
| Trusted LAN | `192.168.10.0/24` |
| Trusted gateway | `192.168.10.1` |
| Umbrel | `192.168.10.20` |
| Admin workstation | `192.168.10.30` |
| IoT VLAN ID | `40` |
| IoT network | `192.168.40.0/24` |
| IoT gateway | `192.168.40.1` |
| BLOCKCLOCK mini | `192.168.40.20` |
| IoT SSID | `Blockclock-IoT` |

Use DHCP reservations or UniFi fixed IP assignments for Umbrel, the admin
workstation, and the BLOCKCLOCK. The firewall policy depends on stable source
and destination addresses.

## Create the isolated UniFi network

UniFi labels and menu paths vary between Network releases, but the resulting
policy should be the same.

1. Open **Settings → Networks → Create New Network**.
2. Name it `Blockclock-IoT`.
3. Select the UniFi gateway as its router.
4. Disable automatic subnet scaling if it chooses an unexpectedly small
   network.
5. Set the gateway/subnet to `192.168.40.1/24`.
6. Set VLAN ID `40`.
7. Enable the DHCP server. A normal `/24` is easier to manage than a `/30`,
   even when the BLOCKCLOCK is currently the only client.
8. Enable **Network Isolation**.
9. Disable **Allow Internet Access** or apply a complete Internet block to this
   network in Policy Engine.
10. Leave mDNS forwarding, UPnP, port forwarding, and captive portal features
    disabled unless another device on the VLAN specifically needs them.

UniFi's Network Isolation setting automatically blocks inter-VLAN traffic. See
[Ubiquiti's network isolation guide](https://help.ui.com/hc/en-us/articles/18965560820247-Implementing-Network-and-Client-Isolation-in-UniFi)
and [VLAN creation guide](https://help.ui.com/hc/en-us/articles/9761080275607-Creating-Virtual-Networks-VLANs).

### Create the IoT WiFi

1. Open **Settings → WiFi → Create New WiFi**.
2. Use the SSID `Blockclock-IoT`.
3. Assign it to the `Blockclock-IoT` network/VLAN.
4. Use a strong, unique WiFi password. WPA2 or WPA2/WPA3 mixed mode may be
   necessary for the ESP32-based BLOCKCLOCK; do not assume it supports
   WPA3-only mode.
5. Enable WiFi client isolation if more untrusted IoT devices will share this
   SSID. Client isolation controls same-VLAN traffic and complements gateway
   isolation.
6. Connect the BLOCKCLOCK and assign it the fixed example address
   `192.168.40.20`.

## Firewall policy

The desired flow is deliberately one-way:

- Umbrel may initiate a connection to the BLOCKCLOCK push API.
- The optional admin workstation may initiate a connection to the BLOCKCLOCK
  web interface.
- The BLOCKCLOCK may only send established/related replies.
- All other BLOCKCLOCK-initiated LAN and Internet traffic is blocked.
- Other trusted-LAN clients cannot initiate connections to the BLOCKCLOCK.

Do **not** add a BLOCKCLOCK → Umbrel rule. The adapter pushes data from Umbrel;
the BLOCKCLOCK never polls the adapter.

### Modern zone-based firewall

On current UniFi releases, create policies in **Settings → Zones** or
**Settings → Policy Table**. Put the IoT VLAN in an untrusted or custom IoT
zone, then create these policies:

1. Allow Umbrel `192.168.10.20` to reach BLOCKCLOCK `192.168.40.20`.
2. Optionally allow admin workstation `192.168.10.30` to reach
   `192.168.40.20`.
3. Enable automatic return traffic for those allow rules.
4. Block IoT → Internal.
5. Block IoT → External/Internet.
6. Block other Internal → BLOCKCLOCK traffic.

Prefer TCP destination port `80` when the UniFi release applies that match
correctly. If the device cannot be reached, temporarily test the same rule with
protocol `All`; keep the source and destination limited to the exact reserved
addresses.

UniFi documents the current zone model and its automatic return-traffic option
in the [zone-based firewall guide](https://help.ui.com/hc/en-us/articles/115003173168-Zone-Based-Firewalls-in-UniFi).

### Legacy Advanced firewall rules

For the older **LAN In** interface, place custom rules **Before Predefined** in
this exact top-to-bottom order. Legacy rules are evaluated by index, so a broad
drop above an exception will make the exception appear broken.

| Order | Name | Action | Source | Destination | State |
| --- | --- | --- | --- | --- | --- |
| 1 | Allow Admin to BLOCKCLOCK | Accept | `192.168.10.30` | `192.168.40.20` | Auto |
| 2 | Allow BLOCKCLOCK Established Replies | Accept | `192.168.40.20` | Trusted LAN | Established, Related |
| 3 | Allow Umbrel to BLOCKCLOCK API | Accept | `192.168.10.20` | `192.168.40.20` | Auto |
| 4 | Block Other LAN Access to BLOCKCLOCK | Drop | Trusted LAN | `192.168.40.20` | Auto |

Start with TCP port `80` for rules 1 and 3. On one tested legacy UniFi build,
the TCP/port selector did not pass the BLOCKCLOCK traffic even though the
addresses and ordering were correct. The working fallback was protocol `All`
with exact source and destination matches. This is still tightly scoped to the
two named devices.

The Established/Related rule must remain above Network Isolation's generated
drop rule so replies to Umbrel and the admin workstation are not discarded.
UniFi explains LAN In direction, connection states, and rule ordering in its
[legacy Advanced Firewall documentation](https://help.ui.com/hc/en-us/articles/27699646208279-UniFi-Gateway-Advanced-Firewall-Rules).

## Configure the BLOCKCLOCK

Open `http://192.168.40.20/` from the allowed admin workstation.

1. Open **Preferences**.
2. Set **Data Backend** to `127.0.0.1`. This prevents the firmware from trying
   Coinkite's Internet backend even if a firewall rule is accidentally relaxed.
3. Set a strong **System Password**.
4. Open **Display** and set **Screen Update Rate** to **Manual**. Otherwise the
   normal pull cycle may replace values pushed by the adapter.
5. Confirm the BLOCKCLOCK is running a trusted, signature-verified firmware
   release. Firmware updates can be downloaded on another computer and applied
   through the local firmware upload page or MicroSD card without granting the
   device Internet access.

The adapter supports HTTP Digest authentication. Put the same system password
in `BLOCKCLOCK_PASSWORD` on Umbrel.

## Install on Umbrel

Copy this repository to Umbrel, then run:

```sh
mkdir -p ~/umbrel/app-data/blockclock-adapter
cd ~/umbrel/app-data/blockclock-adapter
cp .env.example .env
```

Edit `.env` and replace the example BLOCKCLOCK address:

```dotenv
BLOCKCLOCK_URL=http://192.168.40.20
BLOCKCLOCK_PASSWORD=replace-with-your-blockclock-password
```

Start the service:

```sh
sudo docker compose up -d --build
sudo docker compose logs -f --tail=100
```

The container uses host networking so it can reach the Umbrel apps on
`127.0.0.1:3006` and `127.0.0.1:2019`. Its status API binds only to
`127.0.0.1:21022` by default.

Check status on Umbrel:

```sh
curl -fsS http://127.0.0.1:21022/status
```

A healthy response has an empty `errors` object and `display_error: null`.

## Configuration

Set `ENABLED_METRICS` to omit the optional pool displays:

```dotenv
ENABLED_METRICS=block_height,fastest_fee,btc_price,moscow_time
```

The display interval cannot be set below 60 seconds. Five minutes is the
default because Coinkite recommends a low update rate for the E-Ink display.

The price URL must use HTTPS and its hostname must appear in
`PRICE_ALLOWED_HOSTS`. The defaults permit only `api.coinbase.com`.

## Verify the isolation

Run these checks after installation and after major UniFi or Umbrel updates.

From Umbrel, the local push API should respond:

```sh
curl -fsS http://192.168.40.20/api/status
```

From the allowed admin workstation, the web interface should redirect to
`/display`:

```sh
curl -I http://192.168.40.20/
```

From any other trusted-LAN client, the same request should time out or be
blocked. If the allowed workstation fails too, check whether a VPN or exit-node
client is intercepting private-network traffic; enable its local-LAN bypass.

Confirm the dangerous MicroPython WebREPL port is not listening:

```sh
timeout 4 bash -c '</dev/tcp/192.168.40.20/8266' \
  && echo 'WARNING: WebREPL is open' \
  || echo 'WebREPL is not listening'
```

Zero or near-zero bandwidth in the UniFi client view is normal while the
display is idle. The adapter initiates a short local request only when it
changes the display.

## Threat-model notes

- The BLOCKCLOCK web interface is HTTP, not HTTPS. Keep it on the isolated
  VLAN and never port-forward it.
- Do not expose the adapter status port beyond `127.0.0.1` unless another
  protected monitoring host genuinely needs it.
- The application validates that the price endpoint uses HTTPS and an
  allowlisted hostname. Network-level egress controls on Umbrel are optional,
  but do not block the node's other required Bitcoin and app traffic merely to
  restrict this one container.
- Keep an offline or separate-machine copy of this repository. Umbrel's
  `app-data` path is persistent across normal updates, but a disk failure,
  factory reset, or manual deletion is still destructive.

## Test the code

```sh
python3 -m unittest discover -s tests -v
```

## References

- [Coinkite BLOCKCLOCK mini Push API](https://blockclockmini.com/api.html)
- [Coinkite BLOCKCLOCK mini documentation](https://blockclockmini.com/docs.html)
- [Coinbase spot-price API](https://docs.cdp.coinbase.com/coinbase-business/track-apis/prices)
- [UniFi network and client isolation](https://help.ui.com/hc/en-us/articles/18965560820247-Implementing-Network-and-Client-Isolation-in-UniFi)
- [UniFi virtual networks and VLANs](https://help.ui.com/hc/en-us/articles/9761080275607-Creating-Virtual-Networks-VLANs)
- [UniFi zone-based firewall](https://help.ui.com/hc/en-us/articles/115003173168-Zone-Based-Firewalls-in-UniFi)
