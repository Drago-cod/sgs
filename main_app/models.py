from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils import timezone
import secrets




class CustomUserManager(UserManager):
    def _create_user(self, email, password, **extra_fields):
        email = self.normalize_email(email)
        user = CustomUser(email=email, **extra_fields)
        user.password = make_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)

        assert extra_fields["is_staff"]
        assert extra_fields["is_superuser"]
        return self._create_user(email, password, **extra_fields)


class Session(models.Model):
    TERM_CHOICES = (
        ("Term One", "Term One"),
        ("Term Two", "Term Two"),
        ("Term Three", "Term Three"),
    )
    term_name = models.CharField(
        max_length=20,
        choices=TERM_CHOICES,
        default="Term One",
        blank=True,
    )
    start_year = models.DateField()
    end_year = models.DateField()

    def __str__(self):
        if self.term_name:
            return f"{self.term_name} ({self.start_year} to {self.end_year})"
        return "From " + str(self.start_year) + " to " + str(self.end_year)


class CustomUser(AbstractUser):
    USER_TYPE = (
        ("1", "Principal / Administrator"),
        ("2", "Subject Teacher"),
        ("3", "Student"),
        ("4", "Director of Studies (DOS)"),
        ("5", "Bursar / Accountant"),
        ("6", "Class Teacher"),
        ("7", "Parent / Guardian"),
    )
    GENDER = [("M", "Male"), ("F", "Female")]
    
    
    username = None  # Removed username, using email instead
    email = models.EmailField(unique=True)
    user_type = models.CharField(default="1", choices=USER_TYPE, max_length=1)
    gender = models.CharField(max_length=1, choices=GENDER)
    profile_pic = models.ImageField(upload_to='profile_pics/', blank=True, default='')
    address = models.TextField()
    fcm_token = models.TextField(default="")  # For firebase notifications
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = CustomUserManager()

    def __str__(self):
        return self.last_name + ", " + self.first_name


class Admin(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)


class DirectorOfStudies(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.admin.get_full_name() or self.admin.email


class Bursar(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.admin.get_full_name() or self.admin.email



class Course(models.Model):
    name = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Student(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.DO_NOTHING, null=True, blank=False)
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING, null=True)
  
    # My additional field
    phone_number = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.admin.last_name + ", " + self.admin.first_name


class Staff(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, null=True)
    class_teacher = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.admin.last_name + " " + self.admin.first_name


class Subject(models.Model):
    ATTENDANCE_TYPES = (
        ("ACADEMIC", "Academic"),
        ("STUDY_HALL", "Study hall"),
        ("FREE_PERIOD", "Free period"),
        ("DUAL_ENROLLMENT", "Off-campus dual enrollment"),
    )

    name = models.CharField(max_length=120)
    staff = models.ForeignKey(Staff,on_delete=models.CASCADE,)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    period_number = models.PositiveSmallIntegerField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    attendance_type = models.CharField(
        max_length=20, choices=ATTENDANCE_TYPES, default="ACADEMIC")
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Attendance(models.Model):
    session = models.ForeignKey(Session, on_delete=models.DO_NOTHING)
    subject = models.ForeignKey(Subject, on_delete=models.DO_NOTHING)
    date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class AttendanceReport(models.Model):
    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("PRESENT", "Present"),
        ("TARDY", "Tardy"),
        ("ABSENT", "Absent"),
        ("EXCUSED", "Excused"),
        ("DISMISSED", "Dismissed"),
    )

    student = models.ForeignKey(Student, on_delete=models.DO_NOTHING)
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE)
    status = models.BooleanField(default=False)
    attendance_status = models.CharField(
        max_length=10, choices=STATUS_CHOICES, default="PENDING")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StudentAttendancePermission(models.Model):
    PERMISSION_TYPES = (
        ("EXCUSED", "Excused absence"),
        ("DISMISSED", "Early dismissal"),
        ("SCHOOL_ACTIVITY", "School-sanctioned activity"),
        ("OFF_CAMPUS", "Authorized off-campus activity"),
    )
    APPROVAL_STATUSES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("DENIED", "Denied"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.DateField()
    permission_type = models.CharField(max_length=20, choices=PERMISSION_TYPES)
    approval_status = models.CharField(
        max_length=10, choices=APPROVAL_STATUSES, default="PENDING")
    period_number = models.PositiveSmallIntegerField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    is_all_day = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    authorized_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["student", "date", "active"]),
        ]


