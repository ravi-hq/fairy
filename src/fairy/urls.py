from django.urls import path

from fairy import views

urlpatterns = [
    path("health", views.health),
    path("run", views.run_agent),
]
