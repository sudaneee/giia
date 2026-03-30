from django.db import models


from statistics import mode

from django.db import models
import datetime

class GeneralInformation(models.Model):
    phone_number = models.CharField(max_length=200)
    email = models.CharField(max_length=200)
    preamble = models.TextField()
    logo = models.ImageField(upload_to='pics', null=True)
    footer_logo = models.ImageField(upload_to='pics', null=True)
    address = models.CharField(max_length=500, null=True)



    def __str__(self):
        return self.email


class Carousel(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    image = models.ImageField(upload_to='pics')
    created = models.DateTimeField(auto_now_add=True)
    button_link = models.TextField(null=True, blank=True)

    def __str__(self):
        return self.title


    class Meta:
        ordering = ('-created',)

class Course(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    image = models.ImageField(upload_to='pics')


    def __str__(self):
        return self.title

    class Meta:
        ordering = ('-title',)


class Event(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    image = models.ImageField(upload_to='pics')
    time = models.DateTimeField()
    venue = models.TextField()

    def __str__(self):
        return self.title

    class Meta:
        ordering = ('-time',)

class Blog(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    image = models.ImageField(upload_to='pics')
    posted = models.DateTimeField(auto_now_add=True)
    news_file = models.FileField(upload_to='website files', null=True)


    def __str__(self):
        return self.title


    class Meta:
        ordering = ('-posted',)



class Picture(models.Model):
    title = models.CharField(max_length=200)
    image = models.ImageField(upload_to='pics')

    def __str__(self):
        return self.title

class Paragraph(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()

    def __str__(self):
        return self.title


class Staff(models.Model):
    name = models.CharField(max_length=200)
    facebook = models.CharField(max_length=200)
    twitter = models.CharField(max_length=200)
    instagram = models.CharField(max_length=200)
    email = models.CharField(max_length=200)

    designation = models.TextField()
    background = models.TextField(null=True, blank=True)
    image = models.ImageField(upload_to='pics')
    

    def __str__(self):
        return self.name


class Gallery(models.Model):
    image = models.ImageField(upload_to='pics')
    title = models.CharField(max_length=200, null=True, blank=True)

    def __str__(self):
        return self.title


class Journal(models.Model):
    title = models.CharField(max_length=200)
    uploaded = models.DateTimeField(auto_now_add=True)
    news_file = models.FileField(upload_to='e-journals', null=True)


    def __str__(self):
        return self.title


    class Meta:
        ordering = ('-uploaded',)