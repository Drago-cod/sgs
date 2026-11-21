from decimal import Decimal
import uuid

from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import BursarForm, FeePaymentForm, FeeStructureForm
from .models import Bursar, Course, FeePayment, FeeStructure, Session, Student


def _generate_receipt_number():
    year = timezone.localdate().year
    prefix = f"JASS-{year}-"
    count = FeePayment.objects.filter(receipt_number__startswith=prefix).count() + 1
    candidate = f"{prefix}{count:04d}"
    while FeePayment.objects.filter(receipt_number=candidate).exists():
        candidate = f"{prefix}{uuid.uuid4().hex[:6].upper()}"
    return candidate


def _compute_student_fee_ledger():
    """Return list of per-student fee summary dicts and overall financial totals."""
    structures_by_course = {}
    for fs in FeeStructure.objects.select_related("course", "session"):
        structures_by_course.setdefault(fs.course_id, Decimal("0.00"))
        structures_by_course[fs.course_id] += fs.amount

    payments_by_student = {}
    for row in FeePayment.objects.values("student_id").annotate(total=Sum("amount_paid")):
        payments_by_student[row["student_id"]] = row["total"] or Decimal("0.00")

    ledger = []
    total_expected = Decimal("0.00")
    total_collected = Decimal("0.00")

    for student in Student.objects.select_related("admin", "course", "session").order_by(
        "course__name", "admin__last_name"
    ):
        expected = structures_by_course.get(student.course_id, Decimal("0.00"))
        paid = payments_by_student.get(student.id, Decimal("0.00"))
        balance = max(Decimal("0.00"), expected - paid)
        if expected <= 0:
            status = "NO_FEE_SET"
        elif balance == 0:
            status = "PAID"
        elif paid > 0:
            status = "PARTIAL"
        else:
            status = "UNPAID"

        total_expected += expected
        total_collected += paid
        ledger.append({
            "student": student,
            "expected": expected,
            "paid": paid,
            "balance": balance,
            "status": status,
        })

    total_outstanding = max(Decimal("0.00"), total_expected - total_collected)
    collection_rate = (
        round(float(total_collected / total_expected) * 100.0, 1)
        if total_expected > 0
        else 0.0
    )
    return {
        "ledger": ledger,
        "total_expected": total_expected,
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
        "collection_rate": collection_rate,
    }


def bursar_home(request):
    summary = _compute_student_fee_ledger()
    recent_payments = FeePayment.objects.select_related(
        "student__admin", "student__course", "fee_structure", "recorded_by"
    )[:10]

    method_totals = []
    method_labels = dict(FeePayment.PAYMENT_METHODS)
    for code, label in method_labels.items():
        amt = (
            FeePayment.objects.filter(payment_method=code).aggregate(total=Sum("amount_paid"))[
                "total"
            ]
            or Decimal("0.00")
        )
        method_totals.append({"code": code, "label": label, "amount": float(amt)})

    paid_students = sum(1 for row in summary["ledger"] if row["status"] == "PAID")
    partial_students = sum(1 for row in summary["ledger"] if row["status"] == "PARTIAL")
    unpaid_students = sum(1 for row in summary["ledger"] if row["status"] == "UNPAID")

    context = {
        "page_title": "Bursar / Accountant Dashboard",
        "total_expected": summary["total_expected"],
        "total_collected": summary["total_collected"],
        "total_outstanding": summary["total_outstanding"],
        "collection_rate": summary["collection_rate"],
        "paid_students": paid_students,
        "partial_students": partial_students,
        "unpaid_students": unpaid_students,
        "total_receipts": FeePayment.objects.count(),
        "recent_payments": recent_payments,
        "method_totals": method_totals,
    }
    return render(request, "bursar_template/home_content.html", context)


def bursar_fee_structures(request):
    form = FeeStructureForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            fs = form.save()
            messages.success(request, f"Fee structure '{fs.title}' for {fs.course.name} saved.")
            return redirect("bursar_fee_structures")
        messages.error(request, "Please correct the errors in the fee structure form.")

    structures = FeeStructure.objects.select_related("course", "session").all()
    context = {
        "page_title": "School Fee Structures",
        "form": form,
        "structures": structures,
    }
    return render(request, "bursar_template/fee_structures.html", context)


@require_POST
def bursar_delete_fee_structure(request, fee_id):
    fs = get_object_or_404(FeeStructure, id=fee_id)
    fs.delete()
    messages.success(request, "Fee structure deleted.")
    return redirect("bursar_fee_structures")


