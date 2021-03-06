from django.conf.urls import url, include
from rest_framework import routers

from .views import (
    TranscriptViewSet, TranscriptCreateViewset, TranscriptPhraseVoteViewSet,
    TranscriptStatsViewSet, TranscriptPhraseDetailViewSet,
    TranscriptPhraseCorrectionViewSet, SourceViewSet,
    TranscriptPhraseCorrectionVoteViewSet,
    TopicViewSet, ProfileViewSet, ProfileStatsViewSet,
    ScoreViewSet, LeaderboardView, LoadingScreenView, MessageView,
    ContributionStatisticsView
)


router = routers.DefaultRouter()
router.register(r'transcript', TranscriptViewSet)
router.register(r'add-transcript', TranscriptCreateViewset)
router.register(r'statistics', TranscriptStatsViewSet)
router.register(r'phrase', TranscriptPhraseDetailViewSet)
router.register(r'transcriptphrasevote', TranscriptPhraseVoteViewSet)
router.register(r'transcriptphrasecorrection',
                TranscriptPhraseCorrectionViewSet)
router.register(r'transcriptphrasecorrectionvote',
                TranscriptPhraseCorrectionVoteViewSet)
router.register(r'source', SourceViewSet)
router.register(r'topic', TopicViewSet)
router.register(r'profile', ProfileViewSet)
router.register(r'profilestats', ProfileStatsViewSet)
router.register(r'score', ScoreViewSet)


urlpatterns = [
    url(r'^', include(router.urls)),
    url(r'^leaderboard/$', LeaderboardView.as_view()),
    url(r'^loading/$', LoadingScreenView.as_view()),
    url(r'^message/$', MessageView.as_view()),
    url(r'^contributions/$', ContributionStatisticsView.as_view()),
]
