import asyncio
from shazamio import Shazam

class ShazamService:
    def __init__(self):
        self.shazam = Shazam()

    async def recognize(self, file_path: str, timeout: int = 30) -> str:
        try:
            out = await asyncio.wait_for(
                self.shazam.recognize(file_path),
                timeout=timeout
            )

            track = out.get('track', {})
            if not track:
                return "Original Sound"

            title = track.get('title', 'Unknown Track')
            subtitle = track.get('subtitle', 'Unknown Artist')

            return f"{title} — {subtitle}"

        except asyncio.TimeoutError:
            return "Recognition timeout"
        except Exception:
            return "Original Sound"