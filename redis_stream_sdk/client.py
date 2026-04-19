
import redis.asyncio as redis

class RedisClient:
    def __init__(self, url: str = "redis://localhost:6379"):
        self._redis = redis.from_url(url, decode_responses=False)

    async def get(self):
        await self._redis.ping()
        return self._redis

    async def close(self):
        close = getattr(self._redis, "aclose", None)
        if callable(close):
            await close()
            return
        await self._redis.close()
