"""按功能划分的产品 API，挂在 /app 下。"""

from nono_app_server.features.router import mount_product_routes

__all__ = ["mount_product_routes"]
