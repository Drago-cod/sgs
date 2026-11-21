from .models import AIAttendanceNotification


def ai_attendance_alerts(request):
    """Provide unread AI attendance notifications to Admin templates (navbar & sidebar)."""
    user = getattr(request, "user", None)
    if user and user.is_authenticated and getattr(user, "user_type", None) == "1":
        unread_qs = AIAttendanceNotification.objects.filter(
            is_read=False, is_resolved=False
        ).order_by("-created_at")
        return {
            "ai_unread_count": unread_qs.count(),
            "ai_recent_alerts": list(unread_qs[:6]),
        }
    return {
        "ai_unread_count": 0,
        "ai_recent_alerts": [],
    }
