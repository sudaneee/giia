from django.db import models
from django.contrib.auth.models import User
from django.db.models import Sum, Q, F, Window

from django.db.models.functions import Rank

# Utility function for ordinal representation
def ordinal(n):
    """Convert an integer into its ordinal representation."""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return str(n) + suffix

# Guardian Model
class Guardian(models.Model):
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    phone_number = models.CharField(max_length=15)
    email = models.EmailField(blank=True, null=True)
    relationship = models.CharField(max_length=50)  # e.g., Mother, Father, etc.

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

# SchoolClass Model
class SchoolClass(models.Model):
    name = models.CharField(max_length=50)
    description = models.TextField(blank=True, null=True)
    level = models.CharField(max_length=20)  # e.g., "Grade 1", "JSS 2", "SS 3"
    arm = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')], default='A')

    def __str__(self):
        return f"{self.name} {self.arm}"

# Student

class Student(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('graduated', 'Graduated'),
        ('suspended', 'Suspended'),
    ]


    ADMISSION_STATUS_CHOICES = [
        ('admitted', 'Admitted'),
        ('not_admitted', 'Not Admitted'),
    ]


    admission_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=200, null=True, blank=True)
    address = models.TextField(null=True, blank=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    guardians = models.ManyToManyField('Guardian', blank=True)
    enrolled_class = models.ForeignKey('SchoolClass', on_delete=models.SET_NULL, null=True, blank=True)
    enrollment_date = models.DateField(auto_now_add=True)
    photo = models.ImageField(upload_to='students/photos/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    admission_status = models.CharField(max_length=20, choices=ADMISSION_STATUS_CHOICES, default='admitted')
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    admitted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

# Subject Model
class Subject(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

# Session Model
class Session(models.Model):
    name = models.CharField(max_length=20)  # e.g., "2023/2024"
    start_date = models.DateField()
    end_date = models.DateField()
    current = models.BooleanField(default=False)  # To indicate the active session

    def __str__(self):
        return self.name
    
    def save(self, *args, **kwargs):
        if self.current:
            # Ensure only one session is marked as current
            Session.objects.filter(current=True).update(current=False)
        super(Session, self).save(*args, **kwargs)

# Term Model
class Term(models.Model):
    name = models.CharField(max_length=50)  # e.g., "First Term", "Second Term"
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    next_term_resupmtion = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ('name', 'session')  # To prevent duplicate terms in the same session

    def __str__(self):
        return f"{self.name} - {self.session.name}"



class Result(models.Model):
    student = models.ForeignKey('Student', on_delete=models.CASCADE)
    subject = models.ForeignKey('Subject', on_delete=models.CASCADE)
    class_assigned = models.ForeignKey('SchoolClass', on_delete=models.CASCADE)
    session = models.ForeignKey('Session', on_delete=models.CASCADE)
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    ca1_marks = models.IntegerField(verbose_name="1st CA Marks")
    ca2_marks = models.IntegerField(verbose_name="2nd CA Marks")
    home_work_marks = models.IntegerField(verbose_name="Home Work Marks")
    activity_marks = models.IntegerField(verbose_name="Activity Marks")
    exam_marks = models.IntegerField(verbose_name="Exam Marks")

    @property
    def total_marks(self):
        return self.ca1_marks + self.ca2_marks + self.home_work_marks + self.activity_marks + self.exam_marks

    @property
    def grade(self):
        if 76 <= self.total_marks <= 100:
            return 'A+'
        elif 70 <= self.total_marks <= 75:
            return 'A'
        elif 65 <= self.total_marks <= 69:
            return 'A-'
        elif 60 <= self.total_marks <= 64:
            return 'B+'
        elif 55 <= self.total_marks <= 59:
            return 'B'
        elif 50 <= self.total_marks <= 54:
            return 'B-'
        elif 46 <= self.total_marks <= 49:
            return 'C+'
        elif 43 <= self.total_marks <= 45:
            return 'C'
        elif 39 <= self.total_marks <= 42:
            return 'C-'
        elif 0 <= self.total_marks <= 38:
            return 'F'
        else:
            return 'Invalid Marks'

    def calculate_position(self):
        """
        Calculate the rank of the current result (`self`) among all results for the same subject,
        session, term, and class_assigned, handling ties.
        """
        # Fetch all results for the same subject, session, term, and class_assigned
        results = Result.objects.filter(
            subject=self.subject,
            session=self.session,
            term=self.term,
            class_assigned=self.class_assigned
        )

        # Compute total marks dynamically for each result
        total_scores = [(result.id, result.total_marks) for result in results]

        # Sort total scores in descending order
        total_scores.sort(key=lambda x: x[1], reverse=True)

        # Assign ranks, handling ties
        rank = 1
        positions = {}
        for idx, (result_id, marks) in enumerate(total_scores):
            if idx > 0 and marks < total_scores[idx - 1][1]:  # Compare with the previous score
                rank = idx + 1  # Update rank only when scores differ
            positions[result_id] = rank

        # Return the position of `self`
        return positions.get(self.id)

    @property
    def subject_position(self):
        """
        Expose the rank as a property for easy access.
        """
        return ordinal(self.calculate_position())

    def __str__(self):
        return f"{self.student} - {self.subject} ({self.term.name} {self.session.name} - {self.class_assigned.name})"




class StudentBehaviouralAssessment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE)
    conduct = models.IntegerField(default=0)
    punctuality = models.IntegerField(default=0)
    dedication = models.IntegerField(default=0)
    participation = models.IntegerField(default=0)
    hospitality = models.IntegerField(default=0)
    neatness = models.IntegerField(default=0)
    creativity = models.IntegerField(default=0)
    physical = models.IntegerField(default=0)


    def __str__(self):
        return f'{self.session} {self.term} {self.school_class} {self.student}'

# Department Model
class Department(models.Model):
    name = models.CharField(max_length=100)

# Role Model
class Role(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

# Staff Model
class Staff(models.Model):
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=15)
    address = models.TextField()
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True)
    date_joined = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[('active', 'Active'), ('inactive', 'Inactive')])

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