class LeaveReportStudent(models.Model):
    LEAVE_CATEGORIES = (
        ('sick', 'Sick Leave'),
        ('personal', 'Personal Leave'),
        ('study', 'Study Leave'),
        ('annual', 'Annual Leave'),
        ('official', 'Official / Administrative Leave'),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    leave_category = models.CharField(
        max_length=20, choices=LEAVE_CATEGORIES, default='personal')
    forwarded_to_admin = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    date = models.CharField(max_length=120, blank=True, default='')
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class LeaveReportStaff(models.Model):
    LEAVE_CATEGORIES = (
        ('annual', 'Annual Leave'),
        ('sick', 'Sick Leave'),
        ('personal', 'Personal Leave'),
        ('study', 'Study Leave'),
        ('official', 'Official / Administrative Leave'),
    )

    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    leave_category = models.CharField(
        max_length=20, choices=LEAVE_CATEGORIES, default='personal')
    forwarded_to_admin = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    date = models.CharField(max_length=120, blank=True, default='')
    message = models.TextField()
    status = models.SmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class FeedbackStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    feedback = models.TextField()
    reply = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStaff(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class NotificationStudent(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class StudentResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    test = models.FloatField(default=0)
    exam = models.FloatField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = str(instance.user_type)
        if role == '1':
            Admin.objects.get_or_create(admin=instance)
        elif role == '2':
            Staff.objects.get_or_create(admin=instance, defaults={'class_teacher': False})
        elif role == '3':
            Student.objects.get_or_create(admin=instance)
        elif role == '4':
            DirectorOfStudies.objects.get_or_create(admin=instance)
        elif role == '5':
            Bursar.objects.get_or_create(admin=instance)
        elif role == '6':
            Staff.objects.get_or_create(admin=instance, defaults={'class_teacher': True})
        elif role == '7':
            ParentGuardian.objects.get_or_create(admin=instance)


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields and set(update_fields) == {'last_login'}:
        return
    role = str(instance.user_type)
    if role == '1' and hasattr(instance, 'admin'):
        instance.admin.save()
    elif role in ('2', '6') and hasattr(instance, 'staff'):
        instance.staff.save()
    elif role == '3' and hasattr(instance, 'student'):
        instance.student.save()
    elif role == '4' and hasattr(instance, 'directorofstudies'):
        instance.directorofstudies.save()
    elif role == '5' and hasattr(instance, 'bursar'):
        instance.bursar.save()
    elif role == '7' and hasattr(instance, 'parentguardian'):
        instance.parentguardian.save()


# Messaging system between students and class teachers
class StudentMessage(models.Model):
    sender = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='sent_messages')
    receiver = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='received_messages')
    subject = models.CharField(max_length=200, default='No Subject')
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    parent_message = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"From {self.sender.get_full_name()} to {self.receiver.get_full_name()} - {self.subject}"


class AttendanceAnomaly(models.Model):
    REVIEW_STATUSES = (
        ("PENDING", "Needs human review"),
        ("REVIEWED", "Reviewed"),
    )
    REVIEW_OUTCOMES = (
        ("NO_ACTION", "No action required"),
        ("RECORDS_CORRECTED", "Attendance records corrected"),
        ("CONFIRMED", "Discrepancy confirmed"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.DateField()
    flagged_period = models.PositiveSmallIntegerField(default=0)
    flag_type = models.CharField(max_length=30)
    confidence_score = models.FloatField()
    summary = models.TextField()
    source_records = models.JSONField(default=list)
    review_status = models.CharField(
        max_length=10, choices=REVIEW_STATUSES, default="PENDING")
    review_outcome = models.CharField(
        max_length=20, choices=REVIEW_OUTCOMES, blank=True)
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "student__admin__last_name", "flagged_period"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "date", "flagged_period"],
                name="unique_attendance_anomaly_per_period"),
        ]


class AttendanceAuditEvent(models.Model):
    EVENT_TYPES = (
        ("DETECTED", "Anomaly detected"),
        ("REVIEWED", "Human review completed"),
    )

    anomaly = models.ForeignKey(
        AttendanceAnomaly, on_delete=models.CASCADE, related_name="audit_events")
    event_type = models.CharField(max_length=10, choices=EVENT_TYPES)
    actor = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class TeacherAttendance(models.Model):
    STATUS_CHOICES = (
        ("PRESENT", "Present"),
        ("LATE", "Late"),
        ("ABSENT", "Absent"),
        ("ON_LEAVE", "On Leave"),
    )
    SOURCE_CHOICES = (
        ("SELF_CHECKIN", "Teacher Check-In"),
        ("ADMIN", "Admin Recorded"),
        ("AUTO_ACTIVITY", "Auto-Tracked (Class Attendance)"),
    )

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default="PRESENT")
    check_in_time = models.TimeField(null=True, blank=True)
    check_out_time = models.TimeField(null=True, blank=True)
    source = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="SELF_CHECKIN")
    notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "staff__admin__last_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "date"],
                name="unique_teacher_attendance_per_day",
            ),
        ]

    def __str__(self):
        return f"{self.staff} - {self.date} ({self.get_status_display()})"


