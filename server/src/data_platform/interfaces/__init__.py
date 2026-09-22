"""外部接口插件化接入层（批 63）——provider 注册表与 schema 声明。"""
from .base import InterfaceProvider, get_interface_provider, list_interface_schemas, register_provider

__all__ = ["InterfaceProvider", "get_interface_provider", "list_interface_schemas", "register_provider"]
