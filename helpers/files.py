import logging
import shutil
from pathlib import Path
from typing import Optional, Union

# Constants representing file size units and limits for standard and premium users
SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]
BYTES_IN_GB = 1024**3
MAX_FILE_SIZE_BYTES = 2 * BYTES_IN_GB             # 2 GB limit for regular users
PREMIUM_MAX_FILE_SIZE_BYTES = 4 * BYTES_IN_GB     # 4 GB limit for premium users

# Logger setup for monitoring and debugging
logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger(__name__)


def get_download_path(folder_id: Union[int, str], filename: str, root_dir: str = "downloads") -> Path:
    """
    Constructs and ensures the existence of a download directory path for storing files.

    Args:
        folder_id: Identifier for a subfolder inside the root directory (usually some user or chat ID).
        filename: Desired name of the file to be saved.
        root_dir: Base directory where downloads are stored (default is 'downloads').

    Returns:
        Path: Complete Path object pointing to the target file location.
    """
    # Create a Path object for the target subdirectory
    download_folder = Path(root_dir) / str(folder_id)
    # Ensure the directory exists, create if necessary (including parents)
    download_folder.mkdir(parents=True, exist_ok=True)
    # Return the full file path by joining folder and filename
    return download_folder / filename


def cleanup_download(path: Union[str, Path]) -> None:
    """
    Cleans up downloaded files by deleting the specified file, any temp file variation,
    and removes the parent folder if it is left empty after cleanup.

    Args:
        path: Path to the downloaded file (string or Path object).
    """
    try:
        LOGGER.info(f"Cleaning up download: {path}")
        file_path = Path(path)

        # Remove the file itself, skipping error if it doesn't exist
        file_path.unlink(missing_ok=True)
        # Remove the corresponding temporary file (e.g., '.mp4.temp'), skipping error if missing
        file_path.with_suffix(file_path.suffix + ".temp").unlink(missing_ok=True)

        folder_path = file_path.parent
        # If the folder is empty, remove it to avoid clutter
        if folder_path.is_dir() and not any(folder_path.iterdir()):
            shutil.rmtree(folder_path)

    except OSError as e:
        LOGGER.error(f"Cleanup failed for {path}: {e}")


def get_readable_file_size(size_in_bytes: Optional[float]) -> str:
    """
    Converts a file size in bytes to a human-readable string with appropriate units.

    Args:
        size_in_bytes: File size in bytes.

    Returns:
        str: Human-readable string formatted with two decimal places (e.g., "1.50 MB").
    """
    if size_in_bytes is None or size_in_bytes < 0:
        return "0 B"

    if size_in_bytes == 0:
        return "0 B"

    # Iterate through the size units scaling down the size by 1024 at each step
    for unit in SIZE_UNITS:
        if size_in_bytes < 1024:
            return f"{size_in_bytes:.2f} {unit}"
        size_in_bytes /= 1024

    # If it's extremely large, default to the largest unit in the list (PB)
    return f"{size_in_bytes:.2f} {SIZE_UNITS[-1]}"


def get_readable_time(seconds: int) -> str:
    """
    Converts a time duration from seconds into a readable format with days, hours, minutes, and seconds.

    Args:
        seconds: Duration in seconds.

    Returns:
        str: Human-readable duration string (e.g., "1d 2h 3m 4s").
    """
    if seconds < 0:
        return "0s"

    time_parts = []
    # Define time units and their equivalent in seconds
    time_units = [("d", 86400), ("h", 3600), ("m", 60)]

    # Compute each unit value and append if non-zero
    for unit, divisor in time_units:
        value, seconds = divmod(seconds, divisor)
        if value > 0:
            time_parts.append(f"{int(value)}{unit}")

    # Append remaining seconds or "0s" if nothing else was added
    if seconds > 0 or not time_parts:
        time_parts.append(f"{int(seconds)}s")

    # Join all parts with spaces
    return " ".join(time_parts)

async def fileSizeLimit(file_size: int, message, action_type: str = "download", is_premium: bool = False) -> bool:
    """
    Checks if a file size is within allowed limits depending on user status (premium or regular).
    Sends a reply message if the file size exceeds the limit.

    Args:
        file_size: Size of the file in bytes.
        message: Message object to reply to. Assumed to have an async reply() method.
        action_type: String describing the action attempted ('download', 'upload', etc.).
        is_premium: Boolean indicating if the user has premium privileges.

    Returns:
        bool: True if the file size is within the allowed limit, False otherwise.
    """
    max_size = PREMIUM_MAX_FILE_SIZE_BYTES if is_premium else MAX_FILE_SIZE_BYTES
    if file_size > max_size:
        readable_limit = get_readable_file_size(max_size)
        await message.reply(
            f"The file size exceeds the {readable_limit} limit and cannot be {action_type}ed."
        )
        return False
    return True
