from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .default_strategy import DefaultStrategy
    from .user_strategy import UserStrategy

__all__ = ["DefaultStrategy", "UserStrategy"]


def __getattr__(name: str):
    if name == "DefaultStrategy":
        from .default_strategy import DefaultStrategy
        return DefaultStrategy
    if name == "UserStrategy":
        from .user_strategy import UserStrategy
        return UserStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

