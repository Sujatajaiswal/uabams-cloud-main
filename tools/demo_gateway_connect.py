import urllib.request
import json
import sys
import argparse

def main():
    parser = argparse.ArgumentParser(description="Test Gateway Connectivity using Serial Number.")
    parser.add_argument(
        "--url", 
        default="http://127.0.0.1:8000", 
        help="Base URL of the FastAPI cloud server (e.g. http://127.0.0.1:8000 or https://your-app.onrender.com)"
    )
    parser.add_argument(
        "--serial", 
        default="UABAMS_PIL_01", 
        help="Serial Number or Gateway ID to verify against the allowed list"
    )
    args = parser.parse_args()

    # Build endpoint URL
    base_url = args.url.rstrip("/")
    endpoint_url = f"{base_url}/api/v1/gateway/demo-connect"

    print("-------------------------------------------------------------")
    print(f"Initiating Gateway Connection Check")
    print(f"Target URL:    {endpoint_url}")
    print(f"Serial Number: {args.serial}")
    print("-------------------------------------------------------------")

    # Build JSON payload
    payload = {
        "serialNo": args.serial,
        "sensorReadings": {
            "temperature_c": 28.5,
            "vibration_g": 0.05,
            "battery_percent": 98
        }
    }

    try:
        # Perform HTTP POST request
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint_url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            response_code = response.getcode()
            response_body = response.read().decode("utf-8")
            res_json = json.loads(response_body)

        status = res_json.get("status", "unknown").upper()
        message = res_json.get("message", "")
        gateway_id = res_json.get("gatewayId")
        train_id = res_json.get("trainId")

        print(f"Response Code: {response_code}")
        print("-------------------------------------------------------------")
        if status == "APPROVED":
            print(f"[APPROVED] Gateway verified successfully!")
            print(f"Message:    {message}")
            print(f"Gateway ID: {gateway_id}")
            print(f"Train ID:   {train_id}")
        else:
            print(f"[DENIED] Gateway authentication failed!")
            print(f"Message:    {message}")
        print("-------------------------------------------------------------")

    except urllib.error.HTTPError as exc:
        print(f"HTTP Error: {exc.code} {exc.reason}")
        try:
            err_body = exc.read().decode("utf-8")
            print(f"Error Body: {err_body}")
        except Exception:
            pass
        print("-------------------------------------------------------------")
    except Exception as exc:
        print(f"Connection Error: {exc}")
        print("Please verify the server URL is correct and active.")
        print("-------------------------------------------------------------")

if __name__ == "__main__":
    main()
