import json
import requests
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone

from .EmailBackend import EmailBackend
from .middleware import get_user_home_url
from .models import (
    AIAttendanceNotification,
    Attendance,
    AttendanceAnomaly,
    AttendanceAuditEvent,
    CustomUser,
    Examination,
    FeedbackStaff,
    FeedbackStudent,
    LeaveReportStaff,
    LeaveReportStudent,
    NotificationStaff,
    NotificationStudent,
    Session,
    Staff,
    Student,
    StudentMessage,
    StudentWelfareRecord,
    Subject,
    TeacherPerformanceFeedback,
)
from .notifications import get_user_notifications
from .attendance_audit import dataset_from_request_body, evaluate_attendance_dataset
from django.utils.dateparse import parse_date

# Create your views here.


def login_page(request):
    if request.user.is_authenticated:
        return redirect(get_user_home_url(request.user))
    return render(request, 'main_app/login.html')


def doLogin(request, **kwargs):
    if request.method != 'POST':
        return HttpResponse("<h4>Denied</h4>")
    else:
        # Authenticate
        user = EmailBackend.authenticate(request, username=request.POST.get('email'), password=request.POST.get('password'))
        if user != None:
            login(request, user)
            return redirect(get_user_home_url(user))
        else:
            messages.error(request, "Invalid details")
            return redirect("/")


def logout_user(request):
    if request.user != None:
        logout(request)
    return redirect("/")


