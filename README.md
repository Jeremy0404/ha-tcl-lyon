# TCL Lyon — Home Assistant Integration

Custom integration for the **TCL** (Transports en Commun Lyonnais) public transport network in Lyon, France.

Trigger Home Assistant automations N minutes before your tram/bus arrives at a stop, get notified when your line is disrupted, and surface other useful info from the public data.grandlyon.com API.

> **Status:** v0.4 — UI setup with stop/line search and live "next passage" sensors. Disruption binary sensors are next (v0.5). See [docs/01-plan.md](docs/01-plan.md) for the roadmap.

## Requirements

- Home Assistant **2024.10.0** or later.
- A **GrandLyon Connect** account (free): https://moncompte.grandlyon.com/login/
- A **data password** for data.grandlyon.com, set via the forgot-password flow:
  1. Log out of data.grandlyon.com.
  2. Go to https://data.grandlyon.com/portail/fr/mot-de-passe-oublie
  3. Use it to **define** your data password — this works for first-time setup even if you haven't actually forgotten anything. This password is distinct from your SSO password.

> ⚠️ The data password is **not** the same as your GrandLyon Connect SSO password. This is the most common cause of 401 errors during setup.

## Installation

### Via HACS (recommended once published)

1. HACS → Integrations → ⋮ → Custom repositories → add this repo URL as an **Integration**.
2. Install "TCL Lyon".
3. Restart Home Assistant.
4. Settings → Devices & Services → Add Integration → "TCL Lyon".

### Manual

Copy `custom_components/tcl_lyon/` into your HA config's `custom_components/` directory. Restart HA.

## Configuration

All configuration is done through the UI. The setup flow will:

1. Validate your data.grandlyon.com credentials, then download the GTFS catalogue.
2. Let you search for a stop by name (e.g. "Bellecour") and pick it from the matches.
3. Let you search and multi-select the lines you want to follow there.
4. Offer to add another stop, then finish.

To change your stops or lines later, remove and re-add the integration (an in-place
editor is planned). If your data password stops working, Home Assistant prompts you
to re-enter it without losing your stops.

## Entities

One sensor is created per (stop, line) you follow:

- **`sensor.<line>_<stop>`** (e.g. `sensor.t2_bron_hotel_de_ville`) — whole minutes
  until the next passage of that line at that stop. `unavailable` while the API is
  down; empty when no passage is currently known.
  - Attribute `next_departures`: upcoming passes with aimed/expected times, a
    realtime flag, a cancellation flag, the direction/destination, and minutes-to-go.

> Both directions of a line at a stop share one sensor for now — use the
> `direction`/`destination` fields in `next_departures` to tell them apart.

Disruption sensors (**`binary_sensor.tcl_line_<line>_disrupted`**) are coming in v0.5.

## Automation example

Notify 15 minutes before the next T1 tram at Bellecour:

```yaml
automation:
  - alias: "Bellecour T1 — leave soon"
    trigger:
      - platform: numeric_state
        entity_id: sensor.t1_bellecour
        below: 16
        above: 14
    action:
      - service: notify.mobile_app_my_phone
        data:
          message: "T1 in ~15 min — head out!"
```

## Development

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

### Live testing on a real HA instance

`scripts/deploy.ps1` mirrors `custom_components/tcl_lyon/` onto your Home Assistant
config share (defaults to `\\192.168.1.177\config\`; override with `$env:HA_CONFIG_SHARE`):

```powershell
pwsh scripts/deploy.ps1            # one-shot mirror, then restart HA yourself
pwsh scripts/deploy.ps1 -Watch     # re-mirror on every save
pwsh scripts/deploy.ps1 -Restart   # also restart HA via the API (needs HA_TOKEN in .env)
```

Python caches imported modules, so changes only take effect after an **HA restart**
(Developer Tools → YAML → Restart) — a browser refresh is not enough.

Internal docs and discovery notes live in `docs/` (gitignored — local working notes).

## License

MIT. See [LICENSE](LICENSE).