class AIAttendanceNotification(models.Model):
    TARGET_TYPES = (
        ("STUDENT", "Student"),
        ("TEACHER", "Teacher"),
        ("SYSTEM", "School-Wide"),
    )
    SEVERITY_LEVELS = (
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
        ("CRITICAL", "Critical"),
    )
    ALERT_TYPES = (
        ("CHRONIC_ABSENCE", "Chronic Absence Risk"),
        ("CONSECUTIVE_ABSENCE", "Consecutive Absences"),
        ("CLASS_SKIP", "Potential Class Skip"),
        ("TARDINESS_PATTERN", "Frequent Tardiness"),
        ("ATTENDANCE_DROP", "Sudden Attendance Drop"),
        ("TEACHER_ABSENT_UNEXCUSED", "Unexcused Teacher Absence"),
        ("TEACHER_LATE_PATTERN", "Teacher Late Arrival Pattern"),
        ("TEACHER_MISSED_LOGGING", "Teacher Missing Attendance Log"),
        ("DAILY_SUMMARY", "AI Daily Attendance Digest"),
    )

    target_type = models.CharField(max_length=10, choices=TARGET_TYPES)
    student = models.ForeignKey(
        Student, null=True, blank=True, on_delete=models.CASCADE,
        related_name="ai_notifications")
    staff = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.CASCADE,
        related_name="ai_notifications")
    alert_type = models.CharField(max_length=30, choices=ALERT_TYPES)
    severity = models.CharField(
        max_length=10, choices=SEVERITY_LEVELS, default="MEDIUM")
    risk_score = models.FloatField(default=0.0)
    confidence = models.FloatField(default=0.85)
    title = models.CharField(max_length=200)
    message = models.TextField()
    recommendation = models.TextField(blank=True, default="")
    metrics = models.JSONField(default=dict)
    reference_date = models.DateField()
    is_read = models.BooleanField(default=False)
    is_resolved = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="resolved_ai_notifications")
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_read", "is_resolved", "-created_at"]),
            models.Index(fields=["target_type", "reference_date"]),
        ]

    def __str__(self):
        return f"[{self.severity}] {self.title} ({self.reference_date})"


# Last additional model.
class SMSLog(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    message = models.TextField()
    status = models.CharField(max_length=20)
    error_message = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"SMS to {self.student.admin.first_name} - {self.status}"


class Examination(models.Model):
    STATUS_CHOICES = (
        ("UPCOMING", "Upcoming"),
        ("ONGOING", "Ongoing"),
        ("COMPLETED", "Completed"),
        ("PUBLISHED", "Results Published"),
    )

    name = models.CharField(max_length=150)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, null=True, blank=True,
        help_text="Leave blank for school-wide exam")
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="UPCOMING")
    description = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "name"]

    def __str__(self):
        return f"{self.name} ({self.course.name if self.course else 'All Classes'})"


