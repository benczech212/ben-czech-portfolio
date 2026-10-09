# Deploying to the booth Pi

Target: Raspberry Pi 5 (booth) running Raspberry Pi OS Lite 64-bit (Bookworm). Display Pis and bridge Pi Zero 2 Ws only need the client bits.

## Network plan

| Thing | Value |
|---|---|
| Camp SSID | `<camp-ssid>` (WPA2; the password is kept out of this repo) |
| Router | at the booth; DHCP pool `10.42.0.100-250` |
| Booth Pi | **static `10.42.0.1`** + mDNS `campbus.local` |
| Display Pis | static `10.42.0.11+` (`display_entrance_left` = .11, ...) |
| Bridges | static `10.42.0.21+` |
| ESP32s | DHCP; they only ever talk to `10.42.0.1` |

ESP32 mDNS is unreliable, so microcontrollers hardcode the booth IP. That is the one number that must never change.

## Broker (booth Pi)

```
sudo apt install -y mosquitto mosquitto-clients avahi-daemon
sudo cp bus/mosquitto.conf /etc/mosquitto/conf.d/campbus.conf
sudo sed -i 's#/mosquitto/data/#/var/lib/mosquitto/#' /etc/mosquitto/conf.d/campbus.conf
sudo cp bus/acl.example /etc/mosquitto/campbus.acl          # edit the device accounts
sudo mosquitto_passwd -c /etc/mosquitto/campbus.passwd engine
sudo mosquitto_passwd    /etc/mosquitto/campbus.passwd booth   # + audio, bridge, one per device node_id
sudo chown mosquitto: /etc/mosquitto/campbus.passwd /etc/mosquitto/campbus.acl
sudo chmod 600 /etc/mosquitto/campbus.passwd
sudo systemctl enable --now mosquitto
mosquitto_sub -h localhost -u booth -P '<password>' -t '#' -v      # sanity check
```

The booth profile (`bus/mosquitto.conf`) refuses anonymous clients and applies the ACL: only `engine` and `booth`
may publish `cmd/#` and `mapping/set`; device accounts (username = `node_id`) may publish only their own
`node/<id>/#`, `map/<id>`, `prop/<id>`, `toy/<id>` and `instrument/<id>`. Python services read
`CAMPBUS_USER` / `CAMPBUS_PASSWORD` from their environment (put them in a systemd `EnvironmentFile=`, not in the unit).
The docker-compose virtual camp uses the anonymous `bus/mosquitto.dev.conf` instead.

Note: the stock `/etc/mosquitto/mosquitto.conf` already sets `persistence_location` and `log_dest`; if mosquitto complains about duplicates, delete those two lines from `campbus.conf`.

## Python services (booth Pi)

```
sudo apt install -y python3-venv
sudo mkdir -p /opt/campbus && sudo chown $USER /opt/campbus
git clone <repo> /opt/campbus/src        # or rsync apps/camp-bus/ to /opt/campbus/src
python3 -m venv /opt/campbus/venv
/opt/campbus/venv/bin/pip install -e /opt/campbus/src/clients/python
sudo cp deploy/systemd/campbus-fake-beat.service /etc/systemd/system/
sudo systemctl enable --now campbus-fake-beat
```

Every service is a systemd unit with `Restart=always` so it comes back after a power cut. Template in `systemd/`. Add one unit per service (check-in kiosk, analysis, mapping engine, Resolume adapter) as they land.

## Time

The booth Pi runs `chrony` as the LAN time source (`allow 10.42.0.0/24` in `/etc/chrony/chrony.conf`) so Pi-class nodes agree on `ts`. No internet needed; the booth Pi's RTC (Pi 5 has one, add the battery) holds time across power cuts.

## Checklist before leaving for the burn

- [ ] Every node boots headless to a working state with no keyboard/monitor
- [ ] Pull the power on the booth Pi mid-set; everything recovers by itself
- [ ] Pull the power on a display Pi; it comes back showing the current DJ
- [ ] Broker down: kiosk, displays, and toys keep working locally
- [ ] Spare SD cards imaged for booth + one display + one bridge
