"""
quickstart.py - end-to-end example of the DotX Solar Forecast API.

Demonstrates the full lifecycle in raw HTTP:

    1. Register a plant
    2. List plants (GET /plants/) and confirm the new one is there
    3. Register a solar asset
    4. Calibrate the asset model with measurements from your CSV (async)
    5. Poll GET /fit/?task_id=... until the task completes; print R² / RMSE
    6. Submit recent measurements and retrieve a forecast

All calls use one credential: your tenant X-API-Key.

Run:
    python quickstart.py

Requires the following in a local `.env` file:
    EMS_API_KEY    your tenant X-API-Key
    EMS_API_BASE   the API base URL (your DotX contact will provide it)

The CSV path is set in the CONFIGURATION block below (CSV_PATH) - per-run
knobs like that one belong with PLANT_NAME / ASSET_NAME / ..., not in .env.

See README.md for full details; Swagger is available at <EMS_API_BASE>/docs/.
"""

import csv
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv


# --- Configuration ----------------------------------------------------------
# Edit CSV_PATH + the PLANT_* / ASSET_* values below to match your plant
# before running. Lat/lon especially matter - the autofit uses them to
# compute sun position.

CSV_PATH      = r"C:\path\to\measurements.csv"   # CSV: timestamp,solar (cumulative Wh, 15-min, ≥97 rows)

PLANT_NAME    = "My Plant (edit me)"
LATITUDE      = 52.0
LONGITUDE     = 4.0
TIMEZONE      = "Europe/Amsterdam"

ASSET_NAME       = "Solar asset (edit me)"
INVERTER_AC_KW   = 100.0
EFFICIENCY       = 0.90
TEMP_COEFF       = -0.0029   # power loss per °C above 25 °C cell temp [1/°C]; typical c-Si ≈ -0.0029
DC_KWP           = None    # None -> computed from SUB_ARRAYS, or pin to a number
SUB_ARRAYS       = [
    # Each parameter can be:  None -> autofit determines it, or a value -> pinned.
    # Use multiple sub-arrays if your plant has multiple roof faces with
    # different orientations.
    # Azimuth is a compass bearing: 0=north, 90=east, 180=south, 270=west
    # (NOT 0=south; values are never converted). Tilt is degrees from horizontal.
    {"name": "main", "kwp": None, "tilt": None, "azimuth": None},
]

# ---------------------------------------------------------------------------

load_dotenv()

API_BASE = os.environ.get("EMS_API_BASE", "").rstrip("/")
API_KEY  = os.environ.get("EMS_API_KEY")

if not API_KEY:
    sys.exit("ERROR: EMS_API_KEY is not set. Copy .env.example to .env and fill it in.")
if not API_BASE:
    sys.exit("ERROR: EMS_API_BASE is not set. Get the URL from your DotX contact and put it in .env.")
if not os.path.exists(CSV_PATH):
    sys.exit(f"ERROR: CSV file not found: {CSV_PATH}\n"
             f"       Edit CSV_PATH in the CONFIGURATION block at the top of this script.")

headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}


def load_measurements(path):
    """Read a CSV with columns 'timestamp,solar[,reduction]' into the API's JSON shape.

    `solar` is the inverter's cumulative energy counter in Wh (a monotonic
    lifetime-yield reading), not instantaneous power - the server
    differentiates consecutive samples to recover power. Timestamps may
    include or omit a timezone suffix; naive timestamps are treated as
    UTC. Replace this with your own loader if your CSV differs.

    `reduction` is optional: the curtailment level active during the
    reading's interval, as a percentage of nominal AC power available
    (100 = no curtailment, 50 = inverter capped at half its rating). Include
    it if your plant curtails (e.g. to stay under a feed-in limit) - the
    calibration then accounts for capped intervals instead of mistaking them
    for underperforming panels. Omit the column if you never curtail.
    """
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"].replace(" ", "T").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            m = {
                "time":  ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "solar": int(row["solar"]),
            }
            if row.get("reduction") not in (None, ""):
                m["reduction"] = float(row["reduction"])
            out.append(m)
    return out


# --- Step 1: register a plant ----------------------------------------------

print("Step 1 - register a plant")

plant_payload = {
    "name":      PLANT_NAME,
    "latitude":  LATITUDE,
    "longitude": LONGITUDE,
    "timezone":  TIMEZONE,
}

response = requests.post(f"{API_BASE}/plants/", headers=headers, json=plant_payload, timeout=60)
if response.status_code == 409:
    sys.exit(
        f"  ! Plant with name '{PLANT_NAME}' already exists in this tenant. "
        f"Either change PLANT_NAME at the top of the script to create a new plant, "
        f"or comment out Step 1 - the existing plant is listed in Step 2."
    )
response.raise_for_status()
plant    = response.json()
plant_id = plant["plant_id"]
print(f"  -> Created plant {plant_id}.")


# --- Step 2: list plants and confirm the new one is there -----------------

print("\nStep 2 - list plants")

response = requests.get(f"{API_BASE}/plants/", headers=headers, timeout=60)
response.raise_for_status()
body = response.json()
# Endpoint returns either a bare list or a wrapper like {"items": [...]}.
if isinstance(body, list):
    plants = body
else:
    plants = next((body[k] for k in ("items", "plants", "results", "data") if k in body), [])
