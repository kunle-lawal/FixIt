import logging
import requests
import random
from collections import Counter

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.fields import JSONField
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db.models.expressions import RawSQL
from django.db import models
from localflavor.us.models import USStateField

from mla_game.apps.accounts.models import TranscriptPicks
from .exceptions import TranscriptCompleteException, GameOneCompleteException

phrase_positive_limit = settings.TRANSCRIPT_PHRASE_POSITIVE_CONFIDENCE_LIMIT
phrase_negative_limit = settings.TRANSCRIPT_PHRASE_NEGATIVE_CONFIDENCE_LIMIT
correction_lower_limit = settings.TRANSCRIPT_PHRASE_CORRECTION_LOWER_LIMIT
correction_upper_limit = settings.TRANSCRIPT_PHRASE_CORRECTION_UPPER_LIMIT

django_log = logging.getLogger('django')


def usable_for_game_one(user, transcript):
    eligible_phrases = TranscriptPhrase.objects.filter(
        transcript=transcript,
        current_game=1,
    )

    user_votes = TranscriptPhraseVote.objects.filter(
        transcript_phrase__in=eligible_phrases,
        user=user
    )

    if user_votes.count() == eligible_phrases.count():
        raise TranscriptCompleteException
    elif eligible_phrases.count() == 0:
        raise GameOneCompleteException
    else:
        return


