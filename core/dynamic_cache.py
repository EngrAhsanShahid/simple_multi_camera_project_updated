import cv2
import hashlib
from collections import OrderedDict
import numpy as np

class FrameCache:
    def __init__(self, max_cache_size=10):
        self.cache = OrderedDict()
        self.max_cache_size = max_cache_size

    def get(self, frame):
        frame_hash = self.fast_stable_hash(image=frame)
        item = self.cache.get(frame_hash)
        if not item:
            return None
        return item

    def set(self, image, inference_result):
        key = self.fast_stable_hash(image=image)
        if key in self.cache:
            # refresh position
            self.cache.pop(key)

        elif len(self.cache) >= self.max_cache_size:
            # pop oldest (FIFO)
            self.cache.popitem(last=False)
        
        self.cache[key] = inference_result

    @staticmethod
    def fast_stable_hash(image):
        # Downscale → huge speed gain + stable hashing
        small = cv2.resize(image, (160, 160), interpolation=cv2.INTER_AREA)
        
        return hashlib.sha256(small.data).digest()  # bytes, not hex


class FrameSimilarCache:
    def __init__(
        self,
        max_cache_size=10,
        diff_threshold=1.0,
        hash_threshold=15
    ):
        self.cache = OrderedDict()
        self.max_cache_size = max_cache_size

        self.diff_threshold = diff_threshold
        self.hash_threshold = hash_threshold

        self.prev_frame = None
        self.prev_result = None
        self.prev_hash = None

    def get(self, frame):
        # 1️⃣ FAST PATH
        if self.prev_frame is not None:
            diff = self.frame_difference(self.prev_frame, frame)
            # print(f"[DEBUG] diff = {diff:.3f}")

            if diff < self.diff_threshold:
                # print("[HIT] fast diff reuse")
                return self.prev_result

        # 2️⃣ HASH CHECK
        query_hash = self.dhash(frame)

        for key_hash, value in reversed(self.cache.items()):
            dist = self.hamming_distance(query_hash, key_hash)
            # print(f"[DEBUG] hash_dist = {dist}")

            if dist <= self.hash_threshold:
                # print("[HIT] hash reuse")
                return value

        # print("[MISS] running inference")
        return None

    def set(self, frame, inference_result):
        frame_hash = self.dhash(frame)

        if frame_hash in self.cache:
            self.cache.pop(frame_hash)
        elif len(self.cache) >= self.max_cache_size:
            self.cache.popitem(last=False)

        self.cache[frame_hash] = inference_result

        self.prev_frame = frame
        self.prev_result = inference_result
        self.prev_hash = frame_hash

    @staticmethod
    def frame_difference(f1, f2):
        f1_small = cv2.resize(f1, (64, 64))
        f2_small = cv2.resize(f2, (64, 64))
        return np.mean(cv2.absdiff(f1_small, f2_small))

    @staticmethod
    def dhash(image, hash_size=16):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        resized = cv2.resize(gray, (hash_size + 1, hash_size))
        diff = resized[:, 1:] > resized[:, :-1]
        return diff.flatten().astype(np.uint8).tobytes()

    @staticmethod
    def hamming_distance(h1, h2):
        b1 = np.unpackbits(np.frombuffer(h1, dtype=np.uint8))
        b2 = np.unpackbits(np.frombuffer(h2, dtype=np.uint8))
        return np.count_nonzero(b1 != b2)