# LeaveRecord Model
class LeaveRecord(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=[('approved', 'Approved'), ('pending', 'Pending'), ('rejected', 'Rejected')])
    leave_type = models.CharField(max_length=50, choices=[('sick', 'Sick'), ('casual', 'Casual'), ('annual', 'Annual'), ('maternity', 'Maternity')])

    def __str__(self):
        return f"Leave Record for {self.staff}"

# StudentAttendanceRecord Model
class StudentAttendanceRecord(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    date = models.DateField()
    status = models.CharField(max_length=10, choices=[('present', 'Present'), ('absent', 'Absent'), ('late', 'Late')])
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    remark = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Attendance Record for {self.student} for {self.term} {self.session} Academic Session"

# StaffAttendanceRecord Model
class StaffAttendanceRecord(models.Model):
    staff = models.ForeignKey(Staff, on_delete=models.CASCADE)
    date = models.DateField()
    status = models.CharField(max_length=10, choices=[('present', 'Present'), ('absent', 'Absent'), ('late', 'Late')])
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    remark = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"Attendance Record for {self.staff} for {self.term} {self.session} Academic Session"

# FeeStructure Model
class FeeStructure(models.Model):
    class_assigned = models.ForeignKey(SchoolClass, on_delete=models.CASCADE)
    description = models.TextField()
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)
    fee_type = models.CharField(max_length=50, choices=[('tuition', 'Tuition'), ('library', 'Library'), ('sports', 'Sports')])

    def __str__(self):
        return f"School fees for {self.term}, {self.session} Academic Session"

# Payment Model
class Payment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    payment_date = models.DateField(auto_now_add=True)
    payment_method = models.CharField(max_length=50, choices=[('cash', 'Cash'), ('bank_transfer', 'Bank Transfer'), ('credit_card', 'Credit Card')])
    status = models.CharField(max_length=20, choices=[('paid', 'Paid'), ('pending', 'Pending'), ('overdue', 'Overdue')])
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    term = models.ForeignKey(Term, on_delete=models.CASCADE)

    def update_status(self):
        if self.amount_paid >= self.fee_structure.amount:
            self.status = 'paid'
        elif self.payment_date > self.fee_structure.due_date:
            self.status = 'overdue'
        else:
            self.status = 'pending'
        self.save()

    def __str__(self):
        return f"Payment Record for {self.student} for {self.term} {self.session} Academic Session"



# Category Model for Item Categorization
class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

# Supplier Model to Manage Supplier Information
class Supplier(models.Model):
    name = models.CharField(max_length=100)
    contact_email = models.EmailField(blank=True, null=True)
    contact_phone = models.CharField(max_length=15, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

# Item Model to Manage Inventory Items
class Item(models.Model):
    name = models.CharField(max_length=100)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.TextField(blank=True, null=True)
    quantity_in_stock = models.IntegerField()
    reorder_level = models.IntegerField()
    expiry_date = models.DateField(null=True, blank=True)  # Optional field for perishable items

    def __str__(self):
        return self.name

# Inventory Transaction Model to Track Stock Movements
class InventoryTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('addition', 'Addition'),
        ('removal', 'Removal'),
        ('adjustment', 'Adjustment'),
    ]
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=50, choices=TRANSACTION_TYPES)
    quantity = models.IntegerField()
    date = models.DateField(auto_now_add=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.transaction_type} of {self.quantity} {self.item.name} on {self.date}"