class TranscriptManager(models.Manager):
    def game_one(self, user):
        '''
        Returns a transcript based on user's preferences and/or past progress.
        If no suitable transcript is found, returns a random transcript.
        '''
        picks, created = TranscriptPicks.objects.get_or_create(user=user)
        picks = picks.picks
        if created:
            return (self.random_transcript(), False)
        else:
            try:
                transcripts = self.defer('transcript_data_blob').filter(
                    pk__in=picks['partially_completed_transcripts'],
                    active=True
                )
                usable_for_game_one(user, transcripts.first())
                transcript = self.defer(
                    'transcript_data_blob'
                ).filter(
                    pk=transcripts.first().pk
                )
                voted_phrases = [vote.transcript_phrase.pk for vote in
                                 TranscriptPhraseVote.objects.filter(user=user)]
                return (transcript, voted_phrases)
            except Exception:
                pass
            try:
                ideal_transcripts = [
                    transcript.pk for transcript in
                    self.filter(
                        pk__in=picks['ideal_transcripts'],
                        active=True
                    ).only('pk')
                ]
                transcript = self.defer('transcript_data_blob').filter(
                    pk=random.choice(ideal_transcripts))
                usable_for_game_one(user, transcript.first())
                return (transcript, False)
            except GameOneCompleteException:
                pass
            except Exception:
                pass
            try:
                acceptable_transcripts = [
                    transcript.pk for transcript in
                    self.filter(
                        pk__in=picks['acceptable_transcripts'],
                        active=True
                    ).only('pk')
                ]
                transcript = self.defer('transcript_data_blob').filter(
                    pk=random.choice(acceptable_transcripts))
                usable_for_game_one(user, transcript.first())
                return (transcript, False)
            except Exception:
                pass

            skipped = 'skipped_transcripts' in picks
            auto_skipped = 'auto_skipped_transcripts' in picks
            completed = 'completed_transcripts' in picks
            if skipped or auto_skipped or completed:
                return (
                    self.random_transcript(
                        in_progress=False, picks=picks, user=user
                    ),
                    False
                )
        return (self.random_transcript(), False)

    def game_two(self, user):
        '''
        Returns a tuple containing a queryset of Transcript objects and a list
        of phrase PKs to annotate.

        Phrases are excluded under the following circumstances:
            - User upvoted the original phrase
            - User has submitted a correction for this phrase
            - User has upvoted a submitted correction
        '''
        ineligible_phrases = [
            interaction.transcript_phrase.pk for interaction in
            TranscriptPhraseInteraction.objects.filter(
                user=user,
                preclude_from_game=2
            )
        ]
        eligible_phrases = TranscriptPhrase.objects.filter(
            current_game=2,
            active=True
        ).exclude(
            pk__in=ineligible_phrases
        ).only(
            'current_game', 'transcript', 'pk'
        ).prefetch_related(
            models.Prefetch(
                'transcript', queryset=self.only('pk')
            )
        )

        counter = Counter(
            phrase.transcript for phrase in eligible_phrases
        ).most_common()
        total = 0
        transcripts = []

        for transcript, count in counter:
            transcripts.append(transcript)
            total += count
            if total >= 20:
                break

        game_two_ready_phrases = [
            phrase.pk for phrase in
            eligible_phrases.filter(transcript__in=transcripts)
        ][:20]

        transcripts_to_return = self.defer('transcript_data_blob').filter(
            phrases__in=game_two_ready_phrases).distinct()

        return (transcripts_to_return, game_two_ready_phrases)

    def game_three(self, user):
        '''
        Game three needs to present a transcript with submitted corrections for
        voting

        Returns a tuple containing a queryset of Transcript objects and a list
        of dicts containing corrections for phrases in the Transcripts
        '''
        corrected_phrases = set(
            TranscriptPhrase.objects.filter(
                current_game=3,
                active=True
            ).prefetch_related(models.Prefetch(
                'transcript',
                queryset=self.only('pk')
            ))
        )
        already_voted_phrases = {
            vote.original_phrase for vote in
            TranscriptPhraseCorrectionVote.objects.filter(
                user=user
            ).prefetch_related(
                'original_phrase'
            )
        }
        eligible_phrases = corrected_phrases - already_voted_phrases

        counted_transcripts = Counter(
            [phrase.transcript.pk for phrase in eligible_phrases]
        )

        eligible_phrases = sorted(
            eligible_phrases,
            key=lambda phrase: counted_transcripts[phrase.transcript.pk],
        )

        corrections = []
        associated_transcripts = []

        for phrase in eligible_phrases[:20]:
            phrase_corrections = TranscriptPhraseCorrection.objects.filter(
                transcript_phrase=phrase,
                confidence__gte=0.0
            ).order_by('pk')
            if phrase_corrections.count() >= 1:
                corrections.append(
                    {phrase.pk: [phrase_corrections.first()],
                     'transcript': phrase.transcript.pk}
                )
                associated_transcripts.append(phrase.transcript.pk)

        associated_transcripts.sort(
            key=Counter(associated_transcripts).get, reverse=True
        )

        transcripts = self.defer('transcript_data_blob').filter(
            pk__in=associated_transcripts[:20]).distinct()

        corrections = [correction for correction in corrections
                       if correction['transcript']
                       in associated_transcripts[:20]][:20]

        return (transcripts, corrections)

    def random_transcript(self, in_progress=True, picks={}, user=None):
        '''Returns a semi-random transcript.

        If in_progress is True, we'll pick the transcript that has the most
        phrases that are close to the minimum sample size, in order to move
        the most transcripts from game one to game two.

        If picks contain skipped or completed transcripts, we'll want to exclude
        those transcripts from the set of transcripts to use.'''

        excluded = []

        if 'skipped_transcripts' in picks:
            excluded += picks['skipped_transcripts']
        if 'auto_skipped_transcripts' in picks:
            excluded += picks['auto_skipped_transcripts']
        if 'completed_transcripts' in picks:
            excluded += picks['completed_transcripts']

        if excluded:
            qs = self.exclude(pk__in=excluded, active=False)
        else:
            qs = self.filter(active=True)

        if in_progress is False:
            transcripts_in_progress = False
        else:
            transcripts_in_progress = [
                transcript.pk for transcript in qs.filter(
                    statistics__phrases_close_to_minimum_sample_size_percent__gt=0
                ).only('pk', 'statistics').order_by(
                    RawSQL(
                        'statistics->>%s',
                        ('phrases_close_to_minimum_sample_size_percent',)
                    )
                )
            ]

        if transcripts_in_progress:
            return qs.filter(
                pk=transcripts_in_progress[-1]
            )

        for transcript in qs.filter(active=True).order_by('?'):
            try:
                usable_for_game_one(user, transcript)
                return self.filter(pk=transcript.pk)
            except TranscriptCompleteException:
                pass


