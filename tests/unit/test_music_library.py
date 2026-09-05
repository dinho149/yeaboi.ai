"""The music library layer: vendor payloads onto one row, refusals onto one vocabulary."""

from __future__ import annotations

import re

import pytest

from yeaboi.connectors import library, library_apple, library_spotify, library_youtube, oauth


class FakeResponse:
    def __init__(self, status: int, body=None, headers: dict | None = None, content: bytes = b"{}"):
        self.status_code = status
        self._body = body
        self.headers = headers or {}
        self.content = content if body is not None or status == 204 else b""

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


# The desktop's link grammar, ported: every URL a row carries must pass it.
SPOTIFY_URL = re.compile(r"^https://open\.spotify\.com/(track|album|playlist|artist)/[A-Za-z0-9]{22}$")
YOUTUBE_URL = re.compile(r"^https://www\.youtube\.com/(watch\?v=[A-Za-z0-9_-]{11}|playlist\?list=[A-Za-z0-9_-]+)$")
APPLE_URL = re.compile(r"^https://music\.apple\.com/[a-z]{2}/album/[a-z0-9-]+/\d+(\?i=\d{4,})?$")

SPOTIFY_ID = "4uLU6hMCjMI75M1A2tKUQC"
SPOTIFY_TRACK = {
    "id": SPOTIFY_ID,
    "name": "Never Gonna Give You Up",
    "uri": f"spotify:track:{SPOTIFY_ID}",
    "duration_ms": 213573,
    "preview_url": "https://p.scdn.co/mp3-preview/x",
    "artists": [{"name": "Rick Astley"}],
    "album": {"name": "Whenever You Need Somebody", "images": [{"url": "big"}, {"url": "mid"}, {"url": "small"}]},
}
SPOTIFY_PLAYLIST = {
    "id": "37i9dQZF1DX8Uebhn9wzrS",
    "name": "Deep Focus",
    "uri": "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS",
    "owner": {"display_name": "Spotify"},
    "images": [{"url": "cover"}],
    "tracks": {"total": 120},
}


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    library._cache.clear()
    oauth.reset_cache()
    monkeypatch.setattr(oauth, "bearer_for", lambda key: "AT")
    yield
    library._cache.clear()


def serve(monkeypatch, status=200, body=None, headers=None, calls=None):
    def fake_get(url, *, headers=None, timeout=10):
        if calls is not None:
            calls.append(url)
        return FakeResponse(status, body, headers)

    monkeypatch.setattr(library.http, "get_json", fake_get)


class TestSpotifyRows:
    def test_a_track_row_carries_a_share_link_the_desktop_parses(self):
        item = library_spotify.track_item(SPOTIFY_TRACK)
        assert item.kind == "track" and item.title == "Never Gonna Give You Up"
        assert item.subtitle == "Rick Astley · Whenever You Need Somebody"
        assert item.artwork_url == "mid" and item.duration_ms == 213573
        assert SPOTIFY_URL.match(item.url) and item.uri == f"spotify:track:{SPOTIFY_ID}"
        assert item.preview_url

    def test_a_playlist_row(self):
        item = library_spotify.playlist_item(SPOTIFY_PLAYLIST)
        assert item.kind == "playlist" and item.subtitle == "Spotify" and item.count == 120
        assert SPOTIFY_URL.match(item.url)

    def test_junk_rows_are_dropped(self):
        assert library_spotify.track_item({}) is None
        assert library_spotify.track_item(None) is None
        assert library_spotify.album_item({"name": "no id"}) is None

    def test_the_liked_shelf_pages_by_offset(self, monkeypatch):
        calls: list[str] = []
        serve(monkeypatch, 200, {"items": [{"track": SPOTIFY_TRACK}], "total": 45, "next": "…"}, calls=calls)
        page = library_spotify.library("liked", "", 20)
        assert [i.id for i in page.items] == [SPOTIFY_ID] and page.next_cursor == "20"
        assert calls[0].startswith("https://api.spotify.com/v1/me/tracks?") and "offset=0" in calls[0]
        last = library_spotify.library("liked", "40", 20)
        assert last.next_cursor == "" or calls[-1].endswith("offset=40")

    def test_recently_played_pages_by_cursor(self, monkeypatch):
        serve(monkeypatch, 200, {"items": [{"track": SPOTIFY_TRACK}], "cursors": {"before": "1700"}, "next": "…"})
        page = library_spotify.library("recent", "", 20)
        assert page.next_cursor == "1700"

    def test_an_unknown_shelf_is_a_400(self):
        with pytest.raises(library.LibraryError) as info:
            library_spotify.library("moods")
        assert info.value.code == "unsupported_shelf" and info.value.status == 400

    def test_search_merges_the_three_kinds_and_caps_at_ten(self, monkeypatch):
        calls: list[str] = []
        serve(
            monkeypatch,
            200,
            {"tracks": {"items": [SPOTIFY_TRACK]}, "albums": {"items": []}, "playlists": {"items": [SPOTIFY_PLAYLIST]}},
            calls=calls,
        )
        page = library_spotify.search("rick", 50)
        assert [i.kind for i in page.items] == ["track", "playlist"]
        assert "limit=10" in calls[0]

    def test_play_builds_the_right_body_and_refuses_a_bad_uri(self, monkeypatch):
        sent: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            library.http,
            "put_json",
            lambda url, *, headers, payload, timeout=10: sent.append((url, payload)) or FakeResponse(204),
        )
        library_spotify.play(f"spotify:track:{SPOTIFY_ID}")
        library_spotify.play("spotify:playlist:37i9dQZF1DX8Uebhn9wzrS", "dev1")
        assert sent[0] == ("https://api.spotify.com/v1/me/player/play", {"uris": [f"spotify:track:{SPOTIFY_ID}"]})
        assert sent[1][0].endswith("?device_id=dev1") and sent[1][1] == {
            "context_uri": "spotify:playlist:37i9dQZF1DX8Uebhn9wzrS"
        }
        with pytest.raises(library.LibraryError, match="not a Spotify URI"):
            library_spotify.play("spotify:track:../evil")

    def test_player_reads_nothing_playing_as_quiet(self, monkeypatch):
        serve(monkeypatch, 204, None)
        assert library_spotify.player() == {"playing": False, "progress_ms": 0, "item": None, "device": None}
        serve(
            monkeypatch,
            200,
            {"is_playing": True, "progress_ms": 12000, "item": SPOTIFY_TRACK, "device": {"id": "d", "name": "Mac"}},
        )
        now = library_spotify.player()
        assert now["playing"] and now["item"]["id"] == SPOTIFY_ID and now["device"] == {"id": "d", "name": "Mac"}


