from django.urls import include, path

from agent_on_demand.ui import views as ui_views
from agent_on_demand.views import agents, environments, health, sessions

urlpatterns = [
    path("", ui_views.landing, name="landing"),
    path("ui/", include("agent_on_demand.ui.urls")),
    path("health", health.health),
    # Environments
    path("environments", environments.environments_list_create),
    path("environments/<uuid:environment_id>", environments.environment_detail),
    path("environments/<uuid:environment_id>/archive", environments.environment_archive),
    path("environments/<uuid:environment_id>/delete", environments.environment_delete),
    path("environments/<uuid:environment_id>/versions", environments.environment_versions),
    # Agents
    path("agents", agents.agents_list_create),
    path("agents/<uuid:agent_id>", agents.agent_detail),
    path("agents/<uuid:agent_id>/archive", agents.agent_archive),
    path("agents/<uuid:agent_id>/versions", agents.agent_versions),
    # Sessions
    path("sessions", sessions.sessions_list_create),
    path("sessions/<uuid:session_id>", sessions.get_session),
    path("sessions/<uuid:session_id>/prompt", sessions.send_prompt),
    path("sessions/<uuid:session_id>/turns", sessions.list_session_turns),
    path("sessions/<uuid:session_id>/interrupt", sessions.interrupt_session),
    path("sessions/<uuid:session_id>/terminate", sessions.terminate_session),
    path("sessions/<uuid:session_id>/delete", sessions.delete_session),
    path("sessions/<uuid:session_id>/stream", sessions.stream_session),
]
