# TCL Lyon — Home Assistant Integration

Custom integration for the **TCL** (Transports en Commun Lyonnais) public transport network in Lyon, France.

Trigger Home Assistant automations N minutes before your tram/bus arrives at a stop, get notified when your line is disrupted, and surface other useful info from the public data.grandlyon.com API.

> **Status:** v0.1 — scaffold only. No working sensors yet. See [docs/01-plan.md](docs/01-plan.md) for the roadmap.

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

All configuration is done through the UI. The integration will:

1. Validate your data.grandlyon.com credentials.
2. Let you search for stops by name (e.g. "Bellecour").
3. Let you pick the lines you want to follow at each stop.

## Entities

- **`sensor.tcl_<stop>_<line>_<direction>`** — minutes until the next passage. `state = "unavailable"` if the API is down.
  - Attribute `next_departures`: full list of upcoming passes.
- **`binary_sensor.tcl_line_<line>_disrupted`** — `on` if the line has an active disruption.
  - Attribute `disruptions`: list of active situations with description and validity period.

## Automation example

Notify 15 minutes before the next T1 tram at Bellecour:

```yaml
automation:
  - alias: "Bellecour T1 — leave soon"
    trigger:
      - platform: numeric_state
        entity_id: sensor.tcl_bellecour_t1_outbound
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
