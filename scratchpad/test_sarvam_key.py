"""Standalone Sarvam TTS key/credit checker - not part of the app, just a
one-off test. Run: python scratchpad/test_sarvam_key.py <api_key>"""
import sys
import httpx

if len(sys.argv) != 2:
    print("Usage: python test_sarvam_key.py <SARVAM_API_KEY>")
    sys.exit(1)

api_key = sys.argv[1]

payload = {
    "text": "Testing Sarvam TTS credits.",
    "target_language_code": "en-IN",
    "speaker": "ritu",
    "model": "bulbul:v3",
    "enable_preprocessing": True,
}
headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}

try:
    resp = httpx.post("https://api.sarvam.ai/text-to-speech", headers=headers, json=payload, timeout=15)
    if resp.status_code == 200:
        data = resp.json()
        audios = data.get("audios", [])
        print(f"SUCCESS: key works, received {len(audios)} audio chunk(s). Credits are available.")
    else:
        print(f"FAILED: status {resp.status_code}")
        print(f"Response: {resp.text}")
except Exception as e:
    print(f"ERROR: {e}")
