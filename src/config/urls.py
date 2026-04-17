from django.contrib import admin
from django.urls import include, path

from fairy.test_mcp import mcp_streamable_http

urlpatterns = [
    path("admin/", admin.site.urls),
    path("test-mcp", mcp_streamable_http),
    path("", include("fairy.urls")),
]