class TimetableEntry(models.Model):
    DAY_CHOICES = (
        ("MON", "Monday"),
        ("TUE", "Tuesday"),
        ("WED", "Wednesday"),
        ("THU", "Thursday"),
        ("FRI", "Friday"),
        ("SAT", "Saturday"),
    )

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="timetable_entries")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="timetable_entries")
    day_of_week = models.CharField(max_length=3, choices=DAY_CHOICES)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.CharField(max_length=60, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_of_week", "start_time"]

    def __str__(self):
        return f"{self.course.name} · {self.get_day_of_week_display()} {self.start_time} - {self.subject.name}"


class Assignment(models.Model):
    title = models.CharField(max_length=200)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assignments")
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE, related_name="assignments")
    topic = models.CharField(max_length=100, blank=True, default="")
    description = models.TextField()
    attachment = models.FileField(
        upload_to="assignments/materials/", blank=True, null=True
    )
    due_date = models.DateField()
    max_score = models.FloatField(default=100.0)
    allow_late_submission = models.BooleanField(default=True)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-due_date", "-created_at"]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class AssignmentSubmission(models.Model):
    STATUS_CHOICES = (
        ("DRAFT", "Draft Saved"),
        ("TURNED_IN", "Turned In"),
        ("LATE", "Turned In Late"),
        ("GRADED", "Graded & Returned"),
        ("RETURNED", "Returned for Revision"),
        ("REJECTED_AI", "Rejected (AI / Copy-Paste Detected)"),
    )

    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="submissions"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="assignment_submissions"
    )
    document_title = models.CharField(max_length=200, blank=True, default="")
    document_content = models.TextField(blank=True, default="")
    uploaded_file = models.FileField(
        upload_to="assignments/submissions/", blank=True, null=True
    )
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="DRAFT")
    turned_in_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)
    teacher_feedback = models.TextField(blank=True, default="")
    ai_score = models.FloatField(default=0.0)
    ai_flagged = models.BooleanField(default=False)
    ai_report = models.JSONField(default=dict)
    paste_ratio = models.FloatField(default=0.0)
    ai_rejection_count = models.PositiveSmallIntegerField(default=0)
    graded_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="graded_submissions"
    )
    graded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-turned_in_at", "-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student"],
                name="unique_submission_per_student_assignment",
            ),
        ]

    def __str__(self):
        return f"{self.student} - {self.assignment.title} ({self.get_status_display()})"


class AssignmentComment(models.Model):
    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="comments"
    )
    submission = models.ForeignKey(
        AssignmentSubmission, null=True, blank=True, on_delete=models.CASCADE,
        related_name="private_comments"
    )
    author = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    body = models.TextField()
    is_read_by_recipient = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        scope = "Private" if self.submission_id else "Class"
        return f"[{scope}] {self.author}: {self.body[:40]}"


class StudentWelfareRecord(models.Model):
    CATEGORY_CHOICES = (
        ("ACADEMIC", "Academic Support"),
        ("COUNSELING", "Guidance & Counseling"),
        ("HEALTH", "Health & Medical"),
        ("DISCIPLINE", "Conduct & Discipline"),
        ("ATTENDANCE", "Attendance Follow-up"),
        ("RECOGNITION", "Merit / Recognition"),
    )
    STATUS_CHOICES = (
        ("OPEN", "Open"),
        ("IN_PROGRESS", "In Progress"),
        ("RESOLVED", "Resolved"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="welfare_records")
    recorded_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL, related_name="welfare_records")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default="COUNSELING")
    summary = models.TextField()
    action_taken = models.TextField(blank=True, default="")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="OPEN")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} - {self.get_category_display()} ({self.status})"


class FeeStructure(models.Model):
    CURRENCY_CHOICES = (
        ("SSP", "SSP (South Sudanese Pounds)"),
        ("USD", "USD (US Dollar)"),
    )

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="fee_structures")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="fee_structures")
    title = models.CharField(max_length=150, default="Term Tuition & School Fees")
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default="SSP")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.course.name} - {self.title} ({self.currency} {self.amount})"


class FeePayment(models.Model):
    PAYMENT_METHODS = (
        ("CASH", "Cash"),
        ("BANK_TRANSFER", "Bank Transfer"),
        ("MOBILE_MONEY", "Mobile Money"),
        ("CHEQUE", "Cheque"),
    )
    CURRENCY_CHOICES = FeeStructure.CURRENCY_CHOICES

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="fee_payments")
    fee_structure = models.ForeignKey(
        FeeStructure, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.SET_NULL)
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default="SSP")
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField()
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHODS, default="CASH")
    receipt_number = models.CharField(max_length=40, unique=True)
    reference_code = models.CharField(max_length=80, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL, related_name="recorded_payments")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-payment_date", "-created_at"]

    def __str__(self):
        return f"Receipt {self.receipt_number} - {self.student} ({self.currency} {self.amount_paid})"


