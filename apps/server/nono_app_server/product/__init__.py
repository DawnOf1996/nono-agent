"""产品层 /app 路由：sidecar 元信息、模型目录、权限确认、本机文件系统。"""

from nono_app_server.product.router import mount_product_routes

__all__ = ["mount_product_routes"]
