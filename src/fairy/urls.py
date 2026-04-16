from django.urls import path

from fairy import views

urlpatterns = [
    path("health", views.health),
    path("sessions", views.create_session),
    path("sessions/<uuid:session_id>", views.get_session),
    path("sessions/<uuid:session_id>/stream", views.stream_session),
]
