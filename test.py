import asyncio
import httpx

LN_API_KEY = "78740dda2e62475e80b33fa84eb91f4a"

# A real Listen Notes audioLink from your DB
audio_link = "https://www.listennotes.com/e/48a02a8cc6544b81bc43d744b74555ea/"

import re
_LN_E_RE = re.compile(r"listennotes\.com/e/([A-Za-z0-9_-]+)/?")
m = _LN_E_RE.search(audio_link)
ep_id = m.group(1)
print(f"Extracted episode ID: {ep_id}")

async def test():
    # Step 1: Call Listen Notes API
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(
            f"https://listen-api.listennotes.com/api/v2/episodes/{ep_id}",
            headers={"X-ListenAPI-Key": LN_API_KEY}
        )
        resp.raise_for_status()
        data = resp.json()

    audio_url = data.get("audio")
    print(f"Audio URL from API: {audio_url}")

    # Step 2: Try downloading first 10KB of the audio
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(audio_url, follow_redirects=True, headers={"Range": "bytes=0-10240"})
        print(f"Audio download status: {resp.status_code}")
        print(f"Content-Type: {resp.headers.get('content-type')}")
        print(f"Bytes received: {len(resp.content)}")

asyncio.run(test())