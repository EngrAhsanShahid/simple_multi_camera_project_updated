
import cv2
import numpy as np
import msgpack

class FrameSerializer:

    @staticmethod
    def encode_frame(frame: np.ndarray, jpeg_quality: int = 85) -> bytes:
        success, buffer = cv2.imencode(
            ".jpg",
            frame,
            [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality],
        )

        if not success:
            raise RuntimeError("JPEG encode failed")

        return buffer.tobytes()

    @staticmethod
    def decode_frame(jpeg_bytes: bytes) -> np.ndarray:
        np_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)

        return cv2.imdecode(
            np_arr,
            cv2.IMREAD_COLOR
        )

    @staticmethod
    def pack_message(request_id=None, frame=None, jpeg_quality=85) -> bytes:
        payload = {}

        if frame is not None:
            payload["frame"] = FrameSerializer.encode_frame(
                frame,
                jpeg_quality
            )
        if request_id is not None:
            payload["request_id"] = request_id

        return msgpack.packb(payload, use_bin_type=True)

    @staticmethod
    def unpack_message(packed: bytes, decode_frame=True):
        payload = msgpack.unpackb(packed, raw=False)

        if decode_frame and "frame" in payload:
            payload["frame"] = FrameSerializer.decode_frame(payload["frame"])

        return payload