class TeacherPerformanceFeedback(models.Model):
    RATING_CHOICES = (
        (5, "5 - Outstanding"),
        (4, "4 - Very Good"),
        (3, "3 - Satisfactory"),
        (2, "2 - Needs Improvement"),
        (1, "1 - Unsatisfactory"),
    )
    CATEGORY_CHOICES = (
        ("OVERALL", "Overall Performance"),
        ("TEACHING_QUALITY", "Teaching Quality & Lesson Delivery"),
        ("ATTENDANCE_PUNCTUALITY", "Attendance & Punctuality"),
        ("STUDENT_ENGAGEMENT", "Student Engagement"),
        ("ASSESSMENT_GRADING", "Assessment & Grading Timeliness"),
        ("CLASS_MANAGEMENT", "Classroom Management"),
    )

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="performance_feedbacks")
    reviewer = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="given_teacher_feedbacks")
    session = models.ForeignKey(
        Session, null=True, blank=True, on_delete=models.SET_NULL)
    category = models.CharField(
        max_length=30, choices=CATEGORY_CHOICES, default="OVERALL")
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES, default=4)
    strengths = models.TextField()
    areas_for_improvement = models.TextField(blank=True, default="")
    recommendations = models.TextField(blank=True, default="")
    teacher_response = models.TextField(blank=True, default="")
    acknowledged_by_teacher = models.BooleanField(default=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Performance Review: {self.staff} ({self.rating}/5)"


# ============================================================================
# ADVANCED eLEARNING & VIRTUAL CLASSROOM MODULE (Integrated into SGS)
# ============================================================================

def _generate_room_code():
    return "jass-" + secrets.token_hex(6)


def _generate_access_token():
    return secrets.token_urlsafe(24)


class ParentGuardian(models.Model):
    admin = models.OneToOneField(CustomUser, on_delete=models.CASCADE)
    students = models.ManyToManyField(
        Student, blank=True, related_name="guardians"
    )
    phone_number = models.CharField(max_length=20, blank=True, default="")
    relationship = models.CharField(max_length=40, blank=True, default="Parent / Guardian")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.admin.get_full_name() or self.admin.email


class LearningTopic(models.Model):
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="learning_topics"
    )
    created_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="created_topics"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    learning_objectives = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=1)
    is_published = models.BooleanField(default=True)
    available_from = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "created_at"]
        indexes = [
            models.Index(fields=["subject", "order"]),
        ]

    def __str__(self):
        return f"{self.subject.name} — {self.title}"


class LearningMaterial(models.Model):
    MATERIAL_TYPES = (
        ("PDF", "PDF Document"),
        ("DOC", "Word Document"),
        ("PPT", "Presentation (PPT/Slides)"),
        ("VIDEO", "Video Lesson"),
        ("AUDIO", "Audio Recording"),
        ("IMAGE", "Diagram / Image"),
        ("ARCHIVE", "ZIP Resource Pack"),
        ("NOTES", "Structured Lecture Notes"),
        ("LINK", "External Study Link"),
    )

    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="learning_materials"
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="materials"
    )
    uploaded_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="uploaded_materials"
    )
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True, default="")
    material_type = models.CharField(
        max_length=12, choices=MATERIAL_TYPES, default="PDF"
    )
    file = models.FileField(
        upload_to="elearning/materials/", blank=True, null=True
    )
    external_url = models.URLField(blank=True, default="")
    rich_content = models.TextField(blank=True, default="")
    file_size_bytes = models.BigIntegerField(default=0)
    allow_download = models.BooleanField(default=True)
    is_published = models.BooleanField(default=True)
    publish_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subject", "is_published"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class MaterialAccessLog(models.Model):
    material = models.ForeignKey(
        LearningMaterial, on_delete=models.CASCADE, related_name="access_logs"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="material_access_logs"
    )
    access_count = models.PositiveIntegerField(default=1)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    first_accessed_at = models.DateTimeField(auto_now_add=True)
    last_accessed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["material", "student"],
                name="unique_material_access_per_student",
            ),
        ]

    def __str__(self):
        return f"{self.student} -> {self.material.title}"


class SubjectAnnouncement(models.Model):
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="announcements"
    )
    author = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="subject_announcements"
    )
    title = models.CharField(max_length=200)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self):
        return f"[{self.subject.name}] {self.title}"


class DiscussionThread(models.Model):
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="discussion_threads"
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="discussion_threads"
    )
    author = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="discussion_threads"
    )
    title = models.CharField(max_length=220)
    body = models.TextField()
    is_pinned = models.BooleanField(default=False)
    is_closed = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-updated_at"]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class DiscussionReply(models.Model):
    thread = models.ForeignKey(
        DiscussionThread, on_delete=models.CASCADE, related_name="replies"
    )
    author = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="discussion_replies"
    )
    body = models.TextField()
    is_teacher_endorsed = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Reply by {self.author} on {self.thread.title}"


