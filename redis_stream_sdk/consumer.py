
from .serializers import FrameSerializer

class StreamConsumer:

    def __init__(self, redis):
        self.redis = redis


    async def create_group(self, stream_name, group_name):

        try:
            await self.redis.xgroup_create(
                stream_name,
                group_name,
                id="0",
                mkstream=True
            )

        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def consume_frames(
        self,
        stream_name,
        group_name,
        consumer_name,
        count=6,
        block_ms=20
    ):

        response = await self.redis.xreadgroup(
            group_name,
            consumer_name,
            streams={stream_name: ">"},
            count=count,
            block=block_ms
        )

        messages = []

        for stream, entries in response:

            for msg_id, fields in entries:

                payload = FrameSerializer.unpack_message(
                    fields[b'data']
                )

                messages.append({
                    "id": msg_id,
                    "data": payload
                })

        return messages

    async def ack(self, stream_name, group_name, msg_id):

        await self.redis.xack(
            stream_name,
            group_name,
            *msg_id
        )
        await self.redis.xdel("knife", *msg_id)
