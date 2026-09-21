import argparse
import json
import time
import urllib.request
import urllib.error

BASE_HEADERS = {"Content-Type": "application/json"}

def http(method, url, payload=None, extra_headers=None):
    headers = {**BASE_HEADERS, **(extra_headers or {})}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code} {method} {url}: {body}")

def setup_and_alert(base_url, train_no, peak_g, api_key, limit_g, contact_name, contact_phone):
    gw_headers = {"X-Api-Key": api_key}

    # ── Step 1: Create a route ──────────────────────────────────────────────────
    print(f"\n[1/4] Creating route 'SMS-Test-Route' with limit {limit_g}g ...")
    try:
        res = http("POST", f"{base_url}/api/v1/routes",
                   {"name": "SMS-Test-Route", "vertical_limit": limit_g, "lateral_limit": limit_g})
        route_id = res["route"]["id"]
        print(f"      Route created: id={route_id}")
    except RuntimeError as e:
        # Route may already exist from a previous run — fetch it
        print(f"      (Route may already exist — fetching list)")
        routes = http("GET", f"{base_url}/api/v1/routes")["routes"]
        match = next((r for r in routes if r["name"] == "SMS-Test-Route"), None)
        if not match:
            print(f"      ERROR: {e}")
            return
        route_id = match["id"]
        print(f"      Using existing route id={route_id}")

    # ── Step 2: Add a contact to that route ────────────────────────────────────
    print(f"[2/4] Adding contact '{contact_name}' ({contact_phone}) to route ...")
    try:
        http("POST", f"{base_url}/api/v1/contacts",
             {"name": contact_name, "mobile_number": contact_phone, "route_id": route_id, "sms_enabled": True, "active": True})
        print(f"      Contact added.")
    except RuntimeError as e:
        print(f"      (Contact may already exist, continuing) {e}")

    # ── Step 3: Assign the train to this route ─────────────────────────────────
    print(f"[3/4] Assigning train '{train_no}' to route id={route_id} ...")
    try:
        res = http("PUT", f"{base_url}/api/v1/trains/{train_no}/route",
                   {"route_id": route_id})
        print(f"      Assigned: {res}")
    except RuntimeError as e:
        print(f"      ERROR: {e}")
        return

    # ── Step 4: Fire an alert that exceeds the route limit ─────────────────────
    print(f"[4/4] Sending alert for Train {train_no} with peak {peak_g}g (limit={limit_g}g) ...")
    payload = {
        "gatewayId": "GW_UABAMS_BOGIE_01",
        "logicalGatewayId": f"GW_{train_no}_BOGIE_01",
        "trainNo": train_no,
        "sessionName": f"SESSION_{train_no}_SMSTEST",
        "timestampUtcMs": int(time.time() * 1000),
        "startKm": 10.0, "endKm": 10.05,
        "speedKmph": 120.5, "minSpeedKmph": 119.0, "maxSpeedKmph": 121.0,
        "alertsCount": 1,
        "alerts": [{
            "sensor": "BOGIE", "axis": "Z", "channel": "BG_Z",
            "peakValueG": peak_g,
            "thresholdG": limit_g,
            "speedKmph": 120.5,
            "locationKm": 10.02,
            "latitude": 12.9716,
            "longitude": 77.5946
        }]
    }
    res = http("POST", f"{base_url}/api/v1/alert", payload,
               extra_headers={**gw_headers, "X-Gateway-Id": "GW_UABAMS_BOGIE_01"})
    print(f"      Alert response: {res.get('status')} / {res.get('alert')}")

    print(f"\n✅ Done! Now check the container logs:")
    print(f"   docker logs uabams_app --tail 40")
    print(f"   Look for: [SMS SENT TO {contact_phone}]: ...")

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Full SMS alert simulation test")
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--train", default="22151")
    p.add_argument("--peak", type=float, default=9.5, help="Peak g to send (should exceed --limit)")
    p.add_argument("--limit", type=float, default=8.0, help="Route threshold to configure")
    p.add_argument("--api-key", default="local-gw1-key")
    p.add_argument("--contact-name", default="Test Official")
    p.add_argument("--contact-phone", default="+911234567890")
    args = p.parse_args()

    setup_and_alert(args.url, args.train, args.peak, args.api_key,
                    args.limit, args.contact_name, args.contact_phone)


