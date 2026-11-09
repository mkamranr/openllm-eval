"""Adapter registry: the documented plugin point for new providers.

To add an adapter, subclass ``ModelAdapter``, set ``adapter_type`` / ``display_name`` /
``version``, decorate the class with ``@register_adapter``, and import its module in
``app/adapters/__init__.py``. It must pass ``tests/contract/test_adapter_contract.py``.
"""

from __future__ import annotations

from app.adapters.base import AdapterContext, EndpointRuntimeConfig, ModelAdapter

_REGISTRY: dict[str, type[ModelAdapter]] = {}


def register_adapter[T: type[ModelAdapter]](cls: T) -> T:
    if cls.adapter_type in _REGISTRY and _REGISTRY[cls.adapter_type] is not cls:
        raise ValueError(f"Adapter type {cls.adapter_type!r} is already registered")
    _REGISTRY[cls.adapter_type] = cls
    return cls


def is_registered(adapter_type: str) -> bool:
    return adapter_type in _REGISTRY


def adapter_classes() -> dict[str, type[ModelAdapter]]:
    return dict(_REGISTRY)


def create_adapter(
    adapter_type: str, config: EndpointRuntimeConfig, context: AdapterContext
) -> ModelAdapter:
    try:
        cls = _REGISTRY[adapter_type]
    except KeyError:
        raise ValueError(f"Unknown adapter type {adapter_type!r}") from None
    return cls(config, context)


def _register_builtins() -> None:
    from app.adapters.openai_compatible import OpenAICompatibleAdapter

    register_adapter(OpenAICompatibleAdapter)


_register_builtins()
