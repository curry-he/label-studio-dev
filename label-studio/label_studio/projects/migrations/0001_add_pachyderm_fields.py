# Generated migration for adding Pachyderm integration fields to DatasetVersion

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0001_squashed_0065_auto_20210223_2014'),
    ]

    operations = [
        migrations.AddField(
            model_name='datasetversion',
            name='pachyderm_input_commit',
            field=models.CharField(blank=True, help_text='Pachyderm input repository commit ID', max_length=256, null=True),
        ),
        migrations.AddField(
            model_name='datasetversion',
            name='pachyderm_output_commit',
            field=models.CharField(blank=True, help_text='Pachyderm output repository commit ID', max_length=256, null=True),
        ),
        migrations.AddField(
            model_name='datasetversion',
            name='processed_at',
            field=models.DateTimeField(blank=True, help_text='When the data processing was completed', null=True),
        ),
        migrations.AddField(
            model_name='datasetversion',
            name='error_message',
            field=models.TextField(blank=True, help_text='Error message if processing failed', null=True),
        ),
    ]