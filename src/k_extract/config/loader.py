"""YAML load/save functions for extraction.yaml.

Handles reading, validating, and writing the config file
that bridges `k-extract init` and `k-extract run`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from k_extract.config.schema import ExtractionConfig


class _MultilineDumper(yaml.SafeDumper):
    """YAML dumper that uses literal block style for multiline strings."""


def _str_representer(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Represent strings with block literal style when they contain newlines."""
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_MultilineDumper.add_representer(str, _str_representer)


def load_config(path: Path | str) -> ExtractionConfig:
    """Load and validate an extraction config from a YAML file.

    Args:
        path: Path to the extraction.yaml file.

    Returns:
        Validated ExtractionConfig instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        yaml.YAMLError: If the file is not valid YAML.
        pydantic.ValidationError: If the config fails validation.
    """
    path = Path(path)
    with path.open() as f:
        data = yaml.safe_load(f)
    return ExtractionConfig.model_validate(data)


def save_config(config: ExtractionConfig, path: Path | str) -> None:
    """Save an extraction config to a YAML file.

    Args:
        config: Validated ExtractionConfig instance.
        path: Path to write the YAML file.
    """
    path = Path(path)
    data = config.model_dump()
    with path.open("w") as f:
        yaml.dump(
            data,
            f,
            Dumper=_MultilineDumper,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
