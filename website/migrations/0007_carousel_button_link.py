# Generated by Django 5.0.4 on 2024-05-02 13:46

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0006_alter_gallery_title'),
    ]

    operations = [
        migrations.AddField(
            model_name='carousel',
            name='button_link',
            field=models.TextField(blank=True, null=True),
        ),
    ]
