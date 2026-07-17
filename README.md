# DotX Solar Forecast API - Quickstart

A minimal, self-contained example of the DotX Solar Forecast API. The goal of
this repository is one thing: let your engineers go from zero to a working
end-to-end call in under 5 minutes.

The example is written in Python using only `requests` and the standard
library. There is no DotX SDK - every call is a raw HTTP request that
translates 1:1 to whatever stack you use internally.

## Contents

| File | What it is |
|---|---|
| `quickstart.py` | One-file end-to-end demo: register → calibrate → forecast. Copy-paste runnable. |
| `walkthrough.ipynb` | Jupyter notebook with the same flow, plus inline plots and step-by-step explanation. |
| `.env.example` | Template for your API key, API base URL, and measurements CSV path. |
| `requirements.txt` | Python dependencies. |

## Prerequisites

- Python 3.10 or higher
- An internet connection
- Your DotX tenant API key
- A CSV of historical cumulative-energy readings from your plant (see schema below)

## Installation

```powershell
git clone <this-repo>
cd solar-forecast-api-quickstart

python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/Mac: source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Configuration

```powershell
copy .env.example .env
notepad .env
```

Fill in:

```
EMS_API_KEY=<your-tenant-key>
EMS_API_BASE=<api-base-url-from-your-DotX-contact>
```

`.env` is already in `.gitignore` - never commit it.

The path to your measurements CSV lives in the **CONFIGURATION** block at
the top of `quickstart.py` (and in the first code cell of the notebook) -
see *Plant and asset configuration* below. It's a per-run knob, not a
secret, so it sits with `PLANT_NAME`, `ASSET_NAME`, etc.

### Measurements CSV schema

Point `CSV_PATH` (in the CONFIGURATION block, see below) at a CSV of
historical cumulative-energy readings from your plant. Use this exact
header and at least 97 data rows (one full day at 15-minute resolution;
ideally one full year for accuracy). The window must end **at least ~2
days in the past**: the weather data the calibration runs on becomes
available with a ~2-day delay, so newer readings are trimmed from the
fit — a CSV containing only the last day or two fails with an
"insufficient data" error even if it has 97+ rows. (Forecasts are not
affected by this delay.)

```
timestamp,solar
2025-05-01 12:00:00,89797620
2025-05-01 12:15:00,89840350
2025-05-01 12:30:00,89884245
...
```

(An optional third column `reduction` covers curtailed plants - see
*Optional: curtailment* below.)

- `timestamp` - ISO 8601 datetime. Timezone-naive timestamps are treated as
  UTC; if your data is in local time, add a UTC offset (`...+02:00`) or
  trailing `Z` so the conversion is unambiguous.
- `solar` - your inverter's cumulative energy counter in **watt-hours**
  (Wh) - the lifetime-yield number, not instantaneous power. This is the
  raw export from SMA, SolarEdge, Huawei, Fronius, etc.: a monotonically
  increasing counter. The API differentiates consecutive samples to recover
  instantaneous power, so we need one extra reading to produce N power
  values from N+1 cumulative samples (hence ≥97 rows for a full day).

### Optional: curtailment (`reduction` column)

If your plant curtails its inverter - for example to stay under a feed-in
limit - add a third column `reduction`: the curtailment level active during
the reading's interval, as a **percentage of nominal AC power available**
(100 = no curtailment, 50 = inverter capped at half its rating):

```
timestamp,solar,reduction
2025-05-01 12:00:00,89797620,100
2025-05-01 12:15:00,89840350,71.73
2025-05-01 12:30:00,89884245,71.73
...
```

Why it matters: during a curtailed interval the meter records less energy
than the panels could have produced. Without the `reduction` column the
calibration has no way to tell curtailment from weak panels or a wrong
orientation, and the fitted model ends up biased low. With it, the
calibration caps the modeled output per interval exactly like your inverter
did, so every sample stays usable.

Notes:

- The scale is percent (0-100). If your system reports basis points
  (0-10000, e.g. `7173` for 71.73%), divide by 100 before sending - the API
  rejects values above 100 with a hint.
- Omit the column (or the JSON field) entirely if you never curtail; it
  defaults to 100.
- **Forecasts are not affected.** The forecast always predicts the
  uncurtailed output at nominal AC power - `reduction` only improves the
  calibration.

## Plant and asset configuration

Open `quickstart.py` and edit the **CONFIGURATION** block at the top:

```python
CSV_PATH      = r"C:\path\to\measurements.csv"   # CSV: timestamp,solar (cumulative Wh, 15-min, ≥97 rows)

PLANT_NAME    = "My Plant (edit me)"
LATITUDE      = 52.0
LONGITUDE     = 4.0
TIMEZONE      = "Europe/Amsterdam"
EXTERNAL_REF  = "quickstart-demo-001"