@login_required
def evaluate_attendance_audit(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST is required."}, status=405)
    if request.user.user_type != "1":
        return JsonResponse({"error": "Administrator access is required."}, status=403)
    dataset = dataset_from_request_body(request.body)
    if dataset is None:
        return JsonResponse({"error": "Attendance dataset is invalid."}, status=400)
    result = evaluate_attendance_dataset(dataset)
    student_data = dataset["student"]
    student_id = student_data.get("id")
    audit_date = parse_date(dataset["date"])
    if result["anomaly_detected"] and audit_date:
        student = Student.objects.filter(admin_id=student_id).first()
        if student:
            period_key = result["flagged_period"] or 0
            anomaly, created = AttendanceAnomaly.objects.get_or_create(
                student=student,
                date=audit_date,
                flagged_period=period_key,
                defaults={
                    "flag_type": result["flag_type"],
                    "confidence_score": result["confidence_score"],
                    "summary": result["summary"],
                    "source_records": dataset["period_records"],
                },
            )
            if created:
                AttendanceAuditEvent.objects.create(
                    anomaly=anomaly,
                    event_type="DETECTED",
                    actor=request.user,
                    details={
                        "flag_type": result["flag_type"],
                        "confidence_score": result["confidence_score"],
                        "source_records": dataset["period_records"],
                    },
                )
    return JsonResponse(result)


@csrf_exempt
def get_attendance(request):
    subject_id = request.POST.get('subject')
    session_id = request.POST.get('session')
    try:
        subject = get_object_or_404(Subject, id=subject_id)
        session = get_object_or_404(Session, id=session_id)
        attendance = Attendance.objects.filter(subject=subject, session=session)
        attendance_list = []
        for attd in attendance:
            data = {
                    "id": attd.id,
                    "attendance_date": str(attd.date),
                    "session": attd.session.id
                    }
            attendance_list.append(data)
        return JsonResponse(json.dumps(attendance_list), safe=False)
    except Exception as e:
        return None


def showFirebaseJS(request):
    data = """
    // Give the service worker access to Firebase Messaging.
// Note that you can only use Firebase Messaging here, other Firebase libraries
// are not available in the service worker.
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-app.js');
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-messaging.js');

// Initialize the Firebase app in the service worker by passing in
// your app's Firebase config object.
// https://firebase.google.com/docs/web/setup#config-object
firebase.initializeApp({
    apiKey: "AIzaSyBarDWWHTfTMSrtc5Lj3Cdw5dEvjAkFwtM",
    authDomain: "sms-with-django.firebaseapp.com",
    databaseURL: "https://sms-with-django.firebaseio.com",
    projectId: "sms-with-django",
    storageBucket: "sms-with-django.appspot.com",
    messagingSenderId: "945324593139",
    appId: "1:945324593139:web:03fa99a8854bbd38420c86",
    measurementId: "G-2F2RXTL9GT"
});

// Retrieve an instance of Firebase Messaging so that it can handle background
// messages.
const messaging = firebase.messaging();
messaging.setBackgroundMessageHandler(function (payload) {
    const notification = JSON.parse(payload);
    const notificationOption = {
        body: notification.body,
        icon: notification.icon
    }
    return self.registration.showNotification(payload.notification.title, notificationOption);
});
    """
    return HttpResponse(data, content_type='application/javascript')


@login_required
def notification_center(request):
    """Unified actionable Notification Center for all 6 user roles."""
    filter_mode = request.GET.get("filter", "all").lower()
    notifications = get_user_notifications(request.user, limit=50)
    unread_count = sum(1 for n in notifications if n["is_unread"])
    action_count = sum(1 for n in notifications if n["needs_action"])

    if filter_mode == "unread":
        filtered = [n for n in notifications if n["is_unread"]]
    elif filter_mode == "action":
        filtered = [n for n in notifications if n["needs_action"]]
    else:
        filtered = notifications

    context = {
        "page_title": "Notification Center",
        "notifications": filtered,
        "total_count": len(notifications),
        "unread_count": unread_count,
        "action_count": action_count,
        "filter_mode": filter_mode,
        "exam_statuses": Examination.STATUS_CHOICES,
        "welfare_statuses": StudentWelfareRecord.STATUS_CHOICES,
    }
    return render(request, "main_app/notification_center.html", context)


@login_required
@require_POST
def notification_quick_respond(request):
    """Handle 1-click inline responses directly from the Notification Center."""
    action = request.POST.get("action", "").strip()
    item_id = request.POST.get("item_id")
    role = str(getattr(request.user, "user_type", ""))
    next_url = request.POST.get("next") or reverse("notification_center")

    if action == "mark_all_read":
        if role in ("1", "4"):
            AIAttendanceNotification.objects.filter(is_read=False).update(is_read=True)
        elif role in ("2", "6"):
            staff = Staff.objects.filter(admin=request.user).first()
            if staff:
                NotificationStaff.objects.filter(staff=staff, is_read=False).update(is_read=True)
        elif role == "3":
            student = Student.objects.filter(admin=request.user).first()
            if student:
                NotificationStudent.objects.filter(student=student, is_read=False).update(is_read=True)
        StudentMessage.objects.filter(receiver=request.user, is_read=False).update(is_read=True)
        messages.success(request, "All unread notifications marked as read.")
        return redirect(next_url)

    if action == "read_ai_alert" and role in ("1", "4"):
        alert = get_object_or_404(AIAttendanceNotification, id=item_id)
        alert.is_read = True
        alert.save(update_fields=["is_read", "updated_at"])
        messages.success(request, f"Marked alert as read: {alert.title}")

    elif action == "resolve_ai_alert" and role in ("1", "4"):
        alert = get_object_or_404(AIAttendanceNotification, id=item_id)
        alert.is_read = True
        alert.is_resolved = True
        alert.resolved_by = request.user
        alert.resolved_at = timezone.now()
        alert.save(update_fields=["is_read", "is_resolved", "resolved_by", "resolved_at", "updated_at"])
        messages.success(request, f"Resolved AI alert: {alert.title}")

    elif action == "decide_staff_leave" and role == "1":
        leave = get_object_or_404(LeaveReportStaff, id=item_id)
        decision = request.POST.get("decision")
        leave.status = 1 if decision == "approve" else -1
        leave.save(update_fields=["status", "updated_at"])
        label = "Approved" if leave.status == 1 else "Rejected"
        messages.success(request, f"{label} leave request for {leave.staff.admin.get_full_name()}.")

    elif action == "decide_student_leave" and role in ("1", "2", "6"):
        leave = get_object_or_404(LeaveReportStudent, id=item_id)
        decision = request.POST.get("decision")
        allowed = role == "1"
        if role in ("2", "6"):
            staff = Staff.objects.filter(admin=request.user).first()
            allowed = bool(staff and staff.class_teacher and leave.student.course_id == staff.course_id)
        if not allowed:
            messages.error(request, "You are not authorized to respond to this leave request.")
        elif decision == "forward":
            leave.status = 0
            leave.forwarded_to_admin = True
            leave.save(update_fields=["status", "forwarded_to_admin", "updated_at"])
            messages.success(request, f"Forwarded {leave.student.admin.get_full_name()}'s leave request to Principal.")
        else:
            leave.status = 1 if decision == "approve" else -1
            leave.forwarded_to_admin = False
            leave.save(update_fields=["status", "forwarded_to_admin", "updated_at"])
            label = "Approved" if leave.status == 1 else "Rejected"
            messages.success(request, f"{label} leave request for {leave.student.admin.get_full_name()}.")

    elif action == "reply_staff_feedback" and role == "1":
        fb = get_object_or_404(FeedbackStaff, id=item_id)
        reply_text = request.POST.get("reply", "").strip()
        if not reply_text:
            messages.error(request, "Please enter a reply message.")
        else:
            fb.reply = reply_text
            fb.save(update_fields=["reply", "updated_at"])
            messages.success(request, f"Reply sent to {fb.staff.admin.get_full_name()}.")

    elif action == "reply_student_feedback" and role == "1":
        fb = get_object_or_404(FeedbackStudent, id=item_id)
        reply_text = request.POST.get("reply", "").strip()
        if not reply_text:
            messages.error(request, "Please enter a reply message.")
        else:
            fb.reply = reply_text
            fb.save(update_fields=["reply", "updated_at"])
            messages.success(request, f"Reply sent to {fb.student.admin.get_full_name()}.")

    elif action == "reply_message":
        original = get_object_or_404(StudentMessage, id=item_id, receiver=request.user)
        reply_text = request.POST.get("reply", "").strip()
        if not reply_text:
            messages.error(request, "Please type a reply before sending.")
        else:
            original.is_read = True
            original.save(update_fields=["is_read"])
            StudentMessage.objects.create(
                sender=request.user,
                receiver=original.sender,
                subject=original.subject or "Reply",
                message=reply_text,
                parent_message=original,
            )
            messages.success(request, f"Reply sent to {original.sender.get_full_name() or original.sender.email}!")

    elif action == "update_welfare" and role in ("2", "6"):
        staff = Staff.objects.filter(admin=request.user, class_teacher=True).first()
        if staff:
            record = get_object_or_404(StudentWelfareRecord, id=item_id, student__course=staff.course)
            new_status = request.POST.get("status", "").upper()
            if new_status in {c[0] for c in StudentWelfareRecord.STATUS_CHOICES}:
                record.status = new_status
                note = request.POST.get("action_taken", "").strip()
                if note:
                    record.action_taken = note
                record.save()
                messages.success(request, f"Updated welfare case for {record.student.admin.get_full_name()}.")

    elif action == "update_exam_status" and role in ("1", "4"):
        exam = get_object_or_404(Examination, id=item_id)
        new_status = request.POST.get("status", "").upper()
        if new_status in {c[0] for c in Examination.STATUS_CHOICES}:
            exam.status = new_status
            exam.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Updated '{exam.name}' to {exam.get_status_display()}.")

    elif action == "read_staff_announcement" and role in ("2", "6"):
        staff = Staff.objects.filter(admin=request.user).first()
        if staff:
            notif = get_object_or_404(NotificationStaff, id=item_id, staff=staff)
            notif.is_read = True
            notif.save(update_fields=["is_read", "updated_at"])
            messages.success(request, "Announcement marked as read.")

    elif action == "read_student_announcement" and role == "3":
        student = Student.objects.filter(admin=request.user).first()
        if student:
            notif = get_object_or_404(NotificationStudent, id=item_id, student=student)
            notif.is_read = True
            notif.save(update_fields=["is_read", "updated_at"])
            messages.success(request, "Announcement marked as read.")

    elif action == "respond_teacher_performance" and role in ("2", "6"):
        staff = Staff.objects.filter(admin=request.user).first()
        if staff:
            perf = get_object_or_404(TeacherPerformanceFeedback, id=item_id, staff=staff)
            perf.teacher_response = request.POST.get("teacher_response", "").strip()
            perf.acknowledged_by_teacher = True
            perf.acknowledged_at = timezone.now()
            perf.save(update_fields=["teacher_response", "acknowledged_by_teacher", "acknowledged_at", "updated_at"])
            messages.success(request, "Performance feedback acknowledged and response saved.")

    else:
        messages.error(request, "Unsupported or unauthorized notification action.")

    return redirect(next_url)
