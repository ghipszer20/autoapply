"""profile.yaml: the user's own answers. Sensitive answers come only from here, never from the LLM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED = (
    "identity.first_name",
    "identity.last_name",
    "identity.email",
    "identity.phone",
    "education.school",
    "education.expected_graduation",
    "work_authorization.authorized_to_work_in_us",
    "work_authorization.requires_sponsorship_now",
    "work_authorization.requires_sponsorship_future",
)
_MISSING = object()


class ProfileError(Exception):
    pass


@dataclass
class Profile:
    data: dict

    @classmethod
    def load(cls, path: str | Path) -> Profile:
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8-sig")) or {}
        except (OSError, yaml.YAMLError) as e:
            raise ProfileError(f"{path}: {e}") from e
        prof = cls(data)
        missing = [k for k in REQUIRED if prof.get(k, _MISSING) in (_MISSING, None, "")]
        if missing:
            raise ProfileError(f"{path}: missing {', '.join(missing)}")
        return prof

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur
