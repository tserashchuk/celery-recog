from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from bitrix_tasks import views as bitrix_views

urlpatterns = [
    path("", bitrix_views.home, name="home"),
    path("deepseek/", bitrix_views.deepseek_query, name="deepseek_query"),
    path("plan/", bitrix_views.plan_with_ai, name="plan_with_ai"),
    path("tasks/", bitrix_views.my_tasks, name="my_tasks"),
    path("tasks/<int:task_id>/run/", bitrix_views.run_task, name="run_task"),
    path("tasks/progress/", bitrix_views.run_progress, name="run_progress"),
    path("connection/", bitrix_views.connection_edit, name="connection_edit"),
    path("contacts/", bitrix_views.contact_list, name="contact_list"),
    path("contacts/enrich/", bitrix_views.enrich_contacts, name="enrich_contacts"),
    path("contacts/<int:contact_id>/", bitrix_views.contact_detail, name="contact_detail"),
    path("accounts/register/", bitrix_views.RegisterView.as_view(), name="register"),
    path("accounts/login/", LoginView.as_view(template_name="bitrix_tasks/login.html"), name="login"),
    path("accounts/logout/", LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
]
