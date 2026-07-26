from django.db import models

GENDER_CHOICES = [('Male', 'Male'), ('Female', 'Female')]

BLOOD_GROUP_CHOICES = [(v, v) for v in ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']]

GENOTYPE_CHOICES = [(v, v) for v in ['AA', 'AS', 'SS', 'AC', 'SC']]


class AdmissionOpening(models.Model):
    """
    Which classes are open for online applications this admission cycle, and
    how many paid applications each will accept. Capacity isn't a fixed
    property of a SchoolClass (it varies every cycle) so it lives here as an
    opt-in row per (session, class) rather than a field on SchoolClass
    itself - only classes with a row here are selectable on the public apply
    form, which is also how a specific cycle's admission list (e.g. just
    Kindergarten + Basic 7-9 + the two Tahfeez classes) is scoped without
    touching every other existing SchoolClass.
    """
    session = models.ForeignKey('src.Session', on_delete=models.CASCADE, related_name='admission_openings')
    school_class = models.ForeignKey('src.SchoolClass', on_delete=models.CASCADE, related_name='admission_openings')
    capacity = models.PositiveIntegerField(null=True, blank=True, help_text='Leave blank for unlimited.')
    is_open = models.BooleanField(default=True, help_text='Manual switch to close early, independent of capacity.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('session', 'school_class')

    def __str__(self):
        cap = self.capacity if self.capacity is not None else 'unlimited'
        return f"{self.school_class} - {self.session} (capacity: {cap})"

    @property
    def paid_applicant_count(self):
        return Applicant.objects.filter(
            desired_class=self.school_class, session=self.session, application_fee_paid=True,
        ).exclude(status='rejected').count()

    @property
    def is_full(self):
        return self.capacity is not None and self.paid_applicant_count >= self.capacity

    @property
    def is_available(self):
        return self.is_open and not self.is_full


class Applicant(models.Model):
    STATUS_CHOICES = [
        ('submitted', 'Submitted'),
        ('interviewed', 'Interviewed'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    app_number = models.CharField(max_length=30, unique=True, null=True, blank=True)

    # Pupil's personal information
    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    date_of_birth = models.DateField()
    place_of_birth = models.CharField(max_length=100, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES)
    native_language = models.CharField(max_length=50, blank=True)
    blood_group = models.CharField(max_length=10, choices=BLOOD_GROUP_CHOICES, blank=True)
    genotype = models.CharField(max_length=10, choices=GENOTYPE_CHOICES, blank=True)
    has_ailment = models.BooleanField(default=False)
    ailment_details = models.TextField(blank=True)
    residential_address = models.TextField()
    photo = models.ImageField(upload_to='applicants/photos/', blank=True, null=True)

    # Father/guardian information
    father_name = models.CharField(max_length=100)
    father_address = models.TextField(blank=True)
    father_occupation = models.CharField(max_length=100, blank=True)
    father_qualification = models.CharField(max_length=100, blank=True)
    father_phone = models.CharField(max_length=20)
    father_email = models.EmailField(blank=True)

    # Mother information
    mother_name = models.CharField(max_length=100, blank=True)
    mother_qualification = models.CharField(max_length=100, blank=True)
    mother_phone = models.CharField(max_length=20, blank=True)
    mother_email = models.EmailField(blank=True)

    # What/when they're applying for
    session = models.ForeignKey('src.Session', on_delete=models.PROTECT, related_name='applicants')
    desired_class = models.ForeignKey(
        'src.SchoolClass', on_delete=models.PROTECT, related_name='applicants_desiring',
    )

    # Declaration
    declaration_name = models.CharField(max_length=100)
    declaration_agreed = models.BooleanField(default=False)

    # Payment
    reference = models.CharField(max_length=100, unique=True)
    application_fee_paid = models.BooleanField(default=False)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Review workflow ("FOR OFFICIAL USE ONLY" on the paper form)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='submitted')
    interview_date = models.DateField(null=True, blank=True)
    interviewer_name = models.CharField(max_length=100, blank=True)
    recommended_class = models.ForeignKey(
        'src.SchoolClass', on_delete=models.SET_NULL, null=True, blank=True, related_name='applicants_recommended',
    )
    decided_by = models.CharField(max_length=100, blank=True)
    decision_date = models.DateField(null=True, blank=True)

    linked_student = models.OneToOneField(
        'src.Student', on_delete=models.SET_NULL, null=True, blank=True, related_name='applicant',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.app_number or 'pending'} - {self.first_name} {self.last_name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.app_number:
            self.app_number = f"GIIA/APP/{self.created_at.year}/{self.id:04d}"
            super().save(update_fields=['app_number'])