# Purchase Order Model to Manage Orders from Suppliers
class PurchaseOrder(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    quantity_ordered = models.IntegerField()
    received_quantity = models.IntegerField(default=0)  # Track received quantity for partial deliveries
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2)
    order_date = models.DateField(auto_now_add=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    received_date = models.DateField(null=True, blank=True)

    @property
    def total_cost(self):
        return self.quantity_ordered * self.price_per_unit

    def save(self, *args, **kwargs):
        # Automatically update stock quantity if the order is marked as received
        if self.received_date:
            difference = self.received_quantity - self.item.quantity_in_stock
            self.item.quantity_in_stock += difference
            self.item.save()
        super(PurchaseOrder, self).save(*args, **kwargs)

    def __str__(self):
        return f"Order for {self.item.name} on {self.order_date}"



class HowYouFindUs(models.Model):
    student = models.OneToOneField('Student', on_delete=models.CASCADE)
    source = models.CharField(max_length=50)

    def __str__(self):
        return f"{self.student.first_name} - {self.source}"

class SchoolConfig(models.Model):
    header_image = models.ImageField(upload_to='school_images/')
    signature_image = models.ImageField(upload_to='school_images/')
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"School Configuration - Last updated at {self.updated_at}"


class Token(models.Model):
    token_code = models.CharField(max_length=20, unique=True)  # Unique token identifier
    usage_count = models.PositiveIntegerField(default=0)  # Tracks usage count
    max_usage = models.PositiveIntegerField(default=5)  # Max allowable usage
    associated_student = models.ForeignKey(Student, null=True, blank=True, on_delete=models.SET_NULL)
    session = models.ForeignKey(Session, null=True, blank=True, on_delete=models.SET_NULL)
    term = models.ForeignKey(Term, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)  # If token is valid

    def __str__(self):
        return self.token_code

    def use_token(self, student, session, term):
        """
        Function to handle token usage.
        Associates student, session, and term on first use.
        Increments usage and disables token if limit is reached.
        """
        if not self.is_active:
            raise ValueError("This token is no longer active.")

        if self.usage_count >= self.max_usage:
            self.is_active = False
            self.save()
            raise ValueError("This token has reached its maximum usage.")

        if self.associated_student:
            if self.associated_student != student or self.session != session or self.term != term:
                raise ValueError("This token is already associated with another student/session/term.")

        # Associate token on first use
        if self.usage_count == 0:
            self.associated_student = student
            self.session = session
            self.term = term

        # Increment usage count
        self.usage_count += 1

        # Disable token if max usage reached
        if self.usage_count >= self.max_usage:
            self.is_active = False

        self.save()




# Tahfeez Result Model
class TahfeezResult(models.Model):
    student = models.ForeignKey('Student', on_delete=models.CASCADE)
    subject = models.ForeignKey('Subject', on_delete=models.CASCADE)
    class_assigned = models.ForeignKey('SchoolClass', on_delete=models.CASCADE)
    session = models.ForeignKey('Session', on_delete=models.CASCADE)
    term = models.ForeignKey('Term', on_delete=models.CASCADE)
    marks = models.IntegerField(verbose_name="Marks")

    @property
    def total_marks(self):
        # Calculate the total marks for the student in the specific session, term, and class
        total = TahfeezResult.objects.filter(
            student=self.student,
            session=self.session,
            term=self.term,
            class_assigned=self.class_assigned
        ).aggregate(total=Sum('marks'))['total']
        return total if total else 0

    @property
    def class_position(self):
        # Get total marks for all students in the same class, session, and term
        student_totals = TahfeezResult.objects.filter(
            session=self.session,
            term=self.term,
            class_assigned=self.class_assigned
        ).values('student').annotate(total=Sum('marks')).order_by('-total')

        # Handle ties by assigning the same rank to students with equal total marks
        rank = 1
        ranked_students = {}
        previous_total = None

        for index, student in enumerate(student_totals):
            if previous_total is None or student['total'] < previous_total:
                rank = index + 1  # Update rank only if total marks are lower
            ranked_students[student['student']] = rank
            previous_total = student['total']

        # Return the rank of the current student
        return ordinal(ranked_students.get(self.student.id, None))

    @property
    def grade(self):
        if 76 <= self.total_marks <= 100:
            return 'A+'
        elif 70 <= self.total_marks <= 75:
            return 'A'
        elif 65 <= self.total_marks <= 69:
            return 'A-'
        elif 60 <= self.total_marks <= 64:
            return 'B+'
        elif 55 <= self.total_marks <= 59:
            return 'B'
        elif 50 <= self.total_marks <= 54:
            return 'B-'
        elif 46 <= self.total_marks <= 49:
            return 'C+'
        elif 43 <= self.total_marks <= 45:
            return 'C'
        elif 39 <= self.total_marks <= 42:
            return 'C-'
        elif 0 <= self.total_marks <= 38:
            return 'F'
        else:
            return 'Invalid Marks'

    def __str__(self):
        return f"{self.student} - {self.class_assigned} ({self.term.name} {self.session.name})"
