from pyrogram.parser import Parser
from pyrogram.utils import get_channel_id


async def get_parsed_msg(text, entities):
    """
    Asynchronously parses message text with provided entities into plain text.

    Args:
        text (str): The raw message text.
        entities (list): List of message entities for formatting.

    Returns:
        str: The unparsed text without HTML formatting.
    """
    # Use Parser.unparse to convert text and entities back to readable format without HTML.
    return Parser.unparse(text, entities or [], is_html=False)


def getChatMsgID(link: str):
    """
    Extracts chat ID, message thread ID, and message ID from a Telegram message link.

    Args:
        link (str): The Telegram message link.

    Returns:
        tuple: (chat_id, message_id), where chat_id can be int or str depending on the link format.

    Raises:
        ValueError: If the link format is invalid or missing numeric IDs.
    """
    # Split the link by '/' to analyze its structure
    link_parts = link.split("/")

    chat_id, message_thread_id, message_id = None, None, None

    try:
        # Case: Link with 7 parts, e.g. .../c/channel_id/thread_id/message_id
        if len(link_parts) == 7 and link_parts[3] == "c":
            chat_id = get_channel_id(int(link_parts[4]))
            message_thread_id = int(link_parts[5])
            message_id = int(link_parts[6])

        # Case: Link with 6 parts, could have either thread or no thread IDs
        elif len(link_parts) == 6:
            if link_parts[3] == "c":
                chat_id = get_channel_id(int(link_parts[4]))
                message_id = int(link_parts[5])
            else:
                chat_id = link_parts[3]
                message_thread_id = int(link_parts[4])
                message_id = int(link_parts[5])

        # Case: Link with 5 parts, simple link with chat_id and message_id
        elif len(link_parts) == 5:
            chat_id = link_parts[3]
            if chat_id == "m":
                raise ValueError("Invalid ClientType used to parse this message link")
            message_id = int(link_parts[4])

    except (ValueError, TypeError):
        # Raised if IDs are not convertible to int or any other parsing error occurs
        raise ValueError("Invalid post URL. Must end with a numeric ID.")

    # Validate that chat_id and message_id were extracted successfully
    if not chat_id or not message_id:
        raise ValueError("Please send a valid Telegram post URL.")

    # Return only chat_id and message_id since message_thread_id is unused in output
    return chat_id, message_id


def get_file_name(message_id: int, chat_message) -> str:
    """
    Determines the filename for a media file in a Telegram message based on message content type.

    Args:
        message_id (int): The unique message identifier.
        chat_message: The message object, potentially containing media.

    Returns:
        str: The filename with appropriate extension based on media type.
    """
    # Document file usually has a file name directly accessible.
    if chat_message.document:
        return chat_message.document.file_name

    # Video files fallback to message_id with .mp4 if filename is absent.
    elif chat_message.video:
        return chat_message.video.file_name or f"{message_id}.mp4"

    # Audio files fallback similarly with .mp3 extension.
    elif chat_message.audio:
        return chat_message.audio.file_name or f"{message_id}.mp3"

    # Voice messages use .ogg extension with message ID.
    elif chat_message.voice:
        return f"{message_id}.ogg"

    # Video notes saved as .mp4 with message ID.
    elif chat_message.video_note:
        return f"{message_id}.mp4"

    # Animations fallback to file name or use .gif extension.
    elif chat_message.animation:
        return chat_message.animation.file_name or f"{message_id}.gif"

    # Stickers can be static, animated, or video with different extensions.
    elif chat_message.sticker:
        if chat_message.sticker.is_animated:
            return f"{message_id}.tgs"
        elif chat_message.sticker.is_video:
            return f"{message_id}.webm"
        else:
            return f"{message_id}.webp"

    # Photos are saved as .jpg with message ID.
    elif chat_message.photo:
        return f"{message_id}.jpg"

    # Default fallback to message ID as string if no media is detected.
    else:
        return f"{message_id}"
