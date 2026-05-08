import argparse
from http.server import ThreadingHTTPServer

from core.config import DATA_DIR, ensure_dirs, public_settings
from core.server import Handler


def main():
    ensure_dirs()
    parser = argparse.ArgumentParser(description="Gemini Deep Research local app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Gemini Deep Research app: http://{args.host}:{args.port}")
    print(f"Data directory: {DATA_DIR}")
    if not any(item["configured"] for item in public_settings()["providers"].values()):
        print("Warning: no provider API key is configured.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
