from youtube_transcript_api import YouTubeTranscriptApi
import json

try:
    transcript = YouTubeTranscriptApi.get_transcript('W9GDWKzf1mc')
    text = " ".join([x['text'] for x in transcript])
    print(text)
except Exception as e:
    print(e)