class Transcript(models.Model):
    id_number = models.IntegerField()
    collection_id = models.IntegerField()
    name = models.TextField()
    asset_name = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=1000)
    transcript_data_blob = JSONField()
    data_blob_processed = models.BooleanField(
        default=False
    )
    metadata_processed = models.BooleanField(
        default=False
    )
    statistics = JSONField(default={
        'total_number_of_phrases': 0,
        'phrases_with_votes_percent': 0,
        'phrases_close_to_minimum_sample_size_percent': 0,
        'phrases_not_needing_correction_percent': 0,
        'phrases_needing_correction_percent': 0,
        'phrases_with_corrections_percent': 0,
        'corrections_submitted': 0,
        'corrections_accepted': 0,
        'phrases_ready_for_export': 0,
    })
    complete = models.BooleanField(default=False)
    in_progress = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    objects = TranscriptManager()

    @property
    def aapb_xml(self):
        xml_url = 'http://americanarchive.org/api/{}.xml'.format(
            self.asset_name
        )
        request = requests.get(xml_url)
        if request.status_code == 200:
            return request.text
        else:
            return None

    @property
    def aapb_link(self):
        return 'http://americanarchive.org/catalog/{}'.format(
            self.asset_name
        )

    @property
    def media_url(self):
        try:
            media_request = requests.head(
                'http://americanarchive.org/media/{}?part=1'.format(
                    self.asset_name
                ),
                headers={'referer': 'http://americanarchive.org/'},
            )
            if media_request.is_redirect:
                return media_request.headers['Location']
            else:
                return None
        except requests.exceptions.ConnectionError:
            return None

    @property
    def corrected_phrases(self):
        corrected_phrases = 0
        for phrase in self.phrases.all():
            if phrase.corrections > 0:
                corrected_phrases += 1

        return corrected_phrases

    def process_transcript_data_blob(self):
        data = self.transcript_data_blob
        if 'audio_files' in data:
            audio_files = data['audio_files']
            for entry in audio_files:
                if 'filename' in entry:
                    self.asset_name = entry['filename']
                if 'transcript' in entry:
                    if entry['transcript']:
                        new_phrases = [
                            TranscriptPhrase(
                                id_number=phrase['id'],
                                start_time=phrase['start_time'],
                                end_time=phrase['end_time'],
                                text=phrase['text'],
                                speaker_id=phrase['speaker_id'],
                                transcript=self
                            )
                            for phrase in entry['transcript']['parts'] if phrase is not None
                        ]
                        TranscriptPhrase.objects.bulk_create(new_phrases)
                self.data_blob_processed = True
                self.save()
        else:
            django_log.info(
                'Transcript {} has a malformed data blob.'.format(self.pk))

    def activate_or_deactivate_phrases(self, state):
        TranscriptPhrase.objects.filter(transcript=self).update(active=state)

    def __str__(self):
        return self.name


class TranscriptPhraseManager(models.Manager):
    use_for_related_fields = True

    def create_transcript_phrase(self, data_blob, transcript):
        id_number = data_blob['id']
        start_time = data_blob['start_time']
        end_time = data_blob['end_time']
        text = data_blob['text']
        speaker_id = data_blob['speaker_id']
        try:
            transcript_phrase = TranscriptPhrase.objects.get(
                id_number=id_number)
        except TranscriptPhrase.DoesNotExist:
            transcript_phrase = self.create(
                id_number=id_number,
                text=text,
                start_time=start_time,
                end_time=end_time,
                speaker_id=speaker_id,
                transcript=transcript
            )
        return transcript_phrase

    def unseen(self, user):
        considered_phrases = [vote.transcript_phrase.pk for vote in
                              TranscriptPhraseVote.objects.filter(user=user)]
        return self.exclude(pk__in=considered_phrases)