print(f"  -> Tenant has {len(plants)} plant(s).")
for p in plants[:5]:
    marker = " <-- just created" if p["plant_id"] == plant_id else ""
    print(f"     plant_id={p['plant_id']}  name={p['name']!r}  external_ref={p.get('external_ref')!r}{marker}")
if len(plants) > 5:
    print(f"     ... and {len(plants) - 5} more")


# --- Step 3: register a solar asset under the plant ------------------------

print("\nStep 3 - register a solar asset")

asset_payload = {
    "name":           ASSET_NAME,
    "inverter_ac_kw": INVERTER_AC_KW,
    "efficiency":     EFFICIENCY,
    "temp_coeff":     TEMP_COEFF,
    "sub_arrays":     SUB_ARRAYS,
}
if DC_KWP is not None:
    asset_payload["dc_kwp"] = DC_KWP

response = requests.post(
    f"{API_BASE}/plants/{plant_id}/assets/",
    headers=headers, json=asset_payload, timeout=60,
)
response.raise_for_status()
asset    = response.json()
asset_id = asset["asset_id"]
print(f"  -> Asset {asset_id} registered.")


# --- Step 4: calibrate the asset model -------------------------------------

print(f"\nStep 4 - calibrate the asset model using {CSV_PATH}")

measurements = load_measurements(CSV_PATH)
if len(measurements) < 97:
    sys.exit(f"  ! CSV has {len(measurements)} rows; autofit needs at least 97 cumulative samples.")
print(f"  Loaded {len(measurements)} measurements "
      f"({measurements[0]['time']} -> {measurements[-1]['time']}).")

fit_response = requests.post(
    f"{API_BASE}/plants/{plant_id}/assets/{asset_id}/fit/",
    headers=headers, json={"measurements": measurements}, timeout=60,
)
fit_response.raise_for_status()
task    = fit_response.json()
task_id = task['task_id']
print(f"  -> Fit task dispatched: task_id={task_id} status={task['status']}.")
if task.get("rows_reduced"):
    print(f"  -> {task['rows_reduced']} curtailed interval(s) detected (reduction < 100); "
          f"the calibration caps the model accordingly.")


# --- Step 5: poll GET /fit/?task_id=... until the task completes -----------

print("\nStep 5 - poll the autofit task")

POLL_TIMEOUT_SECONDS  = 600
POLL_INTERVAL_SECONDS = 5
poll_url   = f"{API_BASE}/plants/{plant_id}/assets/{asset_id}/fit/"
poll_start = time.monotonic()

while True:
    elapsed = time.monotonic() - poll_start
    if elapsed > POLL_TIMEOUT_SECONDS:
        sys.exit(f"  ! Timeout after {POLL_TIMEOUT_SECONDS:.0f}s - calibration did not complete.")

    response = requests.get(poll_url, headers=headers,
                            params={"task_id": task_id}, timeout=60)
    if response.status_code == 200:
        result = response.json()
        print(f"  -> Calibrated after {elapsed:.0f}s.")
        break
    if response.status_code != 202:
        response.raise_for_status()

    print(f"  ... task still running (elapsed {elapsed:.0f}s)")
    time.sleep(POLL_INTERVAL_SECONDS)

fitted_params = result["fitted_params"]
r2   = fitted_params["r2"]
rmse = fitted_params["rmse"]
print(f"  -> Goodness of fit:    R² = {r2:.3f}, RMSE = {rmse:.2f} kW")
print(f"  -> Fitted DC capacity: {fitted_params['fitted_kwp']:.2f} kWp")
for sa in fitted_params["sub_arrays"]:
    print(f"     {sa['name']}: "
          f"kwp = {sa['kwp']:.2f} kWp, "
          f"tilt = {sa['tilt_deg']:.1f}°, "
          f"azimuth = {sa['azimuth_deg']:.1f}°")


# --- Step 6: submit recent measurements and retrieve the forecast ----------

print("\nStep 6 - submit recent measurements + retrieve the forecast")

# /forecast/ takes the SAME cumulative-Wh readings as /fit/ (`time` + `solar`,
# the lifetime-yield counter in Wh) - there is one unit across the whole API.
# Send a short rolling window of your most recent counter readings (at least
# two); the server differentiates them to power internally. Here we just reuse
# the tail of the calibration series as a stand-in for "live" readings.
recent_measurements = measurements[-3:]

response = requests.post(
    f"{API_BASE}/plants/{plant_id}/assets/{asset_id}/forecast/",
    headers=headers, json={"measurements": recent_measurements}, timeout=60,
)
response.raise_for_status()
forecast = response.json()

ingest = forecast.get("ingest", {})
print(f"  -> Readings ingested  : {ingest.get('rows_received', len(recent_measurements))} "
      f"(dropped_negative={ingest.get('rows_dropped_negative', 0)}, "
      f"clipped={ingest.get('rows_clipped_above_capacity', 0)})")
print(f"  -> Performance ratio  : {forecast['performance_ratio']:.3f}")
print(f"  -> Horizon            : {forecast['horizon_hours']} hours, "
      f"{len(forecast['forecast_data'])} samples")
print("  -> First 8 forecast samples:")
for sample in forecast["forecast_data"][:8]:
    print(f"     {sample['time']}  power_kw={sample['power_kw']:.2f}")


print("\nDone. The plant exists, the model is calibrated, and the forecast is fresh.")
