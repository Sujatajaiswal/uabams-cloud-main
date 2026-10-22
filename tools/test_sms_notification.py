"""
UABAMS SMS Notification Test
==============================
Sends TWO simulated alerts to the live server:
  1. CRITICAL (RED)  — peakValueG=85.0g  →  triggers RED alert + SMS
  2. WARNING (YELLOW) — peakValueG=60.0g  →  triggers YELLOW alert + SMS

Pre-conditions (already met in your setup):
  - Route  : SMS1 (id=1, vertical_limit=50g, lateral_limit=80g)
  - Contact: Test Official1  (+919964160926)  sms_enabled=True
  - Train  : 21304

Run:
    python3 tools/test_sms_notification.py

After running, check SMS on +919964160926 and check docker logs:
    docker logs uabams_app --tail 60 | grep -E "SMS|ALERT"
"""

import json
import time
import urllib.request
import urllib.error

# ── Configuration ──────────────────────────────────────────────────────────────
BASE_URL   = "http://20.8.1.15:8000"
TRAIN_NO   = "21304"
ROUTE_ID   = 1          # SMS1
GATEWAY_ID = "GW_UABAMS_BOGIE_01"
API_KEY    = "local-gw1-key"
# ──────────────────────────────────────────────────────────────────────────────

def http(method, url, payload=None, extra_headers=None):
    headers = {"Content-Type": "application/json", **(extra_headers or {})}
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {body}")

def print_divider(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def send_alert(label, peak_g, threshold_g, axis, location_km):
    ts_ms = int(time.time() * 1000)
    payload = {
        "gatewayId": GATEWAY_ID,
        "logicalGatewayId": f"GW_{TRAIN_NO}_BOGIE_01",
        "trainNo": TRAIN_NO,
        "sessionName": f"SMS_TEST_{label}_{ts_ms}",
        "timestampUtcMs": ts_ms,
        "startKm": location_km,
        "endKm":   location_km + 0.05,
        "speedKmph":    115.0,
        "minSpeedKmph": 112.0,
        "maxSpeedKmph": 118.0,
        "alertsCount": 1,
        "latitude":  28.6426,   # New Delhi (on route)
        "longitude": 77.2198,
        "alerts": [
            {
                "sensor":     "BOGIE",
                "axis":       axis,
                "channel":    f"BG_{axis}",
                "peakValueG": peak_g,
                "thresholdG": threshold_g,
                "speedKmph":  115.0,
                "locationKm": location_km + 0.02,
                "latitude":   17.3850,
                "longitude":  78.4867
            }
        ]
    }
    gw_headers = {
        "X-Api-Key":    API_KEY,
        "X-Gateway-Id": GATEWAY_ID,
    }
    return http("POST", f"{BASE_URL}/api/v1/alert", payload, gw_headers)


# ── Step 1: Assign Train 21304 to Route SMS1 ──────────────────────────────────
print_divider("STEP 1 — Assigning Train 21304 → Route SMS1")
try:
    res = http("PUT", f"{BASE_URL}/api/v1/trains/{TRAIN_NO}/route",
               {"route_id": ROUTE_ID})
    print(f"  OK  Train {TRAIN_NO} assigned to route_id={ROUTE_ID}")
    print(f"      Response: {res}")
except RuntimeError as e:
    print(f"  WARN: {e}")
    print("      Continuing anyway...")

# ── Step 2: CRITICAL (RED) Alert ─────────────────────────────────────────────
print_divider("STEP 2 — Sending CRITICAL (RED) Alert")
print(f"  Train : {TRAIN_NO}")
print(f"  Axis  : Z (Vertical)")
print(f"  Peak  : 85.0g  (>80g  → RED / CRITICAL)")
print(f"  Limit : 50.0g  (route vertical_limit)")
try:
    res = send_alert("CRITICAL", peak_g=85.0, threshold_g=50.0, axis="Z", location_km=120.0)
    color    = res.get("color", "?")
    severity = res.get("severity", "?")
    alert_id = res.get("alertId", "?")
    print(f"\n  RESULT → color={color}  severity={severity}  alertId={alert_id}")
    if color == "RED":
        print("  [SMS] Should be dispatched to +919964160926 now")
    else:
        print(f"  [WARN] Unexpected color={color}. Check server logs.")
except RuntimeError as e:
    print(f"  ERROR: {e}")

# ── Wait before next alert (use different km to skip 50m dedup) ───────────────
print("\n  Waiting 3 seconds before WARNING alert...")
time.sleep(3)

# ── Step 3: WARNING (YELLOW) Alert ───────────────────────────────────────────
print_divider("STEP 3 — Sending WARNING (YELLOW) Alert")
print(f"  Train : {TRAIN_NO}")
print(f"  Axis  : Y (Lateral)")
print(f"  Peak  : 60.0g  (>50g threshold → YELLOW / WARNING)")
print(f"  Limit : 80.0g  (route lateral_limit)")
try:
    res = send_alert("WARNING", peak_g=60.0, threshold_g=80.0, axis="Y", location_km=121.0)
    color    = res.get("color", "?")
    severity = res.get("severity", "?")
    alert_id = res.get("alertId", "?")
    print(f"\n  RESULT → color={color}  severity={severity}  alertId={alert_id}")
    if color in ("RED", "YELLOW"):
        print("  [SMS] Should be dispatched to +919964160926 now")
    else:
        print(f"  [WARN] Unexpected color={color}. Check server logs.")
except RuntimeError as e:
    print(f"  ERROR: {e}")

# ── Done ──────────────────────────────────────────────────────────────────────
print_divider("DONE — Verify SMS & Logs")
print("""
  Check phone +919964160926 for 2 SMS messages:
    SMS 1: UABAMS CRITICAL ALERT!  Peak: 85.00g  Fault: Vertical
    SMS 2: UABAMS WARNING ALERT!   Peak: 60.00g  Fault: Lateral

  Check Docker logs:
    docker logs uabams_app --tail 60 | grep -E "SMS|CRITICAL|WARNING"

  Check UI:
    http://20.8.1.15:8000/dashboard  → Alerts tab
    You should see RED and YELLOW rows for Train 21304
""")
