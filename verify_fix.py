import re

# Test Player Tracking Regex
def test_regex():
    print("Testing Regex...")
    sample_text = """
    Some random text
    Current Players 3 / 10
    More text
    """
    match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", sample_text)
    if match:
        print(f"PASS: Found {match.group(1)} / {match.group(2)}")
    else:
        print("FAIL: Regex did not match")

    sample_text_2 = "Current Players0/5"
    match = re.search(r"Current Players\s*(\d+)\s*/\s*(\d+)", sample_text_2)
    if match:
        print(f"PASS: Found {match.group(1)} / {match.group(2)}")
    else:
        print("FAIL: Regex did not match compact format")

if __name__ == "__main__":
    test_regex()