class TestYouTubeRows:
    def test_a_playlist_item_row_is_a_watch_link(self):
        row = {
            "snippet": {
                "title": "Song",
                "videoOwnerChannelTitle": "Artist",
                "resourceId": {"videoId": "dQw4w9WgXcQ"},
                "thumbnails": {"medium": {"url": "thumb"}},
            }
        }
        item = library_youtube.video_item(row)
        assert item.kind == "video" and item.subtitle == "Artist" and item.artwork_url == "thumb"
        assert YOUTUBE_URL.match(item.url)

    def test_private_and_deleted_videos_are_dropped(self):
        row = {"snippet": {"title": "Private video", "resourceId": {"videoId": "dQw4w9WgXcQ"}}}
        assert library_youtube.video_item(row) is None

    def test_a_playlist_row_and_page_tokens(self, monkeypatch):
        calls: list[str] = []
        body = {
            "items": [
                {"id": "PLx", "snippet": {"title": "Focus", "channelTitle": "Me"}, "contentDetails": {"itemCount": 9}}
            ],
            "nextPageToken": "CAUQAA",
        }
        serve(monkeypatch, 200, body, calls=calls)
        page = library_youtube.library("playlists", "", 20)
        assert page.items[0].count == 9 and YOUTUBE_URL.match(page.items[0].url)
        assert page.next_cursor == "CAUQAA" and "mine=true" in calls[0]

    def test_liked_is_the_ll_playlist_and_albums_are_unsupported(self, monkeypatch):
        calls: list[str] = []
        serve(monkeypatch, 200, {"items": []}, calls=calls)
        library_youtube.library("liked")
        assert "playlistId=LL" in calls[0]
        with pytest.raises(library.LibraryError) as info:
            library_youtube.library("albums")
        assert info.value.status == 400

    def test_search_stays_in_the_music_category_and_is_cached(self, monkeypatch):
        calls: list[str] = []
        serve(monkeypatch, 200, {"items": [{"id": {"videoId": "dQw4w9WgXcQ"}, "snippet": {"title": "S"}}]}, calls=calls)
        first = library_youtube.search("rick")
        second = library_youtube.search("Rick ")
        assert first is second and len(calls) == 1 and "videoCategoryId=10" in calls[0]