class VirtualClassroom(models.Model):
    AUDIENCE_CHOICES = (
        ("STUDENTS", "Student Virtual Class"),
        ("STAFF", "Staff / Faculty Meeting"),
    )
    STATUS_CHOICES = (
        ("SCHEDULED", "Scheduled"),
        ("LIVE", "Live Now"),
        ("ENDED", "Ended"),
        ("CANCELLED", "Cancelled"),
    )
    RECURRENCE_CHOICES = (
        ("NONE", "One-Time Lesson"),
        ("DAILY", "Daily"),
        ("WEEKLY", "Weekly"),
        ("BIWEEKLY", "Every Two Weeks"),
    )

    audience = models.CharField(
        max_length=12, choices=AUDIENCE_CHOICES, default="STUDENTS"
    )
    title = models.CharField(max_length=220)
    host_user = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="hosted_virtual_meetings"
    )
    subject = models.ForeignKey(
        Subject, null=True, blank=True, on_delete=models.CASCADE,
        related_name="virtual_classrooms"
    )
    course = models.ForeignKey(
        Course, null=True, blank=True, on_delete=models.CASCADE,
        related_name="virtual_classrooms"
    )
    session = models.ForeignKey(
        Session, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="virtual_classrooms"
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="virtual_classrooms"
    )
    teacher = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.CASCADE,
        related_name="virtual_classrooms"
    )
    academic_year = models.CharField(max_length=20, default="2026")
    stream = models.CharField(max_length=40, blank=True, default="")
    description = models.TextField(blank=True, default="")
    learning_objectives = models.TextField(blank=True, default="")
    supporting_material = models.FileField(
        upload_to="elearning/classrooms/", blank=True, null=True
    )
    scheduled_start = models.DateTimeField(default=timezone.now)
    duration_minutes = models.PositiveIntegerField(default=45)
    recurrence = models.CharField(
        max_length=12, choices=RECURRENCE_CHOICES, default="NONE"
    )
    is_instant = models.BooleanField(default=False)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default="SCHEDULED"
    )
    room_code = models.CharField(
        max_length=64, unique=True, default=_generate_room_code
    )
    access_token = models.CharField(
        max_length=64, default=_generate_access_token
    )
    is_locked = models.BooleanField(default=False)
    waiting_room_enabled = models.BooleanField(default=True)
    allow_student_chat = models.BooleanField(default=True)
    allow_student_whiteboard = models.BooleanField(default=False)
    mute_on_entry = models.BooleanField(default=True)
    attendance_threshold_percent = models.PositiveSmallIntegerField(default=60)
    late_grace_minutes = models.PositiveSmallIntegerField(default=10)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    attendance_synced = models.BooleanField(default=False)
    synced_attendance = models.ForeignKey(
        Attendance, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="virtual_classrooms"
    )
    whiteboard_data = models.TextField(blank=True, default="")
    invited_students = models.ManyToManyField(
        Student, blank=True, related_name="invited_virtual_classrooms"
    )
    invited_staff = models.ManyToManyField(
        Staff, blank=True, related_name="invited_staff_meetings"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-scheduled_start"]
        indexes = [
            models.Index(fields=["course", "status", "-scheduled_start"]),
            models.Index(fields=["teacher", "status"]),
            models.Index(fields=["audience", "status", "-scheduled_start"]),
        ]

    @property
    def is_staff_meeting(self):
        return self.audience == "STAFF"

    @property
    def host_display_name(self):
        if self.host_user_id:
            return self.host_user.get_full_name() or self.host_user.email
        if self.teacher_id:
            return self.teacher.admin.get_full_name() or self.teacher.admin.email
        return "School Administration"

    def __str__(self):
        if self.audience == "STAFF":
            return f"{self.title} (Staff Meeting)"
        course_name = self.course.name if self.course_id else "Class"
        subj_name = self.subject.name if self.subject_id else "Subject"
        return f"{self.title} ({course_name} · {subj_name})"


class VirtualClassroomMessage(models.Model):
    classroom = models.ForeignKey(
        VirtualClassroom, on_delete=models.CASCADE, related_name="chat_messages"
    )
    sender = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    message = models.TextField()
    is_question = models.BooleanField(default=False)
    is_answered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender}: {self.message[:40]}"


class VirtualAttendanceRecord(models.Model):
    STATUS_CHOICES = (
        ("PRESENT", "Present"),
        ("LATE", "Late"),
        ("PARTIAL", "Partial Attendance"),
        ("ABSENT", "Absent"),
        ("EXCUSED", "Excused"),
        ("PENDING", "Pending Verification"),
    )

    classroom = models.ForeignKey(
        VirtualClassroom, on_delete=models.CASCADE, related_name="attendance_records"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="virtual_attendance_records"
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    active_seconds = models.PositiveIntegerField(default=0)
    heartbeat_count = models.PositiveIntegerField(default=0)
    reconnect_count = models.PositiveIntegerField(default=0)
    hand_raised = models.BooleanField(default=False)
    is_removed = models.BooleanField(default=False)
    is_muted_by_host = models.BooleanField(default=False)
    participation_percent = models.FloatField(default=0.0)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default="PENDING"
    )
    teacher_override = models.BooleanField(default=False)
    override_note = models.TextField(blank=True, default="")
    audit_log = models.JSONField(default=list)
    synced_report = models.ForeignKey(
        AttendanceReport, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="virtual_records"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__admin__last_name", "student__admin__first_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["classroom", "student"],
                name="unique_virtual_attendance_per_classroom_student",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.classroom.title} ({self.status})"


