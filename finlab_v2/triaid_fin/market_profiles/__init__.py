from __future__ import annotations

from .cn import build_profile as build_cn_profile
from .hk import build_profile as build_hk_profile
from .us import build_profile as build_us_profile


PROFILE_FACTORIES=(
    build_us_profile,
    build_cn_profile,
    build_hk_profile,
)


def builtin_profiles():
    return tuple(factory() for factory in PROFILE_FACTORIES)
