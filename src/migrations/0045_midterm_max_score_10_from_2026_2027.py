# Data migration: the school changed the mid-term assessment max score from
# 20 to 10, starting with the 2026/2027 session. Earlier sessions
# (2024/2025, 2025/2026) keep the model's default of 20, since their
# results were graded on that scale and relabeling them now would
# misrepresent what was actually recorded. Any session created after this
# one should have its own max score set explicitly (the session create/
# edit form now asks for it) rather than silently inheriting this value.
from django.db import migrations


def set_2026_2027_to_10(apps, schema_editor):
    Session = apps.get_model('src', 'Session')
    Session.objects.filter(name='2026/2027').update(midterm_max_score=10)


def revert_to_20(apps, schema_editor):
    Session = apps.get_model('src', 'Session')
    Session.objects.filter(name='2026/2027').update(midterm_max_score=20)


class Migration(migrations.Migration):

    dependencies = [
        ('src', '0044_session_midterm_max_score'),
    ]

    operations = [
        migrations.RunPython(set_2026_2027_to_10, revert_to_20),
    ]