class LectureRecording(models.Model):
    STATUS_CHOICES = (
        ("RECORDING", "Recording"),
        ("PROCESSING", "Processing"),
        ("READY", "Ready"),
        ("ARCHIVED", "Archived"),
    )

    classroom = models.ForeignKey(
        VirtualClassroom, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="recordings"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="lecture_recordings"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="lecture_recordings"
    )
    teacher = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="lecture_recordings"
    )
    session = models.ForeignKey(
        Session, null=True, blank=True, on_delete=models.SET_NULL
    )
    academic_year = models.CharField(max_length=20, default="2026")
    title = models.CharField(max_length=220)
    description = models.TextField(blank=True, default="")
    video_file = models.FileField(
        upload_to="elearning/recordings/", blank=True, null=True
    )
    external_video_url = models.URLField(blank=True, default="")
    thumbnail = models.ImageField(
        upload_to="elearning/thumbnails/", blank=True, null=True
    )
    duration_seconds = models.PositiveIntegerField(default=0)
    file_size_bytes = models.BigIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default="READY"
    )
    is_published = models.BooleanField(default=False)
    allow_download = models.BooleanField(default=False)
    retention_until = models.DateField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    recorded_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class OnlineAssessment(models.Model):
    ASSESSMENT_TYPES = (
        ("QUIZ", "Quiz"),
        ("CLASS_TEST", "Class Test"),
        ("TOPIC_TEST", "Topic Assessment"),
        ("TIMED_EXAM", "Timed Examination"),
        ("REVISION", "Revision Test"),
        ("PRACTICE", "Practice Examination"),
    )

    title = models.CharField(max_length=220)
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="online_assessments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="online_assessments"
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="online_assessments"
    )
    session = models.ForeignKey(
        Session, null=True, blank=True, on_delete=models.SET_NULL
    )
    teacher = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="online_assessments"
    )
    assessment_type = models.CharField(
        max_length=15, choices=ASSESSMENT_TYPES, default="QUIZ"
    )
    instructions = models.TextField(blank=True, default="")
    duration_minutes = models.PositiveIntegerField(default=30)
    total_marks = models.FloatField(default=0.0)
    pass_marks = models.FloatField(default=50.0)
    available_from = models.DateTimeField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    max_attempts = models.PositiveSmallIntegerField(default=1)
    randomize_questions = models.BooleanField(default=False)
    randomize_choices = models.BooleanField(default=False)
    detect_tab_switch = models.BooleanField(default=True)
    allow_review = models.BooleanField(default=True)
    is_published = models.BooleanField(default=False)
    results_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["course", "is_published"]),
            models.Index(fields=["subject", "is_published"]),
        ]

    def recalculate_total_marks(self):
        total = sum(q.marks for q in self.questions.all())
        self.total_marks = round(total, 2)
        self.save(update_fields=["total_marks"])
        return self.total_marks

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


class QuestionBankItem(models.Model):
    QUESTION_TYPES = (
        ("MCQ", "Multiple Choice"),
        ("MULTI_SELECT", "Multiple Select"),
        ("TRUE_FALSE", "True / False"),
        ("SHORT_ANSWER", "Short Answer"),
        ("LONG_ANSWER", "Long Answer"),
        ("FILL_BLANK", "Fill in the Blanks"),
        ("MATCHING", "Matching"),
        ("ESSAY", "Essay"),
        ("FILE_UPLOAD", "File Upload"),
    )
    DIFFICULTY_CHOICES = (
        ("EASY", "Easy"),
        ("MEDIUM", "Medium"),
        ("HARD", "Hard"),
    )

    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="question_bank"
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="question_bank"
    )
    assessment = models.ForeignKey(
        OnlineAssessment, null=True, blank=True, on_delete=models.CASCADE,
        related_name="questions"
    )
    created_by = models.ForeignKey(
        Staff, null=True, blank=True, on_delete=models.SET_NULL
    )
    question_type = models.CharField(
        max_length=15, choices=QUESTION_TYPES, default="MCQ"
    )
    prompt = models.TextField()
    choices = models.JSONField(default=list, blank=True)
    correct_answer = models.JSONField(default=dict, blank=True)
    marking_guide = models.TextField(blank=True, default="")
    marks = models.FloatField(default=2.0)
    order = models.PositiveIntegerField(default=1)
    difficulty = models.CharField(
        max_length=10, choices=DIFFICULTY_CHOICES, default="MEDIUM"
    )
    is_ai_generated = models.BooleanField(default=False)
    teacher_approved = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]

    @property
    def is_objective(self):
        return self.question_type in (
            "MCQ", "MULTI_SELECT", "TRUE_FALSE", "FILL_BLANK", "MATCHING"
        )

    def __str__(self):
        return f"[{self.get_question_type_display()}] {self.prompt[:60]}"