class TranscriptPhrase(models.Model):
    id_number = models.IntegerField()
    text = models.TextField()
    start_time = models.DecimalField(max_digits=12, decimal_places=2)
    end_time = models.DecimalField(max_digits=12, decimal_places=2)
    speaker_id = models.IntegerField()
    transcript = models.ForeignKey(
        Transcript,
        related_name='phrases',
        on_delete=models.CASCADE,
    )

    confidence = models.FloatField(default=0)
    num_corrections = models.SmallIntegerField(default=0)
    num_votes = models.SmallIntegerField(default=0)
    current_game = models.PositiveSmallIntegerField(default=1)
    active = models.BooleanField(default=True)

    objects = TranscriptPhraseManager()

    class Meta:
        ordering = ['start_time']
        indexes = [
            models.Index(fields=['current_game', 'active'])
        ]

    @property
    def downvotes_count(self):
        return TranscriptPhraseVote.objects.filter(
            transcript_phrase=self.pk,
            upvote=False
        ).count()

    @property
    def upvotes_count(self):
        return TranscriptPhraseVote.objects.filter(
            transcript_phrase=self.pk,
            upvote=True
        ).count()

    @property
    def considered_by_count(self):
        return self.profile_set.all().count()

    @property
    def corrections(self):
        return self.transcript_phrase_correction.all().count()

    @property
    def total_length(self):
        return self.end_time - self.start_time

    @property
    def best_correction(self):
        corrections = TranscriptPhraseCorrection.objects.filter(
            transcript_phrase=self
        ).order_by('-confidence')
        if corrections.count() > 0:
            return corrections.first()
        return None

    def __str__(self):
        return str(self.transcript) + '_phrase_' + str(self.id)


class TranscriptPhraseInteraction(models.Model):
    GAME_CHOICES = (
        (1, 'one'),
        (2, 'two'),
        (3, 'three'),
    )
    transcript_phrase = models.ForeignKey(
        TranscriptPhrase,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
    )
    preclude_from_game = models.IntegerField(
        choices=GAME_CHOICES
    )
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
    )
    object_id = models.PositiveIntegerField()
    precluding_action = GenericForeignKey(
        'content_type',
        'object_id'
    )


class TranscriptPhraseVote(models.Model):
    transcript_phrase = models.ForeignKey(
        TranscriptPhrase,
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    upvote = models.NullBooleanField()


class TranscriptPhraseCorrection(models.Model):
    correction = models.CharField(max_length=500, blank=True, null=True)
    confidence = models.FloatField(default=0)
    transcript_phrase = models.ForeignKey(
        TranscriptPhrase,
        related_name='transcript_phrase_correction',
        on_delete=models.CASCADE,
    )
    user = models.ForeignKey(
        User,
        default=None,
        on_delete=models.CASCADE,
    )

    @property
    def votes_count(self):
        return TranscriptPhraseCorrectionVote.objects.filter(
            transcript_phrase_correction=self.pk
        ).count()

    def __str__(self):
        return str(self.transcript_phrase) + '_correction'


class TranscriptPhraseCorrectionVote(models.Model):
    transcript_phrase_correction = models.ForeignKey(
        TranscriptPhraseCorrection,
        on_delete=models.CASCADE,
    )
    original_phrase = models.ForeignKey(
        TranscriptPhrase,
        null=True,
        default=None
    )
    upvote = models.NullBooleanField(default=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    def save(self, *args, **kwargs):
        if self.original_phrase is None:
            self.original_phrase = self.transcript_phrase_correction.transcript_phrase
        super(TranscriptPhraseCorrectionVote, self).save(*args, **kwargs)

    class Meta:
        unique_together = ('user', 'transcript_phrase_correction')


class TranscriptMetadata(models.Model):
    media_types = (
        ('v', 'Video'),
        ('a', 'Audio'),
        ('u', 'Unknown')
    )
    transcript = models.OneToOneField(
        Transcript,
        related_name='metadata',
        on_delete=models.CASCADE,
    )
    description = models.TextField(blank=True, null=True)
    series = models.CharField(max_length=255, blank=True, null=True)
    broadcast_date = models.CharField(max_length=255, blank=True, null=True)
    media_type = models.CharField(
        max_length=1,
        choices=media_types,
        default='u'
    )


class Source(models.Model):
    pbcore_source = models.CharField(max_length=255, null=True)
    source = models.CharField(max_length=255)
    state = USStateField(null=True)
    transcripts = models.ManyToManyField(Transcript)

    def __str__(self):
        return self.source


class Topic(models.Model):
    topic = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    transcripts = models.ManyToManyField(Transcript)

    def __str__(self):
        return self.topic
