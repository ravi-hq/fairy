from django.contrib.auth import views as auth_views
from django.urls import path

from agent_on_demand.ui import views

urlpatterns = [
    path("", views.dashboard, name="ui-dashboard"),
    path(
        "login",
        auth_views.LoginView.as_view(template_name="ui/login.html"),
        name="ui-login",
    ),
    path(
        "logout",
        auth_views.LogoutView.as_view(),
        name="ui-logout",
    ),
    path("register", views.register, name="ui-register"),
    path("welcome", views.welcome, name="ui-welcome"),
    path("sprites-key", views.sprites_key, name="ui-sprites-key"),
    path("api-keys", views.api_keys, name="ui-api-keys"),
    path("api-keys/<int:key_id>/revoke", views.api_key_revoke, name="ui-api-key-revoke"),
    path("agents", views.agents_list, name="ui-agents"),
    path("agents/<uuid:agent_id>", views.agent_detail, name="ui-agent-detail"),
    path("environments", views.environments_list, name="ui-environments"),
    path(
        "environments/<uuid:environment_id>",
        views.environment_detail,
        name="ui-environment-detail",
    ),
    path("sessions", views.sessions_list, name="ui-sessions"),
    path("sessions/<uuid:session_id>", views.session_detail, name="ui-session-detail"),
]