class AssessmentAttempt(models.Model):
    STATUS_CHOICES = (
        ("IN_PROGRESS", "In Progress"),
        ("SUBMITTED", "Submitted (Awaiting Manual Review)"),
        ("GRADED", "Graded"),
        ("TIMED_OUT", "Auto-Submitted (Time Expired)"),
    )

    assessment = models.ForeignKey(
        OnlineAssessment, on_delete=models.CASCADE, related_name="attempts"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="assessment_attempts"
    )
    attempt_number = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(
        max_length=15, choices=STATUS_CHOICES, default="IN_PROGRESS"
    )
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    objective_score = models.FloatField(default=0.0)
    subjective_score = models.FloatField(default=0.0)
    total_score = models.FloatField(default=0.0)
    percentage = models.FloatField(default=0.0)
    passed = models.BooleanField(default=False)
    tab_switch_count = models.PositiveSmallIntegerField(default=0)
    flagged_questions = models.JSONField(default=list, blank=True)
    audit_log = models.JSONField(default=list, blank=True)
    teacher_feedback = models.TextField(blank=True, default="")
    graded_by = models.ForeignKey(
        CustomUser, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="graded_assessment_attempts"
    )
    graded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "student", "attempt_number"],
                name="unique_assessment_attempt_number",
            ),
        ]

    def __str__(self):
        return f"{self.student} — {self.assessment.title} (#{self.attempt_number})"


class AssessmentAnswer(models.Model):
    attempt = models.ForeignKey(
        AssessmentAttempt, on_delete=models.CASCADE, related_name="answers"
    )
    question = models.ForeignKey(
        QuestionBankItem, on_delete=models.CASCADE, related_name="student_answers"
    )
    answer_data = models.JSONField(default=dict, blank=True)
    text_answer = models.TextField(blank=True, default="")
    uploaded_file = models.FileField(
        upload_to="elearning/assessment_uploads/", blank=True, null=True
    )
    is_correct = models.BooleanField(null=True, blank=True)
    marks_awarded = models.FloatField(null=True, blank=True)
    teacher_comment = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"],
                name="unique_answer_per_attempt_question",
            ),
        ]

    def __str__(self):
        return f"Answer Q{self.question_id} for Attempt {self.attempt_id}"


class AILearningInteraction(models.Model):
    MODE_CHOICES = (
        ("ASK", "Subject Question & Explanation"),
        ("SUMMARIZE", "Material Summary"),
        ("REVISION_NOTES", "Revision Notes"),
        ("PRACTICE_QUESTIONS", "Practice Questions"),
        ("STUDY_PLAN", "Personalized Revision Schedule"),
        ("LESSON_PLAN", "Teacher Lesson Plan & Objectives"),
        ("GENERATE_QUIZ", "Teacher Quiz Draft (Requires Review)"),
        ("GENERATE_ASSIGNMENT", "Teacher Assignment & Rubric Draft"),
        ("CLASS_ANALYTICS", "Class Performance Insights"),
    )

    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="ai_learning_interactions"
    )
    subject = models.ForeignKey(
        Subject, null=True, blank=True, on_delete=models.SET_NULL
    )
    topic = models.ForeignKey(
        LearningTopic, null=True, blank=True, on_delete=models.SET_NULL
    )
    mode = models.CharField(max_length=25, choices=MODE_CHOICES, default="ASK")
    prompt = models.TextField()
    response = models.TextField()
    structured_payload = models.JSONField(default=dict, blank=True)
    requires_teacher_review = models.BooleanField(default=False)
    teacher_approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.mode}] {self.user} ({self.created_at:%Y-%m-%d})"
