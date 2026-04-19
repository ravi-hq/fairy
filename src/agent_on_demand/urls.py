from django.urls import include, path

from agent_on_demand.api import api
from agent_on_demand.ui import views as ui_views
from agent_on_demand.views import agents, environments, health, sessions

api.add_router("", health.router)
api.add_router("", environments.router)
api.add_router("", agents.router)
api.add_router("", sessions.router)

urlpatterns = [
    path("", ui_views.landing, name="landing"),
    path("ui/", include("agent_on_demand.ui.urls")),
    path("", api.urls),
]
