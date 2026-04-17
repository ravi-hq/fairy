from django.urls import path

from fairy import views

urlpatterns = [
    path("health", views.health),
    # Environments
    path("environments", views.environments_list_create),
    path("environments/<uuid:environment_id>", views.environment_detail),
    path("environments/<uuid:environment_id>/archive", views.environment_archive),
    path("environments/<uuid:environment_id>/delete", views.environment_delete),
    path("environments/<uuid:environment_id>/versions", views.environment_versions),
    # Agents
    path("agents", views.agents_list_create),
    path("agents/<uuid:agent_id>", views.agent_detail),
    path("agents/<uuid:agent_id>/archive", views.agent_archive),
    path("agents/<uuid:agent_id>/versions", views.agent_versions),
    # Sessions
    path("sessions", views.sessions_list_create),
    path("sessions/<uuid:session_id>", views.get_session),
    path("sessions/<uuid:session_id>/prompt", views.send_prompt),
    path("sessions/<uuid:session_id>/terminate", views.terminate_session),
    path("sessions/<uuid:session_id>/delete", views.delete_session),
    path("sessions/<uuid:session_id>/stream", views.stream_session),
]