def bursar_payments(request):
    form = FeePaymentForm(
        request.POST or None,
        initial={"payment_date": timezone.localdate()},
    )
    if request.method == "POST":
        if form.is_valid():
            payment = form.save(commit=False)
            payment.receipt_number = _generate_receipt_number()
            payment.recorded_by = request.user
            if not payment.session and payment.fee_structure:
                payment.session = payment.fee_structure.session
            elif not payment.session and payment.student.session:
                payment.session = payment.student.session
            payment.save()
            messages.success(
                request,
                f"Payment recorded! Receipt #{payment.receipt_number} issued for {payment.student.admin.get_full_name()}.",
            )
            return redirect("bursar_receipt", payment_id=payment.id)
        messages.error(request, "Please fix the errors in the payment form.")

    summary = _compute_student_fee_ledger()
    payments = FeePayment.objects.select_related(
        "student__admin", "student__course", "fee_structure", "recorded_by"
    )[:50]
    context = {
        "page_title": "Fee Payments & Student Balances",
        "form": form,
        "ledger": summary["ledger"],
        "payments": payments,
    }
    return render(request, "bursar_template/payments.html", context)


def bursar_receipt(request, payment_id):
    payment = get_object_or_404(
        FeePayment.objects.select_related(
            "student__admin", "student__course", "fee_structure", "session", "recorded_by"
        ),
        id=payment_id,
    )
    expected = (
        FeeStructure.objects.filter(course=payment.student.course).aggregate(
            total=Sum("amount")
        )["total"]
        or Decimal("0.00")
    )
    total_paid = (
        FeePayment.objects.filter(student=payment.student).aggregate(
            total=Sum("amount_paid")
        )["total"]
        or Decimal("0.00")
    )
    balance_remaining = max(Decimal("0.00"), expected - total_paid)
    context = {
        "page_title": f"Official Receipt #{payment.receipt_number}",
        "payment": payment,
        "expected": expected,
        "total_paid": total_paid,
        "balance_remaining": balance_remaining,
    }
    return render(request, "bursar_template/receipt.html", context)


def bursar_financial_reports(request):
    summary = _compute_student_fee_ledger()
    class_reports = []
    for course in Course.objects.all():
        rows = [r for r in summary["ledger"] if r["student"].course_id == course.id]
        exp = sum((r["expected"] for r in rows), Decimal("0.00"))
        col = sum((r["paid"] for r in rows), Decimal("0.00"))
        bal = max(Decimal("0.00"), exp - col)
        rate = round(float(col / exp) * 100.0, 1) if exp > 0 else 0.0
        class_reports.append({
            "course": course,
            "student_count": len(rows),
            "expected": exp,
            "collected": col,
            "outstanding": bal,
            "rate": rate,
        })

    method_breakdown = []
    for code, label in FeePayment.PAYMENT_METHODS:
        qs = FeePayment.objects.filter(payment_method=code)
        method_breakdown.append({
            "label": label,
            "count": qs.count(),
            "total": qs.aggregate(total=Sum("amount_paid"))["total"] or Decimal("0.00"),
        })

    defaulters = [r for r in summary["ledger"] if r["balance"] > 0]

    context = {
        "page_title": "Financial Reports",
        "generated_at": timezone.now(),
        "total_expected": summary["total_expected"],
        "total_collected": summary["total_collected"],
        "total_outstanding": summary["total_outstanding"],
        "collection_rate": summary["collection_rate"],
        "class_reports": class_reports,
        "method_breakdown": method_breakdown,
        "defaulters": defaulters,
    }
    return render(request, "bursar_template/financial_reports.html", context)


def bursar_view_profile(request):
    bursar, _ = Bursar.objects.get_or_create(admin=request.user)
    form = BursarForm(request.POST or None, request.FILES or None, instance=bursar)
    if request.method == "POST":
        if form.is_valid():
            custom_user = bursar.admin
            password = form.cleaned_data.get("password") or None
            passport = request.FILES.get("profile_pic") or None
            if password:
                custom_user.set_password(password)
            if passport:
                custom_user.profile_pic = passport
            custom_user.first_name = form.cleaned_data.get("first_name")
            custom_user.last_name = form.cleaned_data.get("last_name")
            custom_user.address = form.cleaned_data.get("address")
            custom_user.gender = form.cleaned_data.get("gender")
            custom_user.save()
            messages.success(request, "Bursar Profile Updated!")
            return redirect("bursar_view_profile")
        messages.error(request, "Invalid data provided.")
    return render(
        request,
        "bursar_template/bursar_view_profile.html",
        {"form": form, "page_title": "Bursar Profile"},
    )
