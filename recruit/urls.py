from django.urls import path
from django.views.generic import RedirectView

from . import views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("health/", views.health, name="health"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("dashboard/m/<int:pk>/", views.mentor_detail, name="mentor_detail"),
    path("dashboard/import/", views.import_mentors, name="import_mentors"),
    path("dashboard/export/<str:kind>.csv", views.export_csv, name="export_csv"),
    path("webhooks/twilio/inbound/", views.twilio_inbound, name="twilio_inbound"),
    path("webhooks/twilio/status/", views.twilio_status, name="twilio_status"),
]
