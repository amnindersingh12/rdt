import logging
import os
from logging.handlers import RotatingFileHandler

# Define the log file path
LOG_FILE = "logs.txt"

# Attempt to remove old log file if it exists, ignoring errors other than file not found
try:
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
except Exception as e:
    # Log this exception temporarily to stderr but do not fail app start
    print(f"Warning: Failed to remove old log file '{LOG_FILE}': {e}")

# Configure the logging system
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(funcName)s() - Line %(lineno)d: %(name)s - %(message)s",
    datefmt="%d-%b-%y %I:%M:%S %p",
    handlers=[
        # File handler with rotation: max size 5MB, keep up to 10 backups,
        # open file in write-plus mode to overwrite on start
        RotatingFileHandler(LOG_FILE, mode="w+", maxBytes=5_000_000, backupCount=10),
        # Console output handler
        logging.StreamHandler(),
    ],
)

# Set the log level for 'pyrogram' logger to WARNING to reduce verbosity
logging.getLogger("pyrogram").setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    """
    Helper function to get a logger instance by name.

    Args:
        name (str): Name of the logger.

    Returns:
        logging.Logger: Logger instance with the specified name.
    """
    return logging.getLogger(name)
