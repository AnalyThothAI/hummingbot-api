from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from frontend.components.clmm_config_generator.page import render_config_generator_page  # noqa: F401


def render_config_generator_page() -> None:
    from frontend.components.clmm_config_generator.page import render_config_generator_page as _render

    return _render()


__all__ = ["render_config_generator_page"]
