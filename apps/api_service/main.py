from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
import requests

from apps.api_service.routes.stream import router as stream_router


app = FastAPI(title="Nexa LiveKit API")

# Allow requests from the UI (development convenience)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stream_router)

# Serve static UI from the `web/` folder at /ui
app.mount("/ui", StaticFiles(directory="web", html=True), name="web_ui")


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/cdn/livekit-client")
def proxy_livekit_client() -> Response:
    """Proxy the LiveKit client ESM bundle from a CDN and return it from
    the same origin so the browser can import it without CORS issues.

    This fetches the ESM build from jsdelivr (pinned version). If the
    remote fetch fails we return 502.
    """
    # The livekit-client ESM bundle filename is `livekit-client.esm.mjs` in
    # recent releases. Use jsDelivr to fetch it.
    url = "https://cdn.jsdelivr.net/npm/livekit-client@2.18.1/dist/livekit-client.esm.mjs"
    try:
        r = requests.get(url, timeout=10)
    except Exception as e:
        return Response(content=f"/* proxy error: {e} */", media_type="application/javascript", status_code=502)

    if r.status_code != 200:
        return Response(content=f"/* upstream {r.status_code} */", media_type="application/javascript", status_code=502)

    return Response(content=r.content, media_type="application/javascript")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("apps.api_service.main:app", host="0.0.0.0", port=8001, log_level="info")
