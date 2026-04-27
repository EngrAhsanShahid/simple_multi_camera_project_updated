import time

import cv2
import numpy as np

from shared.contracts import CameraConfig, SourceType
from shared.utils.logging import get_logger

logger = get_logger("source_reader")

class SourceReader:
    RECONNECT_DELAY_SEC = 3.0
    MAX_CONSECUTIVE_FAILURES = 30

    def __init__(self, camera_config: CameraConfig) -> None:
        self.config = camera_config
        self.capture: cv2.VideoCapture | None = None
        self.connected = False
        self.consecutive_failures = 0
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5

    @property
    def source_uri(self) -> str:
        if self.config.source_path:
            return self.config.source_path
        if self.config.source_type == SourceType.RTSP:
            return self.config.rtsp_url or ""
        return self.config.file_path or ""

    def open(self) -> bool:
        """Open the video source with better RTSP handling"""
        try:
            uri = self.source_uri
            is_rtsp = uri.startswith("rtsp://")
            
            if is_rtsp:
                # Try different backends for better RTSP compatibility
                backends = [
                    cv2.CAP_FFMPEG,
                    cv2.CAP_ANY
                ]
                
                for backend in backends:
                    logger.info(f"Trying to open RTSP with backend {backend}: {uri}")
                    self.capture = cv2.VideoCapture(uri, backend)
                    
                    if self.capture.isOpened():
                        # Set RTSP specific properties for better performance
                        self.capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reduce buffer
                        self.capture.set(cv2.CAP_PROP_FPS, self.config.target_fps)
                        
                        # Test if we can actually read a frame
                        ret, test_frame = self.capture.read()
                        if ret and test_frame is not None:
                            logger.info(f"Successfully opened RTSP stream with backend {backend}")
                            self.connected = True
                            self.consecutive_failures = 0
                            self.reconnect_attempts = 0
                            return True
                        else:
                            logger.warning(f"Could not read test frame with backend {backend}")
                            self.capture.release()
                            self.capture = None
                    else:
                        logger.warning(f"Failed to open with backend {backend}")
                
                logger.error(f"All backends failed for RTSP stream: {uri}")
                self.connected = False
                return False
            else:
                # For video files
                self.capture = cv2.VideoCapture(uri)
                self.connected = self.capture.isOpened()
                if self.connected:
                    logger.info(f"Successfully opened video file: {uri}")
                else:
                    logger.error(f"Failed to open video file: {uri}")
                return self.connected
                
        except Exception as e:
            logger.error(f"Error opening source: {e}")
            self.connected = False
            return False

    def read_frame(self) -> np.ndarray | None:
        """Read a frame with automatic reconnection for RTSP"""
        if self.capture is None or not self.capture.isOpened():
            uri = self.source_uri
            if uri.startswith("rtsp://"):
                logger.warning(f"Capture not opened, attempting reconnect for {self.config.camera_id}")
                if self.reconnect():
                    # Retry reading after successful reconnect
                    return self.read_frame()
            return None

        try:
            ok, frame = self.capture.read()
            
            if ok and frame is not None:
                self.consecutive_failures = 0
                self.reconnect_attempts = 0  # Reset reconnect attempts on success
                return frame

            uri = self.source_uri
            # Handle different source types
            if not uri.startswith("rtsp://"):
                # For video files, handle looping
                if hasattr(self.config, 'loop') and self.config.loop:
                    logger.info(f"Looping video file: {self.config.camera_id}")
                    self.capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ok, frame = self.capture.read()
                    if ok and frame is not None:
                        return frame
                self.connected = False
                return None

            # For RTSP, track failures and reconnect
            self.consecutive_failures += 1
            logger.warning(f"RTSP read failed for {self.config.camera_id} (failure {self.consecutive_failures}/{self.MAX_CONSECUTIVE_FAILURES})")
            
            if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                logger.warning(f"Max consecutive failures reached for {self.config.camera_id}, attempting reconnect")
                self.connected = False
                if self.reconnect():
                    # Recursive call to get frame after successful reconnect
                    return self.read_frame()
            return None
            
        except Exception as e:
            logger.error(f"Error reading frame from {self.config.camera_id}: {e}")
            self.consecutive_failures += 1
            if self.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                self.connected = False
            return None

    def reconnect(self) -> bool:
        """Reconnect RTSP stream with retry logic"""
        uri = self.source_uri
        if not uri.startswith("rtsp://"):
            logger.debug(f"Reconnect only for RTSP, not for: {uri}")
            return False
            
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"Max reconnect attempts ({self.max_reconnect_attempts}) reached for {self.config.camera_id}")
            return False
            
        self.reconnect_attempts += 1
        logger.info(f"Reconnecting RTSP for {self.config.camera_id} (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        # Release old connection
        self.release()
        
        # Exponential backoff for reconnection
        delay = self.RECONNECT_DELAY_SEC * self.reconnect_attempts
        logger.info(f"Waiting {delay} seconds before reconnecting...")
        time.sleep(delay)
        
        # Try to reopen
        success = self.open()
        if success:
            logger.info(f"Successfully reconnected RTSP for {self.config.camera_id}")
            self.consecutive_failures = 0
        else:
            logger.warning(f"Failed to reconnect RTSP for {self.config.camera_id}")
            
        return success

    def release(self) -> None:
        """Release the video capture"""
        if self.capture is not None:
            self.capture.release()
        self.capture = None
        self.connected = False
        logger.debug(f"Released source for {self.config.camera_id}")