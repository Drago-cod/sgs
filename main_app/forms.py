from django import forms
from django.forms.widgets import DateInput, TextInput

from .models import *


class FormSettings(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(FormSettings, self).__init__(*args, **kwargs)
        # Here make some changes such as:
        for field in self.visible_fields():
            field.field.widget.attrs['class'] = 'form-control'


class CustomUserForm(FormSettings):
    email = forms.EmailField(required=True)
    gender = forms.ChoiceField(choices=[('M', 'Male'), ('F', 'Female')])
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    address = forms.CharField(widget=forms.Textarea)
    password = forms.CharField(widget=forms.PasswordInput)
    widget = {
        'password': forms.PasswordInput(),
    }
    profile_pic = forms.ImageField(required=False)

    def __init__(self, *args, **kwargs):
        super(CustomUserForm, self).__init__(*args, **kwargs)

        if kwargs.get('instance'):
            instance = kwargs.get('instance').admin.__dict__
            self.fields['password'].required = False
            for field in CustomUserForm.Meta.fields:
                self.fields[field].initial = instance.get(field)
            if self.instance.pk is not None:
                self.fields['password'].widget.attrs['placeholder'] = "Fill this only if you wish to update password"

    def clean_email(self, *args, **kwargs):
        formEmail = self.cleaned_data['email'].lower()
        if self.instance.pk is None:  # Insert
            if CustomUser.objects.filter(email=formEmail).exists():
                raise forms.ValidationError(
                    "The given email is already registered")
        else:  # Update
            dbEmail = self.Meta.model.objects.get(
                id=self.instance.pk).admin.email.lower()
            if dbEmail != formEmail:  # There has been changes
                if CustomUser.objects.filter(email=formEmail).exists():
                    raise forms.ValidationError("The given email is already registered")

        return formEmail

    class Meta:
        model = CustomUser
        fields = ['first_name', 'last_name', 'email', 'gender',  'password','profile_pic', 'address' ]


class StudentForm(CustomUserForm):
    phone_number = forms.CharField(label="Phone Number", max_length=15, widget=forms.TextInput(attrs={"class":"form-control"}))
    course = forms.ModelChoiceField(queryset=Course.objects.all(), label="Class", widget=forms.Select(attrs={"class":"form-control"}))
    session = forms.ModelChoiceField(queryset=Session.objects.all(), label="Term", widget=forms.Select(attrs={"class":"form-control"}))
    
    def __init__(self, *args, **kwargs):
        super(StudentForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = Student
        fields = CustomUserForm.Meta.fields + ['course', 'session', 'phone_number']


class AdminForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        super(AdminForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = Admin
        fields = CustomUserForm.Meta.fields


class StaffForm(CustomUserForm):
    STAFF_ROLE_CHOICES = [
        ("2", "Subject Teacher"),
        ("6", "Class Teacher"),
        ("4", "Director of Studies (DOS)"),
        ("5", "Bursar / Accountant"),
        ("1", "Principal / Administrator"),
    ]
    TEACHER_ONLY_ROLE_CHOICES = [
        ("2", "Subject Teacher"),
        ("6", "Class Teacher"),
    ]
    role = forms.ChoiceField(
        choices=STAFF_ROLE_CHOICES,
        initial="2",
        required=True,
        label="User Role",
    )

    def __init__(self, *args, allow_all_roles=True, **kwargs):
        super(StaffForm, self).__init__(*args, **kwargs)
        self.fields['course'].required = False
        self.fields['course'].label = "Classes"
        self.fields['course'].empty_label = "All Classes (School-Wide)"
        self.fields['course'].help_text = (
            "Select a class for teachers. Director of Studies (DOS), Bursar, and Principal automatically oversee all classes."
        )
        if not allow_all_roles:
            self.fields['role'].choices = self.TEACHER_ONLY_ROLE_CHOICES
        if kwargs.get('instance') and getattr(kwargs.get('instance'), 'admin', None):
            self.fields['role'].initial = str(kwargs['instance'].admin.user_type)

    class Meta(CustomUserForm.Meta):
        model = Staff
        fields = CustomUserForm.Meta.fields + ['role', 'course']
        labels = {
            'course': 'Classes',
        }


class DirectorOfStudiesForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        super(DirectorOfStudiesForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = DirectorOfStudies
        fields = CustomUserForm.Meta.fields


class BursarForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        super(BursarForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = Bursar
        fields = CustomUserForm.Meta.fields


class CourseForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(CourseForm, self).__init__(*args, **kwargs)

    class Meta:
        fields = ['name']
        model = Course
        labels = {
            'name': 'Class Name',
        }


class SubjectForm(FormSettings):

    def __init__(self, *args, **kwargs):
        super(SubjectForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Subject
        fields = ['name', 'staff', 'course', 'period_number', 'attendance_type']
        labels = {
            'course': 'Class',
        }


class SessionForm(FormSettings):
    def __init__(self, *args, **kwargs):
        super(SessionForm, self).__init__(*args, **kwargs)
        self.fields['term_name'].required = False
        self.fields['term_name'].choices = Session.TERM_CHOICES
        if not self.initial.get('term_name') and not (self.instance and self.instance.pk):
            self.initial['term_name'] = 'Term One'

    def clean_term_name(self):
        return self.cleaned_data.get('term_name') or 'Term One'

    class Meta:
        model = Session
        fields = ['term_name', 'start_year', 'end_year']
        labels = {
            'term_name': 'Term',
            'start_year': 'Term Start Date',
            'end_year': 'Term End Date',
        }
        widgets = {
            'start_year': DateInput(attrs={'type': 'date'}),
            'end_year': DateInput(attrs={'type': 'date'}),
        }


class LeaveReportStaffForm(FormSettings):
    leave_category = forms.ChoiceField(
        choices=[
            ('annual', 'Annual Leave'),
            ('sick', 'Sick Leave'),
            ('personal', 'Personal Leave'),
            ('study', 'Study Leave'),
            ('maternity', 'Maternity/Paternity Leave'),
        ],
        required=True,
        label='Leave Category *'
    )
    start_date = forms.DateField(
        required=True,
        label='Start Date *',
        widget=DateInput(attrs={'type': 'date'})
    )
    start_time = forms.TimeField(
        required=True,
        label='Start Time *',
        widget=forms.TimeInput(attrs={'type': 'time'})
    )
    end_date = forms.DateField(
        required=True,
        label='End Date *',
        widget=DateInput(attrs={'type': 'date'})
    )
    end_time = forms.TimeField(
        required=True,
        label='End Time *',
        widget=forms.TimeInput(attrs={'type': 'time'})
    )
    message = forms.CharField(
        required=True,
        label='Reason / Justification *',
        widget=forms.Textarea(attrs={
            'rows': 4,
            'placeholder': 'Provide clear reason for leave...'
        })
    )

    def __init__(self, *args, **kwargs):
        super(LeaveReportStaffForm, self).__init__(*args, **kwargs)
        self.fields['leave_category'].widget.attrs.update({'class': 'form-control'})
        self.fields['start_date'].widget.attrs.update({'class': 'form-control'})
        self.fields['start_time'].widget.attrs.update({'class': 'form-control'})
        self.fields['end_date'].widget.attrs.update({'class': 'form-control'})
        self.fields['end_time'].widget.attrs.update({'class': 'form-control'})
        self.fields['message'].widget.attrs.update({'class': 'form-control'})

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        start_time = cleaned_data.get('start_time')
        end_date = cleaned_data.get('end_date')
        end_time = cleaned_data.get('end_time')

        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError('End date cannot be earlier than the start date.')
        if start_date == end_date and start_time and end_time and end_time < start_time:
            raise forms.ValidationError('End time cannot be earlier than the start time on the same day.')
        return cleaned_data

    class Meta:
        model = LeaveReportStaff
        fields = ['leave_category', 'start_date', 'start_time', 'end_date', 'end_time', 'message']


class FeedbackStaffForm(FormSettings):

    def __init__(self, *args, **kwargs):
        super(FeedbackStaffForm, self).__init__(*args, **kwargs)

    class Meta:
        model = FeedbackStaff
        fields = ['feedback']


class LeaveReportStudentForm(FormSettings):
    leave_category = forms.ChoiceField(
        choices=[
            ('annual', 'Annual Leave'),
            ('sick', 'Sick Leave'),
            ('personal', 'Personal Leave'),
            ('study', 'Study Leave'),
            ('official', 'Official / Administrative Leave'),
        ],
        required=True,
        label='Leave Category *'
    )
    start_date = forms.DateField(
        required=True,
        label='Start Date *',
        widget=DateInput(attrs={'type': 'date'})
    )
    start_time = forms.TimeField(
        required=True,
        label='Start Time *',
        widget=forms.TimeInput(attrs={'type': 'time'})
    )
    end_date = forms.DateField(
        required=True,
        label='End Date *',
        widget=DateInput(attrs={'type': 'date'})
    )
    end_time = forms.TimeField(
        required=True,
        label='End Time *',
        widget=forms.TimeInput(attrs={'type': 'time'})
    )
    message = forms.CharField(
        required=True,
        label='Reason / Justification *',
        widget=forms.Textarea(attrs={'rows': 4, 'placeholder': 'Provide clear reason for leave...'}),
    )

    def __init__(self, *args, **kwargs):
        super(LeaveReportStudentForm, self).__init__(*args, **kwargs)
        self.fields['leave_category'].widget.attrs.update({'class': 'form-control'})
        self.fields['start_date'].widget.attrs.update({'class': 'form-control'})
        self.fields['start_time'].widget.attrs.update({'class': 'form-control'})
        self.fields['end_date'].widget.attrs.update({'class': 'form-control'})
        self.fields['end_time'].widget.attrs.update({'class': 'form-control'})
        self.fields['message'].widget.attrs.update({'class': 'form-control'})

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        start_time = cleaned_data.get('start_time')
        end_date = cleaned_data.get('end_date')
        end_time = cleaned_data.get('end_time')

        if start_date and end_date and end_date < start_date:
            raise forms.ValidationError('End date cannot be earlier than the start date.')
        if start_date == end_date and start_time and end_time and end_time < start_time:
            raise forms.ValidationError('End time cannot be earlier than the start time on the same day.')
        return cleaned_data

    class Meta:
        model = LeaveReportStudent
        fields = ['leave_category', 'start_date', 'start_time', 'end_date', 'end_time', 'message']


class FeedbackStudentForm(FormSettings):

    def __init__(self, *args, **kwargs):
        super(FeedbackStudentForm, self).__init__(*args, **kwargs)

    class Meta:
        model = FeedbackStudent
        fields = ['feedback']


class StudentEditForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        super(StudentEditForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = Student
        fields = CustomUserForm.Meta.fields 


class StaffEditForm(CustomUserForm):
    def __init__(self, *args, **kwargs):
        super(StaffEditForm, self).__init__(*args, **kwargs)

    class Meta(CustomUserForm.Meta):
        model = Staff
        fields = CustomUserForm.Meta.fields


class EditResultForm(FormSettings):
    session_list = Session.objects.all()
    session_year = forms.ModelChoiceField(
        label="Term", queryset=session_list, required=True)

    def __init__(self, *args, **kwargs):
        super(EditResultForm, self).__init__(*args, **kwargs)

    class Meta:
        model = StudentResult
        fields = ['session_year', 'subject', 'student', 'test', 'exam']


class ClassTeacherForm(forms.ModelForm):
    class Meta:
        model = Staff
        fields = ['class_teacher']
        widgets = {
            'class_teacher': forms.CheckboxInput(attrs={'class': 'form-check-input'})
        }


class ExaminationForm(FormSettings):
    class Meta:
        model = Examination
        fields = ['name', 'session', 'course', 'start_date', 'end_date', 'status', 'description']
        labels = {
            'session': 'Term',
            'course': 'Class',
        }
        widgets = {
            'start_date': DateInput(attrs={'type': 'date'}),
            'end_date': DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_date')
        end = cleaned.get('end_date')
        if start and end and end < start:
            raise forms.ValidationError("End date cannot be earlier than start date.")
        return cleaned


class TimetableEntryForm(FormSettings):
    class Meta:
        model = TimetableEntry
        fields = ['course', 'subject', 'day_of_week', 'start_time', 'end_time', 'room']
        labels = {
            'course': 'Class',
        }
        widgets = {
            'start_time': forms.TimeInput(attrs={'type': 'time'}),
            'end_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start_time')
        end = cleaned.get('end_time')
        if start and end and end <= start:
            raise forms.ValidationError("End time must be after start time.")
        return cleaned


class AssignmentForm(FormSettings):
    def __init__(self, *args, staff=None, **kwargs):
        super(AssignmentForm, self).__init__(*args, **kwargs)
        if staff is not None:
            self.fields['subject'].queryset = Subject.objects.filter(staff=staff)
        self.fields['allow_late_submission'].widget.attrs['class'] = 'form-check-input'

    class Meta:
        model = Assignment
        fields = [
            'title',
            'subject',
            'topic',
            'description',
            'attachment',
            'due_date',
            'max_score',
            'allow_late_submission',
        ]
        widgets = {
            'topic': forms.TextInput(attrs={'placeholder': 'e.g. Unit 3: Algebra (optional)'}),
            'due_date': DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 4, 'placeholder': 'Instructions, rubric, or questions...'}),
            'allow_late_submission': forms.CheckboxInput(),
        }


class AssignmentSubmissionForm(FormSettings):
    class Meta:
        model = AssignmentSubmission
        fields = ['document_title', 'document_content', 'uploaded_file']
        widgets = {
            'document_title': forms.TextInput(attrs={'placeholder': 'Document Title (e.g. Lab Report - Draft 1)'}),
            'document_content': forms.Textarea(attrs={'rows': 10, 'class': 'form-control summernote-doc'}),
        }


class StudentWelfareForm(FormSettings):
    def __init__(self, *args, course=None, **kwargs):
        super(StudentWelfareForm, self).__init__(*args, **kwargs)
        if course is not None:
            self.fields['student'].queryset = Student.objects.filter(course=course)

    class Meta:
        model = StudentWelfareRecord
        fields = ['student', 'category', 'summary', 'action_taken', 'status']
        widgets = {
            'summary': forms.Textarea(attrs={'rows': 3}),
            'action_taken': forms.Textarea(attrs={'rows': 2}),
        }


class FeeStructureForm(FormSettings):
    class Meta:
        model = FeeStructure
        fields = ['course', 'session', 'title', 'currency', 'amount', 'due_date', 'description']
        labels = {
            'course': 'Class',
            'session': 'Term',
            'currency': 'Currency',
        }
        widgets = {
            'due_date': DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].choices = FeeStructure.CURRENCY_CHOICES
        self.fields['currency'].required = False

    def clean_currency(self):
        return self.cleaned_data.get('currency') or 'SSP'


class FeePaymentForm(FormSettings):
    class Meta:
        model = FeePayment
        fields = [
            'student',
            'fee_structure',
            'currency',
            'amount_paid',
            'payment_date',
            'payment_method',
            'reference_code',
            'notes',
        ]
        labels = {
            'currency': 'Currency',
        }
        widgets = {
            'payment_date': DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['currency'].choices = FeeStructure.CURRENCY_CHOICES
        self.fields['currency'].required = False

    def clean_currency(self):
        currency = self.cleaned_data.get('currency')
        if currency:
            return currency
        fee_structure = self.cleaned_data.get('fee_structure')
        if fee_structure:
            return fee_structure.currency
        return 'SSP'


class TeacherPerformanceFeedbackForm(FormSettings):
    class Meta:
        model = TeacherPerformanceFeedback
        fields = [
            'staff',
            'session',
            'category',
            'rating',
            'strengths',
            'areas_for_improvement',
            'recommendations',
        ]
        labels = {
            'session': 'Term',
        }
        widgets = {
            'strengths': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Key strengths observed...'}),
            'areas_for_improvement': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Areas needing improvement (optional)...'}),
            'recommendations': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Actionable next steps (optional)...'}),
        }
