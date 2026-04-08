import numpy as np
from multiprocessing import shared_memory

class SharedFrameBuffer:
    def __init__(self, shape, dtype=np.uint8, size=20):
        self.shape = shape
        self.dtype = dtype
        self.size = size
        self.buffers = []
        self.index = 0

        for _ in range(size):
            shm = shared_memory.SharedMemory(
                create=True,
                size=np.prod(shape) * np.dtype(dtype).itemsize
            )
            self.buffers.append(shm)

    def write(self, frame):
        shm = self.buffers[self.index]
        arr = np.ndarray(self.shape, dtype=self.dtype, buffer=shm.buf)
        arr[:] = frame

        fid = self.index
        self.index = (self.index + 1) % self.size
        return fid

    def cleanup(self):
        for shm in self.buffers:
            shm.close()
            shm.unlink()