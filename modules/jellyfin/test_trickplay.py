import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import threading
import types
import unittest


def load_module():
    package = types.ModuleType("jellyfin_kodi")
    package.__path__ = []
    helper = types.ModuleType("jellyfin_kodi.helper")
    helper.LazyLogger = lambda _name: types.SimpleNamespace(
        debug=lambda *_args: None,
        warning=lambda *_args: None,
    )
    helper.window = lambda *_args, **_kwargs: None
    utils = types.ModuleType("jellyfin_kodi.helper.utils")
    utils.translate_path = lambda path: path
    xbmc = types.ModuleType("xbmc")
    requests = types.ModuleType("requests")
    requests.get = lambda *_args, **_kwargs: None
    requests.Session = lambda: types.SimpleNamespace(headers={})

    sys.modules["jellyfin_kodi"] = package
    sys.modules["jellyfin_kodi.helper"] = helper
    sys.modules["jellyfin_kodi.helper.utils"] = utils
    sys.modules["xbmc"] = xbmc
    sys.modules["requests"] = requests

    path = pathlib.Path(
        os.environ.get(
            "TRICKPLAY_MODULE",
            pathlib.Path(__file__).with_name("trickplay.py"),
        )
    )
    spec = importlib.util.spec_from_file_location("jellyfin_kodi.trickplay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trickplay = load_module()

INFO = {
    "Interval": 10000,
    "ThumbnailCount": 140,
    "TileWidth": 10,
    "TileHeight": 10,
    "Width": 320,
    "Height": 240,
}


class PropertyStore(object):
    def __init__(self, initial=None):
        self.values = dict(initial or {})
        self.events = []

    def window(self, key, value=None, clear=False):
        self.events.append((key, value, clear))
        if clear:
            self.values.pop(key, None)
        elif value is not None:
            self.values[key] = value
        return self.values.get(key, "")


class TrickplayTest(unittest.TestCase):
    def setUp(self):
        self.original_window = trickplay.window
        self.original_preview_backoffs = trickplay.PREVIEW_RETRY_BACKOFFS
        self.original_chapter_backoffs = trickplay.CHAPTER_RETRY_BACKOFFS

    def tearDown(self):
        trickplay.window = self.original_window
        trickplay.PREVIEW_RETRY_BACKOFFS = self.original_preview_backoffs
        trickplay.CHAPTER_RETRY_BACKOFFS = self.original_chapter_backoffs

    def make_request(
        self,
        generation="7",
        target="60",
        direction=1,
        playback="playback-1",
        revision=3,
    ):
        return trickplay.make_preview_request(
            playback,
            revision,
            generation,
            target,
            INFO,
            direction,
        )

    def make_process_state(self, root, request, chapters=None):
        output_root = os.path.join(root, "output")
        state = {
            "playback_token": request["token"]["playback"],
            "revision": request["token"]["revision"],
            "info": INFO,
            "chapters": chapters or [],
            "request_slot": trickplay.LatestRequestSlot(),
            "prefetch_slot": trickplay.LatestRequestSlot(),
            "output_slots": trickplay.OutputSlots(output_root, 3),
            "publish_lock": threading.RLock(),
        }
        state["request_slot"].submit(request)
        return state

    def process_while_resolver_is_delayed(
        self,
        manager,
        state,
        request,
        source,
        while_delayed,
    ):
        started = threading.Event()
        release = threading.Event()
        calls = []
        results = []
        errors = []

        def resolve(_state, frame, _abort, foreground):
            calls.append((frame, foreground))
            started.set()
            if not release.wait(1):
                raise AssertionError("test did not release delayed resolver")
            if isinstance(source, Exception):
                raise source
            return source

        manager._resolve_frame_path = resolve

        def process():
            try:
                results.append(
                    manager._process_preview_request(
                        state,
                        request,
                        threading.Event(),
                    )
                )
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=process)
        worker.start()
        try:
            self.assertTrue(started.wait(1))
            while_delayed()
        finally:
            release.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        if errors:
            raise errors[0]
        return results, calls

    def assert_pending_request_publishes(
        self,
        manager,
        state,
        expected,
        source,
        store,
    ):
        pending = state["request_slot"].take(threading.Event())
        self.assertIs(pending, expected)
        manager._resolve_frame_path = lambda *_args, **_kwargs: source
        self.assertTrue(
            manager._process_preview_request(
                state,
                pending,
                threading.Event(),
            )
        )
        self.assertEqual(
            json.loads(store.values[trickplay.PREVIEW_TOKEN]),
            expected["token"],
        )

    def process_terminal_failure_while_clear_is_blocked(
        self,
        manager,
        state,
        request,
        abort,
        while_blocked,
    ):
        results = []
        errors = []

        class SignallingRLock(object):
            def __init__(self):
                self.lock = threading.RLock()
                self.armed = False
                self.attempted = threading.Event()

            def __enter__(self):
                if self.armed:
                    self.attempted.set()
                self.lock.acquire()
                return self

            def __exit__(self, *_args):
                self.lock.release()

        def fail(*_args, **_kwargs):
            raise trickplay.PreviewFailure("terminal", False)

        def process():
            try:
                results.append(
                    manager._process_preview_request(
                        state,
                        request,
                        abort,
                    )
                )
            except Exception as error:
                errors.append(error)

        property_lock = SignallingRLock()
        manager._property_lock = property_lock
        manager._resolve_frame_path = fail
        worker = threading.Thread(target=process)
        with property_lock:
            property_lock.armed = True
            worker.start()
            self.assertTrue(property_lock.attempted.wait(1))
            while_blocked()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        if errors:
            raise errors[0]
        return results

    @staticmethod
    def seed_preview_contract(store, request, path="/replacement-preview.jpg"):
        token = request["token"]
        values = {
            trickplay.PREVIEW_PATH: path,
            trickplay.PREVIEW_CHAPTER: "Replacement chapter",
            trickplay.PREVIEW_TARGET: request["target_text"],
            trickplay.PREVIEW_TOKEN: json.dumps(
                token,
                sort_keys=True,
                separators=(",", ":"),
            ),
            trickplay.PREVIEW_PLAYBACK: token["playback"],
            trickplay.PREVIEW_GENERATION: token["seek_generation"],
            trickplay.PREVIEW_SAMPLE: str(token["sample_seconds"]),
            trickplay.PREVIEW_FRAME: str(token["frame_index"]),
            trickplay.PREVIEW_REVISION: str(token["revision"]),
        }
        store.values.update(values)
        return values

    @staticmethod
    def write_file(path, content):
        with open(path, "wb") as output:
            output.write(content)
        return path

    def activate_request(self, store, request):
        store.values.update(
            {
                trickplay.SEEK_ACTIVE: "true",
                trickplay.SEEK_GENERATION: request["token"]["seek_generation"],
                trickplay.SEEK_TARGET: request["target_text"],
            }
        )

    def test_time_and_tile_helpers(self):
        self.assertEqual(trickplay.parse_time_label("01:02:03"), 3723)
        self.assertEqual(trickplay.format_time(3723), "1:02:03")
        self.assertIsNone(trickplay.parse_time_label("bad"))
        self.assertEqual(
            trickplay.tile_for_time(110, INFO),
            (11, 0, (320, 240, 640, 480)),
        )
        self.assertEqual(
            trickplay.tile_for_time(99999, INFO),
            (139, 1, (2880, 720, 3200, 960)),
        )

    def test_selects_nearest_complete_trickplay_resolution(self):
        metadata = {
            "source": {
                "160": dict(INFO, Width=160),
                "320": INFO,
                "640": dict(INFO, Width=640),
            }
        }
        width, selected = trickplay.select_trickplay(metadata, "source")
        self.assertEqual(width, 320)
        self.assertIs(selected, INFO)
        self.assertEqual(
            trickplay.select_trickplay({"source": {"320": {}}}, "source"),
            (None, None),
        )

    def test_sanitizes_chapters_and_marks_every_entry_kind(self):
        chapters = [
            {"Name": " Later\nname ", "StartPositionTicks": 300000000},
            {"Name": "Start\x00", "StartPositionTicks": 0},
            {"Name": "duplicate", "StartPositionTicks": 5000000},
            {"Name": "past end", "StartPositionTicks": 1000000000},
            {"Name": "negative", "StartPositionTicks": -1},
            {"Name": "invalid", "StartPositionTicks": "nan"},
        ]
        result = trickplay.sanitize_chapters(chapters, 100)
        self.assertEqual([entry["time_seconds"] for entry in result], [0, 30])
        self.assertEqual([entry["label"] for entry in result], ["Start", "Later name"])
        self.assertTrue(all(entry["kind"] == "chapter" for entry in result))
        self.assertTrue(all(entry["image"] == "" for entry in result))
        self.assertEqual(trickplay.sanitize_chapters(chapters, float("nan")), [])

    def test_sanitized_chapter_rail_is_bounded_to_image_cache_capacity(self):
        chapters = [
            {
                "Name": "Chapter %d" % index,
                "StartPositionTicks": index * 20000000,
            }
            for index in range(trickplay.CHAPTER_CACHE_LIMIT + 10)
        ]
        result = trickplay.sanitize_chapters(chapters, 1000)
        self.assertEqual(len(result), trickplay.CHAPTER_CACHE_LIMIT)

    def test_complete_preview_token_is_exact_and_machine_readable(self):
        request = self.make_request(generation="21", target="63.5", direction=-1)
        token = request["token"]
        self.assertEqual(
            set(token),
            {
                "schema",
                "playback",
                "seek_generation",
                "target_seconds",
                "sample_seconds",
                "frame_index",
                "revision",
            },
        )
        self.assertEqual(token["target_seconds"], 63.5)
        self.assertEqual(token["sample_seconds"], 60)
        self.assertEqual(token["frame_index"], 6)
        self.assertEqual(request["direction"], -1)

    def test_latest_request_slot_coalesces_to_one_pending_target(self):
        slot = trickplay.LatestRequestSlot()
        abort = threading.Event()
        first = self.make_request(target="10")
        second = self.make_request(target="20")
        third = self.make_request(target="30")
        slot.submit(first)
        slot.submit(second)
        slot.submit(third)
        self.assertEqual(slot.pending_count, 1)
        self.assertFalse(slot.is_latest(first["key"]))
        self.assertIs(slot.take(abort), third)
        self.assertEqual(slot.pending_count, 0)
        self.assertTrue(slot.is_latest(third["key"]))

    def test_same_frame_churn_publishes_exact_latest_target_once(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(target="61")
            updates = [
                self.make_request(target=target)
                for target in ("62", "64", "67", "69")
            ]
            latest = updates[-1]
            self.assertTrue(
                all(request["frame"] == first["frame"] for request in updates)
            )
            self.assertTrue(
                all(request["key"] == first["key"] for request in updates)
            )
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            state = self.make_process_state(root, first)
            manager = trickplay.TrickplayPreviewManager(None)

            def move_cursor():
                for update in updates:
                    state["request_slot"].submit(update)
                    self.activate_request(store, update)

            results, calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                first,
                source,
                move_cursor,
            )

            self.assertEqual(results, [True])
            self.assertEqual(calls, [(first["frame"], True)])
            self.assertEqual(state["request_slot"].pending_count, 0)
            self.assertEqual(
                json.loads(store.values[trickplay.PREVIEW_TOKEN]),
                latest["token"],
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_TARGET],
                latest["target_text"],
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_PLAYBACK],
                latest["token"]["playback"],
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_GENERATION],
                latest["token"]["seek_generation"],
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_SAMPLE],
                str(latest["token"]["sample_seconds"]),
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_FRAME],
                str(latest["token"]["frame_index"]),
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_REVISION],
                str(latest["token"]["revision"]),
            )

    def test_terminal_failure_preserves_newer_same_frame_request(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(target="61")
            latest = self.make_request(target="69")
            failure = trickplay.PreviewFailure("terminal", False)
            recovered = self.write_file(
                os.path.join(root, "recovered"),
                b"recovered",
            )
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            state = self.make_process_state(root, first)
            work = state["request_slot"].take(threading.Event())
            manager = trickplay.TrickplayPreviewManager(None)

            def submit_latest():
                state["request_slot"].submit(latest)
                self.activate_request(store, latest)

            results, calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                work,
                failure,
                submit_latest,
            )

            self.assertEqual(results, [False])
            self.assertEqual(calls, [(first["frame"], True)])
            self.assertEqual(state["request_slot"].pending_count, 1)
            self.assert_pending_request_publishes(
                manager,
                state,
                latest,
                recovered,
                store,
            )

    def test_exhausted_transient_preserves_newer_same_frame_request(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(target="61")
            latest = self.make_request(target="69")
            failure = trickplay.PreviewFailure("transient", True)
            recovered = self.write_file(
                os.path.join(root, "recovered"),
                b"recovered",
            )
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            trickplay.PREVIEW_RETRY_BACKOFFS = (0,)
            state = self.make_process_state(root, first)
            work = state["request_slot"].take(threading.Event())
            manager = trickplay.TrickplayPreviewManager(None)

            def submit_latest():
                state["request_slot"].submit(latest)
                self.activate_request(store, latest)

            results, calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                work,
                failure,
                submit_latest,
            )

            self.assertEqual(results, [False])
            self.assertEqual(
                calls,
                [(first["frame"], True), (first["frame"], True)],
            )
            self.assertEqual(state["request_slot"].pending_count, 1)
            self.assert_pending_request_publishes(
                manager,
                state,
                latest,
                recovered,
                store,
            )

    def test_submit_between_publish_and_discard_is_retained(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(target="61")
            latest = self.make_request(target="69")
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            state = self.make_process_state(root, first)

            class InterleavingSlot(trickplay.LatestRequestSlot):
                def __init__(self, submit_latest):
                    super(InterleavingSlot, self).__init__()
                    self.submit_latest = submit_latest
                    self.injected = False

                def discard_pending(self, request):
                    if not self.injected:
                        self.injected = True
                        self.submit_latest()
                    return super(InterleavingSlot, self).discard_pending(request)

            def submit_latest():
                state["request_slot"].submit(latest)
                self.activate_request(store, latest)

            slot = InterleavingSlot(submit_latest)
            state["request_slot"] = slot
            slot.submit(first)
            work = slot.take(threading.Event())
            manager = trickplay.TrickplayPreviewManager(None)
            manager._resolve_frame_path = lambda *_args, **_kwargs: source

            self.assertTrue(
                manager._process_preview_request(
                    state,
                    work,
                    threading.Event(),
                )
            )
            self.assertEqual(slot.pending_count, 1)
            self.assert_pending_request_publishes(
                manager,
                state,
                latest,
                source,
                store,
            )

    def test_abort_before_failure_clear_preserves_replacement(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request(target="61")
            pending = self.make_request(target="61")
            replacement = self.make_request(
                generation="9",
                target="90",
                playback="playback-replacement",
                revision=4,
            )
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            work = state["request_slot"].take(threading.Event())
            manager = trickplay.TrickplayPreviewManager(None)
            abort = threading.Event()
            replacement_values = {}

            def replace_while_blocked():
                abort.set()
                state["request_slot"].submit(pending)
                replacement_values.update(
                    self.seed_preview_contract(store, replacement)
                )

            results = self.process_terminal_failure_while_clear_is_blocked(
                manager,
                state,
                work,
                abort,
                replace_while_blocked,
            )

            self.assertEqual(results, [False])
            self.assertEqual(
                {key: store.values.get(key) for key in replacement_values},
                replacement_values,
            )
            self.assertIs(
                state["request_slot"].take(threading.Event()),
                pending,
            )

    def test_replacement_lifecycle_before_failure_clear_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request(generation="1", target="61")
            replacement = self.make_request(
                generation="2",
                target="62",
                playback="playback-replacement",
                revision=4,
            )
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            work = state["request_slot"].take(threading.Event())
            manager = trickplay.TrickplayPreviewManager(None)
            replacement_values = {}

            def replace_while_blocked():
                state["request_slot"].submit(replacement)
                self.activate_request(store, replacement)
                replacement_values.update(
                    self.seed_preview_contract(store, replacement)
                )

            results = self.process_terminal_failure_while_clear_is_blocked(
                manager,
                state,
                work,
                threading.Event(),
                replace_while_blocked,
            )

            self.assertEqual(results, [False])
            self.assertEqual(
                {key: store.values.get(key) for key in replacement_values},
                replacement_values,
            )
            self.assertIs(
                state["request_slot"].take(threading.Event()),
                replacement,
            )

    def test_frame_boundary_rejects_delayed_completion(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(target="69")
            latest = self.make_request(target="70")
            self.assertNotEqual(first["frame"], latest["frame"])
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            state = self.make_process_state(root, first)
            manager = trickplay.TrickplayPreviewManager(None)

            def cross_frame_boundary():
                state["request_slot"].submit(latest)
                self.activate_request(store, latest)

            results, calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                first,
                source,
                cross_frame_boundary,
            )

            self.assertEqual(results, [False])
            self.assertEqual(calls, [(first["frame"], True)])
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
            self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)
            self.assertIs(
                state["request_slot"].take(threading.Event()),
                latest,
            )

    def test_generation_change_rejects_same_frame_completion(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.make_request(generation="1", target="61")
            latest = self.make_request(generation="2", target="62")
            self.assertEqual(first["frame"], latest["frame"])
            self.assertNotEqual(first["key"], latest["key"])
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, first)
            trickplay.window = store.window
            state = self.make_process_state(root, first)
            manager = trickplay.TrickplayPreviewManager(None)

            def replace_generation():
                state["request_slot"].submit(latest)
                self.activate_request(store, latest)

            results, _calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                first,
                source,
                replace_generation,
            )

            self.assertEqual(results, [False])
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
            self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_media_or_revision_change_rejects_delayed_completion(self):
        replacements = (
            ("playback_token", "playback-2"),
            ("revision", 4),
        )
        for field, replacement in replacements:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                request = self.make_request(target="61")
                source = self.write_file(os.path.join(root, "frame"), b"frame")
                store = PropertyStore()
                self.activate_request(store, request)
                trickplay.window = store.window
                state = self.make_process_state(root, request)
                manager = trickplay.TrickplayPreviewManager(None)

                results, _calls = self.process_while_resolver_is_delayed(
                    manager,
                    state,
                    request,
                    source,
                    lambda: state.__setitem__(field, replacement),
                )

                self.assertEqual(results, [False])
                self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
                self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_unobserved_target_change_rejects_stale_completion(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request(target="61")
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)

            results, _calls = self.process_while_resolver_is_delayed(
                manager,
                state,
                request,
                source,
                lambda: store.values.__setitem__(trickplay.SEEK_TARGET, "62"),
            )

            self.assertEqual(results, [False])
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
            self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_output_slots_double_buffer_and_pin_active_path(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.write_file(os.path.join(root, "first"), b"first")
            second = self.write_file(os.path.join(root, "second"), b"second")
            third = self.write_file(os.path.join(root, "third"), b"third")
            slots = trickplay.OutputSlots(os.path.join(root, "output"), 2)

            path_a = slots.stage(first)
            slots.activate(path_a)
            path_b = slots.stage(second)
            self.assertNotEqual(path_a, path_b)
            with open(path_a, "rb") as active:
                self.assertEqual(active.read(), b"first")

            slots.activate(path_b)
            path_a_again = slots.stage(third)
            self.assertEqual(path_a_again, path_a)
            with open(path_b, "rb") as active:
                self.assertEqual(active.read(), b"second")

    def test_sprite_and_file_caches_are_strictly_bounded(self):
        byte_cache = trickplay.ByteLruCache(5)
        self.assertTrue(byte_cache.put("a", b"aaa"))
        self.assertTrue(byte_cache.put("b", b"bbb"))
        self.assertEqual(byte_cache.keys(), ["b"])
        self.assertEqual(byte_cache.byte_size, 3)
        self.assertFalse(byte_cache.put("huge", b"123456"))
        self.assertTrue(byte_cache.remove("b"))
        self.assertEqual(byte_cache.byte_size, 0)

        with tempfile.TemporaryDirectory() as root:
            paths = [
                self.write_file(os.path.join(root, str(index)), b"x")
                for index in range(3)
            ]
            frame_cache = trickplay.FileLruCache(1)
            chapter_cache = trickplay.FileLruCache(2)
            frame_cache.put("frame-a", paths[0])
            chapter_cache.put("chapter-a", paths[1])
            chapter_cache.put("chapter-b", paths[2])
            self.assertEqual(len(frame_cache), 1)
            self.assertEqual(len(chapter_cache), 2)
            self.assertEqual(frame_cache.get("frame-a"), paths[0])

    def test_exact_preview_never_falls_back_to_chapter_artwork(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request()
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)
            chapter_calls = []
            manager._download_chapter_image = (
                lambda *_args: chapter_calls.append(True)
            )
            manager._resolve_frame_path = lambda *_args, **_kwargs: (
                (_ for _ in ()).throw(
                    trickplay.PreviewFailure("no exact frame", False)
                )
            )

            self.assertFalse(
                manager._process_preview_request(
                    state,
                    request,
                    threading.Event(),
                )
            )
            self.assertEqual(chapter_calls, [])
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
            self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_stale_resolved_frame_is_rejected_before_publication(self):
        with tempfile.TemporaryDirectory() as root:
            old_request = self.make_request(generation="1", target="10")
            new_request = self.make_request(generation="2", target="20")
            old_path = self.write_file(os.path.join(root, "old"), b"old")
            new_path = self.write_file(os.path.join(root, "new"), b"new")
            store = PropertyStore()
            self.activate_request(store, old_request)
            trickplay.window = store.window
            state = self.make_process_state(root, old_request)
            manager = trickplay.TrickplayPreviewManager(None)

            def resolve(_state, frame, _abort, foreground):
                self.assertTrue(foreground)
                if frame == old_request["frame"]:
                    state["request_slot"].submit(new_request)
                    self.activate_request(store, new_request)
                    return old_path
                return new_path

            manager._resolve_frame_path = resolve
            self.assertFalse(
                manager._process_preview_request(
                    state,
                    old_request,
                    threading.Event(),
                )
            )
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)

            pending = state["request_slot"].take(threading.Event())
            self.assertIs(pending, new_request)
            self.assertTrue(
                manager._process_preview_request(
                    state,
                    pending,
                    threading.Event(),
                )
            )
            self.assertEqual(
                store.values[trickplay.PREVIEW_TARGET],
                new_request["target_text"],
            )

    def test_transient_exact_failure_retries_without_poisoning_frame(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request()
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            trickplay.PREVIEW_RETRY_BACKOFFS = (0,)
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)
            calls = []

            def resolve(_state, frame, _abort, foreground):
                calls.append((frame, foreground))
                if len(calls) == 1:
                    raise trickplay.PreviewFailure("temporary", True)
                return source

            manager._resolve_frame_path = resolve
            self.assertTrue(
                manager._process_preview_request(
                    state,
                    request,
                    threading.Event(),
                )
            )
            self.assertEqual(calls, [(6, True), (6, True)])
            self.assertIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_published_preview_has_complete_token_and_commit_order(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request(target="65")
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            chapters = [
                {
                    "kind": "chapter",
                    "id": "chapter-0000",
                    "index": 0,
                    "time_seconds": 0,
                    "label": "Opening",
                    "image": "",
                }
            ]
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request, chapters)
            manager = trickplay.TrickplayPreviewManager(None)
            manager._resolve_frame_path = lambda *_args, **_kwargs: source

            self.assertTrue(
                manager._process_preview_request(
                    state,
                    request,
                    threading.Event(),
                )
            )
            token = json.loads(store.values[trickplay.PREVIEW_TOKEN])
            self.assertEqual(token, request["token"])
            self.assertEqual(store.values[trickplay.PREVIEW_CHAPTER], "Opening")
            keys = [event[0] for event in store.events]
            self.assertLess(
                keys.index(trickplay.PREVIEW_PATH),
                keys.index(trickplay.PREVIEW_TOKEN),
            )
            self.assertLess(
                keys.index(trickplay.PREVIEW_TOKEN),
                keys.index(trickplay.PREVIEW_TARGET),
            )

    def test_abort_wins_against_a_preview_waiting_to_publish(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request()
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)
            manager._resolve_frame_path = lambda *_args, **_kwargs: source
            staged = threading.Event()
            real_slots = state["output_slots"]

            class SignallingSlots(object):
                def stage(self, source_path):
                    path = real_slots.stage(source_path)
                    staged.set()
                    return path

                def activate(self, path):
                    real_slots.activate(path)

                def clear(self):
                    real_slots.clear()

            state["output_slots"] = SignallingSlots()
            abort = threading.Event()
            results = []
            with manager._property_lock:
                publisher = threading.Thread(
                    target=lambda: results.append(
                        manager._process_preview_request(
                            state,
                            request,
                            abort,
                        )
                    )
                )
                publisher.start()
                self.assertTrue(staged.wait(1))
                abort.set()
            publisher.join(1)
            self.assertEqual(results, [False])
            self.assertNotIn(trickplay.PREVIEW_PATH, store.values)
            self.assertNotIn(trickplay.PREVIEW_TOKEN, store.values)

    def test_only_one_directional_neighbor_is_queued(self):
        slot = trickplay.LatestRequestSlot()
        manager = trickplay.TrickplayPreviewManager(None)
        state = {
            "info": INFO,
            "playback_token": "playback-1",
            "revision": 3,
            "prefetch_slot": slot,
        }
        manager._queue_one_neighbor(state, self.make_request(target="60", direction=1))
        self.assertEqual(slot.pending_count, 1)
        manager._queue_one_neighbor(state, self.make_request(target="90", direction=-1))
        self.assertEqual(slot.pending_count, 1)
        self.assertEqual(slot.take(threading.Event())["frame"], 8)

    def test_chapter_manifest_requires_two_and_is_tokened(self):
        with tempfile.TemporaryDirectory() as root:
            chapters = trickplay.sanitize_chapters(
                [
                    {"Name": "One", "StartPositionTicks": 0},
                    {"Name": "Two", "StartPositionTicks": 600000000},
                ],
                120,
            )
            chapters[0]["image"] = self.write_file(
                os.path.join(root, "one.jpg"),
                b"one",
            )
            chapters[1]["image"] = self.write_file(
                os.path.join(root, "two.jpg"),
                b"two",
            )
            state = {
                "chapters": chapters,
                "duration": 120,
                "playback_token": "playback-1",
                "revision": 3,
                "manifest_revision": 4,
                "chapter_lock": threading.RLock(),
            }
            store = PropertyStore()
            trickplay.window = store.window
            manager = trickplay.TrickplayPreviewManager(None)
            self.assertTrue(manager._publish_chapter_manifest(state))

            manifest = json.loads(store.values[trickplay.CHAPTER_MANIFEST])
            token = json.loads(store.values[trickplay.CHAPTER_TOKEN])
            self.assertEqual(manifest["playback"], "playback-1")
            self.assertEqual(manifest["manifest_revision"], 4)
            self.assertEqual(manifest["expected_count"], 2)
            self.assertTrue(
                all(entry["kind"] == "chapter" for entry in manifest["entries"])
            )
            self.assertTrue(
                all(os.path.isfile(entry["image"]) for entry in manifest["entries"])
            )
            self.assertEqual(
                token,
                {
                    "schema": 1,
                    "playback": "playback-1",
                    "revision": 3,
                    "manifest_revision": 4,
                },
            )
            self.assertEqual(store.values[trickplay.CHAPTER_AVAILABLE], "true")

            state["chapters"][1]["image"] = ""
            self.assertFalse(manager._publish_chapter_manifest(state))
            self.assertNotIn(trickplay.CHAPTER_AVAILABLE, store.values)
            self.assertNotIn(trickplay.CHAPTER_MANIFEST, store.values)

    def test_chapter_manifest_waits_for_every_retained_frame(self):
        with tempfile.TemporaryDirectory() as root:
            chapters = trickplay.sanitize_chapters(
                [
                    {"Name": "One", "StartPositionTicks": 0},
                    {"Name": "Two", "StartPositionTicks": 300000000},
                    {"Name": "Three", "StartPositionTicks": 600000000},
                ],
                120,
            )
            chapters[0]["image"] = self.write_file(
                os.path.join(root, "one.jpg"),
                b"one",
            )
            chapters[1]["image"] = self.write_file(
                os.path.join(root, "two.jpg"),
                b"two",
            )
            state = {
                "chapters": chapters,
                "duration": 120,
                "playback_token": "playback-1",
                "revision": 3,
                "manifest_revision": 2,
                "chapter_lock": threading.RLock(),
            }
            store = PropertyStore()
            trickplay.window = store.window
            manager = trickplay.TrickplayPreviewManager(None)

            self.assertFalse(manager._publish_chapter_manifest(state))
            self.assertNotIn(trickplay.CHAPTER_AVAILABLE, store.values)

            chapters[2]["image"] = self.write_file(
                os.path.join(root, "three.jpg"),
                b"three",
            )
            state["manifest_revision"] = 3
            self.assertTrue(manager._publish_chapter_manifest(state))
            manifest = json.loads(store.values[trickplay.CHAPTER_MANIFEST])
            self.assertEqual(len(manifest["entries"]), 3)
            self.assertEqual(manifest["expected_count"], 3)

    def test_stale_incomplete_lifecycle_cannot_clear_replacement_chapters(self):
        store = PropertyStore()
        trickplay.window = store.window
        manager = trickplay.TrickplayPreviewManager(None)
        store.values.update(
            {
                trickplay.CHAPTER_AVAILABLE: "true",
                trickplay.CHAPTER_PLAYBACK: "playback-new",
                trickplay.CHAPTER_MANIFEST: '{"new":true}',
                trickplay.CHAPTER_TOKEN: '{"new":true}',
                trickplay.CHAPTER_REVISION: "9",
            }
        )
        stale_state = {
            "chapters": [
                {
                    "kind": "chapter",
                    "id": "chapter-0000",
                    "index": 0,
                    "time_seconds": 0,
                    "image": "",
                },
                {
                    "kind": "chapter",
                    "id": "chapter-0001",
                    "index": 1,
                    "time_seconds": 60,
                    "image": "",
                },
            ],
            "playback_token": "playback-old",
            "chapter_lock": threading.RLock(),
        }

        self.assertFalse(manager._publish_chapter_manifest(stale_state))
        self.assertEqual(
            store.values[trickplay.CHAPTER_PLAYBACK],
            "playback-new",
        )
        self.assertEqual(
            store.values[trickplay.CHAPTER_AVAILABLE],
            "true",
        )

    def test_chapter_download_has_separate_cache_and_bounded_retry(self):
        with tempfile.TemporaryDirectory() as root:
            state = {
                "chapter_cache": trickplay.FileLruCache(2),
                "chapter_root": root,
                "item_id": "item",
                "client": object(),
                "chapter_session": object(),
                "background_network_lock": threading.Lock(),
            }
            entry = {
                "kind": "chapter",
                "id": "chapter-0001",
                "index": 1,
            }
            trickplay.CHAPTER_RETRY_BACKOFFS = (0,)
            manager = trickplay.TrickplayPreviewManager(None)
            calls = []

            def download(*_args, **_kwargs):
                calls.append(True)
                if len(calls) == 1:
                    raise OSError("temporary")
                return b"chapter"

            manager._download = download
            path = manager._download_chapter_image(
                state,
                entry,
                threading.Event(),
            )
            self.assertEqual(len(calls), 2)
            self.assertEqual(state["chapter_cache"].get(entry["id"]), path)
            with open(path, "rb") as image:
                self.assertEqual(image.read(), b"chapter")

    def test_failed_chapter_endpoint_uses_exact_trickplay_frame(self):
        with tempfile.TemporaryDirectory() as root:
            source = self.write_file(
                os.path.join(root, "exact-frame.jpg"),
                b"exact",
            )
            state = {
                "chapter_cache": trickplay.FileLruCache(2),
                "chapter_root": root,
                "item_id": "item",
                "client": object(),
                "chapter_session": object(),
                "background_network_lock": threading.Lock(),
                "info": INFO,
            }
            entry = {
                "kind": "chapter",
                "id": "chapter-0001",
                "index": 1,
                "time_seconds": 30.0,
            }
            manager = trickplay.TrickplayPreviewManager(None)
            manager._download = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("no chapter endpoint")
            )
            manager._resolve_frame_path = (
                lambda *_args, **_kwargs: source
            )

            path = manager._download_chapter_image(
                state,
                entry,
                threading.Event(),
            )
            self.assertIsNotNone(path)
            self.assertEqual(state["chapter_cache"].get(entry["id"]), path)
            with open(path, "rb") as image:
                self.assertEqual(image.read(), b"exact")

    def test_exact_and_neighbor_deduplicate_same_inflight_sprite(self):
        manager = trickplay.TrickplayPreviewManager(None)
        state = {
            "sprite_cache": trickplay.ByteLruCache(1024),
            "sprite_condition": threading.Condition(threading.RLock()),
            "inflight_sprites": set(),
            "background_network_lock": threading.Lock(),
            "item_id": "item",
            "width": 320,
            "media_source_id": "source",
            "client": object(),
            "exact_session": object(),
            "prefetch_session": object(),
        }
        download_started = threading.Event()
        release_download = threading.Event()
        calls = []
        results = []

        def download(*_args, **_kwargs):
            calls.append(True)
            download_started.set()
            release_download.wait(1)
            return b"sprite"

        manager._download = download
        abort = threading.Event()
        exact = threading.Thread(
            target=lambda: results.append(
                manager._load_sprite_data(state, 2, abort, True)
            )
        )
        neighbor = threading.Thread(
            target=lambda: results.append(
                manager._load_sprite_data(state, 2, abort, False)
            )
        )
        exact.start()
        self.assertTrue(download_started.wait(1))
        neighbor.start()
        release_download.set()
        exact.join(1)
        neighbor.join(1)
        self.assertEqual(calls, [True])
        self.assertEqual(results, [b"sprite", b"sprite"])

    def test_empty_sprite_response_is_transient_and_never_cached(self):
        manager = trickplay.TrickplayPreviewManager(None)
        state = {
            "sprite_cache": trickplay.ByteLruCache(1024),
            "sprite_condition": threading.Condition(threading.RLock()),
            "inflight_sprites": set(),
            "background_network_lock": threading.Lock(),
            "item_id": "item",
            "width": 320,
            "media_source_id": "source",
            "client": object(),
            "exact_session": object(),
            "prefetch_session": object(),
        }
        manager._download = lambda *_args, **_kwargs: b""
        with self.assertRaises(trickplay.PreviewFailure) as raised:
            manager._load_sprite_data(
                state,
                2,
                threading.Event(),
                True,
            )
        self.assertTrue(raised.exception.transient)
        self.assertIsNone(state["sprite_cache"].get(2))
        self.assertEqual(state["inflight_sprites"], set())

    def test_neighbor_and_chapter_share_one_background_network_slot(self):
        with tempfile.TemporaryDirectory() as root:
            manager = trickplay.TrickplayPreviewManager(None)
            shared_lock = threading.Lock()
            state = {
                "sprite_cache": trickplay.ByteLruCache(1024),
                "sprite_condition": threading.Condition(threading.RLock()),
                "inflight_sprites": set(),
                "background_network_lock": shared_lock,
                "item_id": "item",
                "width": 320,
                "media_source_id": "source",
                "client": object(),
                "exact_session": object(),
                "prefetch_session": object(),
                "chapter_session": object(),
                "chapter_cache": trickplay.FileLruCache(2),
                "chapter_root": root,
            }
            first_started = threading.Event()
            release_first = threading.Event()
            count_lock = threading.Lock()
            counters = {"calls": 0, "active": 0, "maximum": 0}

            def download(*_args, **_kwargs):
                with count_lock:
                    counters["calls"] += 1
                    counters["active"] += 1
                    counters["maximum"] = max(
                        counters["maximum"],
                        counters["active"],
                    )
                    call_number = counters["calls"]
                if call_number == 1:
                    first_started.set()
                    release_first.wait(1)
                with count_lock:
                    counters["active"] -= 1
                return b"image"

            manager._download = download
            abort = threading.Event()
            neighbor = threading.Thread(
                target=lambda: manager._load_sprite_data(
                    state,
                    1,
                    abort,
                    False,
                )
            )
            chapter = threading.Thread(
                target=lambda: manager._download_chapter_image(
                    state,
                    {"id": "chapter-0000", "index": 0},
                    abort,
                )
            )
            neighbor.start()
            self.assertTrue(first_started.wait(1))
            chapter.start()
            chapter.join(0.05)
            self.assertTrue(chapter.is_alive())
            release_first.set()
            neighbor.join(1)
            chapter.join(1)
            self.assertEqual(counters["calls"], 2)
            self.assertEqual(counters["maximum"], 1)

    def test_persistent_session_carries_token_in_header(self):
        class Session(object):
            def __init__(self):
                self.headers = {}

        original_session = trickplay.requests.Session
        trickplay.requests.Session = Session
        try:
            client = types.SimpleNamespace(
                config=types.SimpleNamespace(
                    data={"auth.token": "secret-token"}
                )
            )
            session = trickplay.TrickplayPreviewManager._new_session(client)
        finally:
            trickplay.requests.Session = original_session

        self.assertEqual(session.headers["X-Emby-Token"], "secret-token")
        self.assertEqual(session.headers["Accept"], "image/jpeg")

    def test_download_does_not_put_token_in_url_or_params(self):
        calls = []

        class Response(object):
            content = b"image"

            @staticmethod
            def raise_for_status():
                return None

        original_get = trickplay.requests.get
        trickplay.requests.get = lambda url, **kwargs: (
            calls.append((url, kwargs)) or Response()
        )
        try:
            client = types.SimpleNamespace(
                config=types.SimpleNamespace(
                    data={
                        "auth.server": "https://media.example",
                        "auth.token": "secret-token",
                        "auth.ssl": True,
                    }
                )
            )
            result = trickplay.TrickplayPreviewManager._download(
                client,
                "Videos/item/Trickplay/320/0.jpg",
                {"MediaSourceId": "source"},
            )
        finally:
            trickplay.requests.get = original_get

        self.assertEqual(result, b"image")
        url, kwargs = calls[0]
        self.assertNotIn("secret-token", url)
        self.assertNotIn("secret-token", repr(kwargs["params"]))
        self.assertEqual(kwargs["headers"]["X-Emby-Token"], "secret-token")


if __name__ == "__main__":
    unittest.main()
