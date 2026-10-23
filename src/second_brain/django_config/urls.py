"""Root URL configuration for the local API."""

from django.urls import path

from .api import api

urlpatterns = [path("v1/", api.urls)]
