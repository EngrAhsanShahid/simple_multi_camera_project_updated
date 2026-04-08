import logging
import os
from dotenv import load_dotenv
load_dotenv()
def get_logger(name: str = "app"):
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Convert string → logging level
    level = getattr(logging, log_level, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    )

    return logging.getLogger(name)