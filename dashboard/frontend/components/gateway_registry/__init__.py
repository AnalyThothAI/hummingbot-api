def render_gateway_pool_picker(*args, **kwargs):
    from frontend.components.gateway_registry.pool_picker import render_gateway_pool_picker as _render

    return _render(*args, **kwargs)


__all__ = ["render_gateway_pool_picker"]
