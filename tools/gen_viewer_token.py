"""Generate a subscriber JWT for the LiveKit Web UI.

Usage:
    conda run -n yolo python tools/gen_viewer_token.py tenant_01 viewer_anas

Then paste the printed token into the WebUI's "Token" textarea and click Connect.
"""

import sys
from pathlib import Path

# Make project root importable so this script works when invoked from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import AppSettings
from livekit.tokens import generate_subscriber_token


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/gen_viewer_token.py <tenant_id> [<viewer_id>]")
        sys.exit(1)

    tenant_id = sys.argv[1]
    viewer_id = sys.argv[2] if len(sys.argv) >= 3 else "viewer"

    settings = AppSettings()
    token = generate_subscriber_token(tenant_id, viewer_id, settings.livekit)

    print(f"\nTenant : {tenant_id}")
    print(f"Viewer : {viewer_id}")
    print(f"Server : {settings.livekit.url}")
    print(f"\nToken (paste into WebUI):\n\n{token}\n")


if __name__ == "__main__":
    main()
