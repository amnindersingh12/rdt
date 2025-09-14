import logging
import os
from logging.handlers import RotatingFileHandler

# Define the log file path
LOG_FILE = "logs.txt"

# Do not delete existing log file; keep history across restarts

# Configure the logging system
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(funcName)s() - Line %(lineno)d: %(name)s - %(message)s",
    datefmt="%d-%b-%y %I:%M:%S %p",
    handlers=[
        # File handler with rotation: max size 5MB, keep up to 10 backups,
        # open file in write-plus mode to overwrite on start
    RotatingFileHandler(LOG_FILE, mode="a", maxBytes=5_000_000, backupCount=10),
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
