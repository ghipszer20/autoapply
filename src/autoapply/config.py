from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError, field_validator


class ConfigError(Exception):
    pass


class FilterConfig(BaseModel):
    categories: list[str]
    allowed_terms: list[str]
    title_include: list[str]
    title_exclude: list[str]
    require_bachelors: bool = True
    us_only: bool = True
    us_citizen: bool

    @field_validator("title_include", "title_exclude")
    @classmethod
    def _valid_regexes(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            try:
                re.compile(p)
            except re.error as e:
                raise ValueError(f"bad regex {p!r}: {e}") from e
        return patterns


class Config(BaseModel):
    filter: FilterConfig
    boards: dict[str, dict[str, str]] = {}


def load_config(path: str | Path) -> Config:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return Config.model_validate(raw)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
        raise ConfigError(f"{path}: {problems}") from e
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"{path}: {e}") from e
