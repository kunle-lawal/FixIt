# -*- coding: utf-8 -*-
# Generated by Django 1.11.3 on 2017-10-25 16:46
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('transcript', '0012_transcriptphrase_num_votes'),
    ]

    operations = [
        migrations.AddField(
            model_name='transcriptphrase',
            name='current_game',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]