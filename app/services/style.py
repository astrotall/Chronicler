import logging
from pathlib import Path

from app.config.constants import EXAMPLES_ENCODING, EXAMPLES_FILE_SUFFIX, HIDDEN_FILE_PREFIX

logger = logging.getLogger(__name__)


def is_example_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() == EXAMPLES_FILE_SUFFIX
        and not path.name.startswith(HIDDEN_FILE_PREFIX)
    )


def load_examples(directory: Path, limit: int) -> list[str]:
    if limit <= 0 or not directory.is_dir():
        return []
    examples: list[str] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if len(examples) >= limit:
            break
        if not is_example_file(path):
            continue
        try:
            text = path.read_text(encoding=EXAMPLES_ENCODING).strip()
        except UnicodeDecodeError:
            logger.warning("example skipped, not %s: name=%s", EXAMPLES_ENCODING, path.name)
            continue
        if text:
            examples.append(text)
    return examples
