from django.urls import path

from . import views


app_name = "reports"

urlpatterns = [
    path("", views.report_list, name="list"),
    path("reports/<int:pk>/", views.report_detail, name="detail"),
    path("runs/<uuid:pk>/", views.run_detail, name="run_detail"),
    path("guides/", views.guide_list, name="guide_list"),
    path("documents/<str:kind>/<slug:slug>/", views.document_detail, name="document_detail"),
    path("documents/<str:kind>/<slug:slug>/content/", views.document_content, name="document_content"),
    path("api/v1/report-runs/", views.ingest_report_run, name="ingest"),
    path("api/v1/documents/", views.publish_document, name="publish_document"),
]
