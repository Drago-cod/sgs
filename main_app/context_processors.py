from django.urls import reverse

from .middleware import ROLE_HOME_URLS
from .models import AIAttendanceNotification

ROLE_PROFILE_URLS = {
    "1": "admin_view_profile",
    "2": "staff_view_profile",
    "3": "student_view_profile",
    "4": "dos_view_profile",
    "5": "bursar_view_profile",
    "6": "staff_view_profile",
}


def ai_attendance_alerts(request):
    """Provide unread AI alerts and role navigation links to all templates."""
    user = getattr(request, "user", None)
    context = {
        "ai_unread_count": 0,
        "ai_recent_alerts": [],
        "user_home_url": reverse("login_page"),
        "user_profile_url": reverse("login_page"),
    }
    if user and user.is_authenticated:
        role = str(getattr(user, "user_type", ""))
        context["user_home_url"] = reverse(ROLE_HOME_URLS.get(role, "login_page"))
        context["user_profile_url"] = reverse(ROLE_PROFILE_URLS.get(role, "login_page"))
        if role == "1":
            unread_qs = AIAttendanceNotification.objects.filter(
                is_read=False, is_resolved=False
            ).order_by("-created_at")
            context["ai_unread_count"] = unread_qs.count()
            context["ai_recent_alerts"] = list(unread_qs[:6])
    return context
