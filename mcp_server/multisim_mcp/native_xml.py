"""Read decoder XML without destroying multiline SPICE attribute values."""
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def parse_native_xml(path: str | Path) -> ET.ElementTree:
    # The native decoder emits literal newlines inside quoted attributes.
    # XML parsers normalize these to spaces, turning a leading SPICE comment
    # into a comment covering the entire macromodel. Escape before parsing.
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        text = stream.read()
    # The decoder can leave a stray NUL byte in otherwise valid output (seen in
    # roughly a quarter of the stock samples). XML 1.0 forbids NUL anywhere, so
    # strip it here rather than make every caller rediscover the parse failure.
    if "\x00" in text:
        text = text.replace("\x00", "")
    def preserve(match: re.Match[str]) -> str:
        value = match[3].replace("\r", "&#13;").replace("\n", "&#10;").replace("\t", "&#9;")
        return match[1] + match[2] + value + match[2]
    text = re.sub(r'''(=\s*)(["'])(.*?)\2''', preserve, text, flags=re.DOTALL)
    return ET.ElementTree(ET.fromstring(text))


def write_native_xml(tree: ET.ElementTree, path: str | Path) -> None:
    # The encoder handles named XML entities but stores numeric LF/CR/TAB
    # references literally in SPICE. Its decoder uses raw whitespace instead.
    text = ET.tostring(tree.getroot(), encoding="unicode", xml_declaration=True)
    for reference, value in (("&#10;", "\n"), ("&#13;", "\r"), ("&#09;", "\t")):
        text = text.replace(reference, value)
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)