ASSET_NAME       = "Solar asset (edit me)"
INVERTER_AC_KW   = 200.0
EFFICIENCY       = 0.9
TEMP_COEFF       = -0.0029   # power loss per °C above 25 °C cell temp [1/°C]
DC_KWP           = None    # None -> computed from SUB_ARRAYS, or your datasheet total
SUB_ARRAYS       = [
    {"name": "main", "kwp": None, "tilt": None, "azimuth": None},
]
```

- `CSV_PATH` is the path to your cumulative-energy CSV (see schema above).
  Use a raw string (`r"..."`) on Windows so backslashes survive.
- `LATITUDE` / `LONGITUDE` must match the physical location of your plant.
- `EXTERNAL_REF` is a stable id from your own systems (e.g. CRM customer id).
- `SUB_ARRAYS` - use one sub-array per roof face if your plant has multiple
  orientations. Each angle (`tilt`, `azimuth`) has **three states**:
  - omitted or `null` - determined entirely from your measurements;
  - a value - a **starting estimate** for the fit; the calibration refines
    it from your measurements;
  - a value plus `"fix_tilt": true` / `"fix_azimuth": true` - **locked**:
    the angle is excluded from the fit and kept exactly as registered.
    A `fix_*` flag without the corresponding value is rejected.

  Example for a roof you have physically measured:

  ```python
  {"name": "main", "kwp": None, "tilt": 12.0, "azimuth": 186.0,
   "fix_tilt": True, "fix_azimuth": True}
  ```

  `kwp` works differently: it is **never pinned**. A supplied `kwp` sets
  the capacity ratio between sub-arrays, while the calibration determines
  the total from the energy in your measurements. The fit result reports
  per angle whether it was `fixed` or `fitted` (`tilt_source` /
  `azimuth_source`), so you can verify a lock was honoured.
- **Azimuth convention**: a compass bearing in degrees - **0° = north,
  90° = east, 180° = south, 270° = west**. A south-west roof face is
  ~220°, not -40° or +40°. Values are **not** converted from other
  conventions (e.g. 0° = south): the fit searches the full circle
  (0°-360°), so a wrong-convention value like -90 for a west roof is
  clamped to 0° (north) and degrades the fit instead of failing loudly.
  Tilt is degrees from horizontal (0° = flat, 90° = vertical facade).
  When unsure, prefer `null` over a guess in the wrong convention - and
  never combine a `fix_*` lock with an unverified convention.
- `INVERTER_AC_KW` is the AC capacity of your inverter; forecasts are
  clipped at this value.
- `TEMP_COEFF` is the panel power temperature coefficient in 1/°C - the
  fractional power loss per °C of cell temperature above 25 °C. Typical
  crystalline-silicon panels are around -0.0029 to -0.004; check your panel
  datasheet. The API defaults to -0.0029 if omitted.

The same configuration lives in the first code cell of the notebook.

## Run the end-to-end script

```powershell
python quickstart.py
```

You'll see five steps print to your terminal:

1. **Register a plant.**
2. **Register a solar asset** under the plant.
3. **Calibrate the asset model** by uploading your cumulative-energy CSV.
   Calibration is asynchronous and returns a task handle immediately. If the
   CSV carries a `reduction` column, the response reports how many curtailed
   intervals were detected (`rows_reduced`).
4. **Poll until calibration completes.** Once `is_calibrated` flips to
   `true`, R² and RMSE are printed, plus the fitted parameters per
   sub-array - each angle tagged with its provenance (`fixed` when you
   locked it with `fix_*`, `fitted` otherwise).
5. **Submit recent measurements and get a forecast.** The `/forecast/`
   endpoint takes the **same** cumulative-Wh format as `/fit/` (`time` +
   `solar`, the lifetime-yield counter in Wh) — one unit across the whole
   API. Send a short rolling window of your most recent readings (at least
   two); the server differentiates them internally. The forecast is a
   **rolling 48-hour window** (192 quarter-hour samples) starting at the
   current 15-minute interval, so a full local *tomorrow* is always available
   in any EU timezone. Forecast horizon, `performance_ratio`, and the first 8
   timesteps are printed.

All calls authenticate with the same `X-API-Key` header carrying your tenant
key. There is no separate key handshake or login flow.

## Run the notebook

```powershell
jupyter notebook walkthrough.ipynb
```

The notebook walks the same flow as the script, with markdown commentary and
two inline matplotlib plots: measured vs. modeled (over the calibration
period) and the forecast curve.

## API reference

Full request/response schemas and an interactive "Try it out" UI:

  `<EMS_API_BASE>/docs/`

Swagger documents the static shape of every endpoint. This repository
documents the **flow** through them - the bits Swagger can't carry
(polling patterns, minimum-data constraints, how to read the
goodness-of-fit).

## Security notes

- Your tenant API key is the master credential for your account. Treat it
  like a database password.
- Never commit `.env` or any file containing a real key. The `.gitignore`
  in this repo blocks `.env` by default.
- If a key is exposed, contact us via your normal support channel to rotate
  it.

## Support

Reach out via the channel you agreed on during onboarding.
