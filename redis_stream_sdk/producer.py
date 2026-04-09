
from typing import Dict, Optional
from .serializers import FrameSerializer
import msgpack
class StreamProducer:

    def __init__(self, redis):
        self.redis = redis

    async def publish_frame(
        self,
        stream_name: str,
        frame,
        request_id: Optional[str] = None,
        maxlen: int = 50000,
        ttl_seconds: Optional[int] = None,
        jpeg_quality: int = 85
    ):

        packed = FrameSerializer.pack_message(
            request_id,
            frame,
            jpeg_quality
        )

        msg_id = await self.redis.xadd(
            stream_name,
            {"data": packed},
            maxlen=maxlen,
            approximate=True
        )

        if ttl_seconds:
            await self.redis.expire(stream_name, ttl_seconds)

        return msg_id
    
    async def get_results(self, request_id):
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(request_id)
        async for message in pubsub.listen():

            # skip internal subscribe confirmation messages
            if message["type"] != "message":
                continue

            payload = msgpack.unpackb(message["data"], raw=False)
            break
        return payload
