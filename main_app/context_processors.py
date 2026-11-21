from django.urls import reverse

from .middleware import ROLE_HOME_URLS
from .models import AIAttendanceNotification
from .notifications import get_user_notifications

ROLE_PROFILE_URLS = {
    "1": "admin_view_profile",
    "2": "staff_view_profile",
    "3": "student_view_profile",
    "4": "dos_view_profile",
    "5": "bursar_view_profile",
    "6": "staff_view_profile",
}


def ai_attendance_alerts(request):
    """Provide role-aware actionable notifications and navigation links to all templates."""
    user = getattr(request, "user", None)
    context = {
        "ai_unread_count": 0,
        "ai_recent_alerts": [],
        "nav_notifications": [],
        "nav_unread_count": 0,
        "user_home_url": reverse("login_page"),
        "user_profile_url": reverse("login_page"),
    }
    if user and user.is_authenticated:
        role = str(getattr(user, "user_type", ""))
        context["user_home_url"] = reverse(ROLE_HOME_URLS.get(role, "login_page"))
        context["user_profile_url"] = reverse(ROLE_PROFILE_URLS.get(role, "login_page"))

        all_notifs = get_user_notifications(user, limit=25)
        context["nav_notifications"] = all_notifs[:6]
        context["nav_unread_count"] = sum(1 for n in all_notifs if n["is_unread"])

        if role in ("1", "4"):
            unread_qs = AIAttendanceNotification.objects.filter(
                is_read=False, is_resolved=False
            ).order_by("-created_at")
            context["ai_unread_count"] = unread_qs.count()
            context["ai_recent_alerts"] = list(unread_qs[:6])
    return context
