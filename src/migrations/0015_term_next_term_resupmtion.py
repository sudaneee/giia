# Generated by Django 5.1.3 on 2024-11-26 12:50

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0014_alter_student_admission_status_alter_student_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='term',
            name='next_term_resupmtion',
            field=models.DateField(blank=True, null=True),
        ),
    ]
