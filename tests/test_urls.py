import pytest
from helpers.external import is_supported_url, extract_supported_url

@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc", True),
    ("https://youtu.be/xyz", True),
    ("https://instagram.com/p/123", True),
    ("https://www.pinterest.com/pin/123", True),
    ("https://pin.it/abcd", True),
    ("https://example.com/video", False),
])
def test_is_supported_url(url, expected):
    assert is_supported_url(url) is expected

@pytest.mark.parametrize("text,expected_part", [
    ("Check this https://youtu.be/xyz great", "https://youtu.be/xyz"),
    ("Leading (https://www.youtube.com/watch?v=abc).", "https://www.youtube.com/watch?v=abc"),
    ("Multiple https://instagram.com/p/123 and https://pin.it/abcd", "https://instagram.com/p/123"),
    ("No links here", None),
])
def test_extract_supported_url(text, expected_part):
    result = extract_supported_url(text)
    if expected_part is None:
        assert result is None
    else:
        assert result is not None and result.startswith(expected_part)
