from django.urls import path

from fairy import views

urlpatterns = [
    path("health", views.health),
    # Agents
    path("agents", views.agents_list_create),
    path("agents/<uuid:agent_id>", views.agent_detail),
    path("agents/<uuid:agent_id>/archive", views.agent_archive),
    path("agents/<uuid:agent_id>/versions", views.agent_versions),
    # Sessions
    path("sessions", views.create_session),
    path("sessions/<uuid:session_id>", views.get_session),
    path("sessions/<uuid:session_id>/prompt", views.send_prompt),
    path("sessions/<uuid:session_id>/terminate", views.terminate_session),
    path("sessions/<uuid:session_id>/delete", views.delete_session),
    path("sessions/<uuid:session_id>/stream", views.stream_session),
]
