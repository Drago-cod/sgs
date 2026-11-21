from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import UserManager
from django.dispatch import receiver
from django.db.models.signals import post_save
from django.db import models
from django.contrib.auth.models import AbstractUser




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
    start_year = models.DateField()
    end_year = models.DateField()

    def __str__(self):
        return "From " + str(self.start_year) + " to " + str(self.end_year)


class CustomUser(AbstractUser):
    USER_TYPE = (
        ("1", "Principal / Administrator"),
        ("2", "Subject Teacher"),
        ("3", "Student"),
        ("4", "Director of Studies (DOS)"),
        ("5", "Bursar / Accountant"),
        ("6", "Class Teacher"),
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


@receiver(post_save, sender=CustomUser)
def save_user_profile(sender, instance, **kwargs):
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
    description = models.TextField()
    due_date = models.DateField()
    max_score = models.FloatField(default=100.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-due_date", "-created_at"]

    def __str__(self):
        return f"{self.title} ({self.subject.name})"


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
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="fee_structures")
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name="fee_structures")
    title = models.CharField(max_length=150, default="Term Tuition & School Fees")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    due_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.course.name} - {self.title} ({self.amount})"


class FeePayment(models.Model):
    PAYMENT_METHODS = (
        ("CASH", "Cash"),
        ("BANK_TRANSFER", "Bank Transfer"),
        ("MOBILE_MONEY", "Mobile Money"),
        ("CHEQUE", "Cheque"),
    )

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="fee_payments")
    fee_structure = models.ForeignKey(
        FeeStructure, null=True, blank=True, on_delete=models.SET_NULL, related_name="payments")
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.SET_NULL)
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
        return f"Receipt {self.receipt_number} - {self.student} ({self.amount_paid})"