class TestAppleRows:
    def test_a_song_result_is_an_album_link_with_the_track_and_a_preview(self, monkeypatch):
        body = {
            "results": [
                {
                    "wrapperType": "track",
                    "trackId": 1440935467,
                    "collectionId": 1440935400,
                    "trackName": "Get Lucky",
                    "artistName": "Daft Punk",
                    "collectionName": "Random Access Memories (Deluxe)",
                    "artworkUrl100": "https://a/100x100bb.jpg",
                    "trackTimeMillis": 369000,
                    "previewUrl": "https://p/preview.m4a",
                },
                {
                    "wrapperType": "collection",
                    "collectionId": 5,
                    "collectionName": "RAM",
                    "artistName": "Daft Punk",
                    "trackCount": 13,
                },
                {"wrapperType": "artist", "artistId": 1},
            ]
        }
        monkeypatch.setattr(library_apple.http, "get_json", lambda url, headers, timeout=10: FakeResponse(200, body))
        page = library_apple.search("daft punk", 20, "GB")
        song, album = page.items
        assert (
            song.kind == "song"
            and APPLE_URL.match(song.url)
            and song.url.startswith("https://music.apple.com/gb/album/")
        )
        assert (
            song.url.endswith("/1440935400?i=1440935467")
            and song.preview_url
            and song.artwork_url.endswith("300x300bb.jpg")
        )
        assert album.kind == "album" and album.count == 13 and APPLE_URL.match(album.url)

    def test_a_bad_country_is_us_and_a_slug_is_never_empty(self):
        assert library_apple._country("zzz") == "us"
        assert library_apple.slug("!!!") == "album"
        assert library_apple.slug("Random Access Memories (Deluxe)") == "random-access-memories-deluxe"


class TestRefusals:
    @pytest.mark.parametrize(
        ("status", "body", "code", "http_status"),
        [
            (401, {}, "signed_out", 409),
            (403, {"error": {"status": 403, "reason": "PREMIUM_REQUIRED"}}, "premium_required", 403),
            (403, {"error": {"errors": [{"reason": "quotaExceeded"}]}}, "quota_exceeded", 429),
            (403, {"error": "forbidden"}, "not_allowlisted", 403),
            (404, {"error": {"reason": "NO_ACTIVE_DEVICE"}}, "no_active_device", 409),
            (429, {}, "rate_limited", 429),
            (503, None, "unavailable", 502),
        ],
    )
    def test_every_vendor_refusal_has_one_name(self, status, body, code, http_status):
        error = library.translate("spotify", FakeResponse(status, body, {"Retry-After": "7"}))
        assert error.code == code and error.status == http_status
        if code == "rate_limited":
            assert error.retry_after == 7 and error.as_dict()["retry_after"] == 7
        assert set(error.as_dict()) >= {"error", "code"}

    def test_a_401_gets_one_forced_refresh_then_signs_out(self, monkeypatch):
        tokens = iter(["AT-old", "AT-new"])
        monkeypatch.setattr(oauth, "bearer_for", lambda key: next(tokens))
        seen: list[str] = []

        def fake_get(url, *, headers, timeout=10):
            seen.append(headers["Authorization"])
            return FakeResponse(401, {})

        monkeypatch.setattr(library.http, "get_json", fake_get)
        with pytest.raises(library.LibraryError) as info:
            library.vendor_get("spotify", "https://api.spotify.com/v1/me")
        assert seen == ["Bearer AT-old", "Bearer AT-new"] and info.value.code == "signed_out"

    def test_a_missing_token_is_signed_out_before_any_request(self, monkeypatch):
        monkeypatch.setattr(
            oauth, "bearer_for", lambda key: (_ for _ in ()).throw(oauth.SignedOutError("Sign in to Spotify"))
        )
        with pytest.raises(library.LibraryError) as info:
            library_spotify.library("playlists")
        assert info.value.code == "signed_out" and info.value.status == 409

    def test_a_transport_failure_is_unavailable_and_names_no_url(self, monkeypatch):
        def boom(url, *, headers, timeout=10):
            raise ConnectionError("https://api.spotify.com/v1/me?token=SECRET")

        monkeypatch.setattr(library.http, "get_json", boom)
        with pytest.raises(library.LibraryError) as info:
            library.vendor_get("spotify", "https://api.spotify.com/v1/me")
        assert info.value.code == "unavailable" and "SECRET" not in info.value.message


class TestCache:
    def test_pages_are_kept_and_forgotten_per_service(self, monkeypatch):
        calls: list[str] = []
        serve(monkeypatch, 200, {"items": [], "total": 0}, calls=calls)
        library_spotify.library("playlists")
        library_spotify.library("playlists")
        assert len(calls) == 1
        library.forget("spotify")
        library_spotify.library("playlists")
        assert len(calls) == 2

    def test_the_cache_is_bounded(self):
        for i in range(library.CACHE_ENTRIES + 10):
            library.cached(("k", i), 60, lambda: library.Page())
        assert len(library._cache) <= library.CACHE_ENTRIES

    def test_limits_clamp(self):
        assert library.clamp_limit("") == 20 and library.clamp_limit("abc") == 20
        assert library.clamp_limit("999") == library.MAX_LIMIT and library.clamp_limit(0) == 1
