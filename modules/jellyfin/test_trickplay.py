import importlib.util
import io
import json
import os
import pathlib
import sys
import tempfile
import threading
import types
import unittest


def load_module():
    path = pathlib.Path(
        os.environ.get(
            "TRICKPLAY_MODULE",
            pathlib.Path(__file__).with_name("trickplay.py"),
        )
    )
    package = types.ModuleType("jellyfin_kodi")
    package.__path__ = [str(path.parent)]
    helper = types.ModuleType("jellyfin_kodi.helper")
    helper.LazyLogger = lambda _name: types.SimpleNamespace(
        debug=lambda *_args: None,
        info=lambda *_args: None,
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

    spec = importlib.util.spec_from_file_location("jellyfin_kodi.trickplay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trickplay = load_module()


def load_media_contract():
    root = pathlib.Path(
        os.environ.get(
            "HTPC_SETTINGS_ROOT",
            pathlib.Path(__file__).parents[1] / "kodi-settings-addon",
        )
    )
    spec = importlib.util.spec_from_file_location(
        "htpc_media_contract",
        root / "media_contract.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


media_contract = load_media_contract()

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
        self.original_log = trickplay.LOG
        self.original_preview_backoffs = trickplay.PREVIEW_RETRY_BACKOFFS
        self.original_stationary_retry = (
            trickplay.PREVIEW_STATIONARY_RETRY_SECONDS
        )
        self.original_metadata_delays = (
            trickplay.METADATA_TRANSIENT_RETRY_DELAYS
        )

    def tearDown(self):
        trickplay.window = self.original_window
        trickplay.LOG = self.original_log
        trickplay.PREVIEW_RETRY_BACKOFFS = self.original_preview_backoffs
        trickplay.PREVIEW_STATIONARY_RETRY_SECONDS = (
            self.original_stationary_retry
        )
        trickplay.METADATA_TRANSIENT_RETRY_DELAYS = (
            self.original_metadata_delays
        )

    def make_request(
        self,
        generation="7",
        target="60",
        direction=1,
        playback="playback-1",
        revision=3,
    ):
        request = trickplay.make_preview_request(
            playback,
            revision,
            generation,
            target,
            INFO,
            direction,
        )
        request["token"].update(
            {
                "consumer_nonce": "consumer-1",
                "playback_epoch": 2,
            }
        )
        return request

    def make_process_state(self, root, request, chapters=None):
        state = {
            "playback_token": request["token"]["playback"],
            "revision": request["token"]["revision"],
            "item_id": "item-1",
            "media_source_id": "source-1",
            "width": 320,
            "info": INFO,
            "chapters": chapters or [],
            "request_slot": trickplay.LatestRequestSlot(),
            "publish_lock": threading.RLock(),
            "preview_failure_diagnostics": {},
            "frame_cache": trickplay.PlaybackFrameCache(
                os.path.join(root, "frames"),
                byte_limit=1024 * 1024,
            ),
            "chapter_frames": set(),
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
        contract = json.loads(store.values[trickplay.PREVIEW_CONTRACT])
        self.assertEqual(contract["target_seconds"], expected["token"]["target_seconds"])
        self.assertEqual(contract["status"], "ready")

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
        contract = dict(request["token"])
        contract.update({"status": "ready", "path": path})
        values = {trickplay.PREVIEW_CONTRACT: json.dumps(
            contract, sort_keys=True, separators=(",", ":")
        )}
        store.values.update(values)
        return values

    @staticmethod
    def write_file(path, content):
        with open(path, "wb") as output:
            output.write(content)
        return path

    def activate_request(self, store, request):
        request["token"].setdefault("playback_epoch", 2)
        request["token"].setdefault("consumer_nonce", "consumer-1")
        store.values[trickplay.SEEK_REQUEST] = json.dumps(
            {
                "schema": 1,
                "active": True,
                "generation": int(request["token"]["seek_generation"]),
                "target_seconds": int(round(request["target_seconds"])),
                "playback_epoch": request["token"]["playback_epoch"],
                "consumer_nonce": request["token"]["consumer_nonce"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def test_warm_sprite_decodes_once_and_extracts_every_frame(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as root:
            sprite = Image.new("RGB", (20, 20))
            colors = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0))
            for index, color in enumerate(colors):
                left = (index % 2) * 10
                top = (index // 2) * 10
                tile = Image.new("RGB", (10, 10), color)
                sprite.paste(tile, (left, top))
                tile.close()
            encoded = io.BytesIO()
            sprite.save(encoded, "JPEG", quality=95)
            sprite.close()

            info = dict(
                INFO,
                ThumbnailCount=4,
                TileWidth=2,
                TileHeight=2,
                Width=10,
                Height=10,
            )
            frame_root = os.path.join(root, "frames")
            state = {
                "info": info,
                "revision": 2,
                "frame_root": frame_root,
                "frame_cache": trickplay.PlaybackFrameCache(
                    frame_root,
                    byte_limit=1024 * 1024,
                ),
                "sprite_cache": trickplay.ByteLruCache(1024 * 1024),
                "warmed_sprites": set(),
            }
            calls = []
            manager = trickplay.TrickplayPreviewManager(None)
            manager._load_sprite_data = lambda *_args, **_kwargs: (
                calls.append(0) or encoded.getvalue()
            )

            self.assertTrue(manager._warm_sprite(state, 0, threading.Event()))
            self.assertEqual(calls, [0])
            self.assertEqual(len(state["frame_cache"]), 4)
            paths = [state["frame_cache"].get(frame) for frame in range(4)]
            self.assertTrue(all(path and os.path.isfile(path) for path in paths))
            self.assertEqual(len(set(paths)), 4)
            self.assertFalse(manager._warm_sprite(state, 0, threading.Event()))
            self.assertEqual(calls, [0])

            os.unlink(paths[2])
            self.assertTrue(
                manager._warm_sprite(
                    state,
                    0,
                    threading.Event(),
                    priority_frame=2,
                )
            )
            self.assertEqual(calls, [0, 0])
            self.assertTrue(os.path.isfile(state["frame_cache"].get(2)))

    def test_frame_cache_keeps_active_pin_under_pressure(self):
        with tempfile.TemporaryDirectory() as root:
            cache = trickplay.PlaybackFrameCache(root, byte_limit=8)
            active = self.write_file(os.path.join(root, "active"), b"1234")
            other = self.write_file(os.path.join(root, "other"), b"5678")
            newest = self.write_file(os.path.join(root, "newest"), b"abcd")
            self.assertTrue(cache.put(1, active))
            self.assertTrue(cache.put(2, other))
            cache.set_pins({1})
            self.assertTrue(cache.put(3, newest))
            self.assertEqual(cache.get(1), active)
            self.assertTrue(os.path.isfile(active))
            self.assertIsNone(cache.get(2))

    def test_whole_title_sprite_order_expands_from_current_position(self):
        info = dict(INFO, ThumbnailCount=550)
        self.assertEqual(
            trickplay.sprite_order(info, current_frame=250),
            [2, 3, 1, 4, 0, 5],
        )

    def test_playback_frame_cache_is_byte_bounded_and_preserves_pins(self):
        with tempfile.TemporaryDirectory() as root:
            cache = trickplay.PlaybackFrameCache(root, byte_limit=8)
            paths = []
            for index, content in enumerate((b"aaaa", b"bbbb", b"cccc")):
                path = self.write_file(
                    os.path.join(root, "frame-%d.jpg" % index),
                    content,
                )
                paths.append(path)
            cache.pin([0])
            self.assertTrue(cache.put(0, paths[0]))
            self.assertTrue(cache.put(1, paths[1]))
            self.assertTrue(cache.put(2, paths[2]))
            self.assertEqual(cache.byte_size, 8)
            self.assertEqual(cache.get(0), paths[0])
            self.assertIsNone(cache.get(1))
            self.assertEqual(cache.get(2), paths[2])

    def test_work_queue_prefers_latest_request_and_promotes_its_sprite(self):
        queue = trickplay.PlaybackWorkQueue([0, 1, 2])
        abort = threading.Event()
        first = {"frame": 100}
        latest = {"frame": 200}
        queue.submit_request(first, 1)
        queue.submit_request(latest, 2)
        self.assertEqual(queue.take(abort), ("request", latest))
        self.assertEqual(queue.take(abort), ("warm", 2))
        self.assertEqual(queue.remaining, 2)

    def test_real_pillow_sprite_crop_publishes_valid_consumer_contract(self):
        from PIL import Image

        info = {
            "Interval": 1000,
            "ThumbnailCount": 2,
            "TileWidth": 2,
            "TileHeight": 1,
            "Width": 10,
            "Height": 10,
        }
        sprite = Image.new("RGB", (20, 10), (255, 0, 0))
        sprite.paste((0, 0, 255), (10, 0, 20, 10))
        encoded = io.BytesIO()
        sprite.save(encoded, "JPEG", quality=100, subsampling=0)
        sprite.close()

        with tempfile.TemporaryDirectory() as root:
            request = trickplay.make_preview_request(
                "playback-1",
                3,
                "7",
                "1",
                info,
                1,
            )
            state = self.make_process_state(root, request)
            frame_root = os.path.join(root, "frames")
            os.makedirs(frame_root, exist_ok=True)
            state.update(
                {
                    "info": info,
                    "item_id": "private-item",
                    "media_source_id": "private-source",
                    "width": 10,
                    "client": object(),
                    "frame_root": frame_root,
                    "sprite_cache": trickplay.ByteLruCache(1024 * 1024),
                    "frame_cache": trickplay.PlaybackFrameCache(
                        frame_root,
                        byte_limit=1024 * 1024,
                    ),
                    "chapter_frames": set(),
                    "sprite_condition": threading.Condition(threading.RLock()),
                    "inflight_sprites": set(),
                    "background_network_lock": threading.Lock(),
                    "decoded_sprites": trickplay.OrderedDict(),
                    "decode_lock": threading.RLock(),
                    "exact_session": object(),
                    "prefetch_session": object(),
                }
            )
            store = PropertyStore()
            self.activate_request(store, request)
            messages = []
            trickplay.window = store.window
            trickplay.LOG = types.SimpleNamespace(
                debug=lambda *_args: None,
                info=lambda message, *args: messages.append(
                    message % args if args else message
                ),
                warning=lambda *_args: None,
            )
            manager = trickplay.TrickplayPreviewManager(None)
            manager._download = lambda *_args, **_kwargs: encoded.getvalue()

            pending = state["request_slot"].take(threading.Event())
            self.assertTrue(
                manager._process_preview_request(
                    state,
                    pending,
                    threading.Event(),
                )
            )

            published = json.loads(
                store.values[trickplay.PREVIEW_CONTRACT]
            )["path"]
            with Image.open(published) as cropped:
                self.assertEqual(cropped.size, (10, 10))
                red, _green, blue = cropped.getpixel((5, 5))
                self.assertGreater(blue, red)

            self.assertEqual(
                media_contract.validated_preview(
                    store.values,
                    {
                        "active": True,
                        "generation": 7,
                        "target_seconds": 1,
                        "consumer_nonce": "consumer-1",
                        "playback_epoch": 2,
                    },
                ),
                published,
            )
            diagnostics = "\n".join(messages)
            for stage in ("download", "decode", "crop", "publication"):
                self.assertIn("stage=%s outcome=ready" % stage, diagnostics)
            for private_value in (
                "private-item",
                "private-source",
                published,
            ):
                self.assertNotIn(private_value, diagnostics)

    def test_selects_nearest_complete_trickplay_resolution(self):
        metadata = {
            "source": {
                "160": dict(INFO, Width=160),
                "320": INFO,
                "640": dict(INFO, Width=640),
            }
        }
        source, width, selected = trickplay.select_trickplay(metadata, "source")
        self.assertEqual(source, "source")
        self.assertEqual(width, 320)
        self.assertEqual(selected, INFO)
        self.assertEqual(
            trickplay.select_trickplay({"source": {"320": {}}}, "source"),
            (None, None, None),
        )

    def test_diagnostic_media_kind_is_allowlisted(self):
        self.assertEqual(trickplay._media_kind("Movie"), "Movie")
        self.assertEqual(trickplay._media_kind("Episode"), "Episode")
        self.assertEqual(trickplay._media_kind("private-title"), "unknown")

    def test_success_diagnostic_never_breaks_a_preview(self):
        original_log = trickplay.LOG
        calls = []

        def broken_logger(*_args):
            calls.append("attempted")
            raise RuntimeError("logger unavailable")

        try:
            trickplay.LOG = types.SimpleNamespace(info=broken_logger)
            state = {}
            self.assertTrue(
                trickplay._log_stage_once(
                    state,
                    "publication",
                    "ready",
                    "contract",
                )
            )
            self.assertFalse(
                trickplay._log_stage_once(
                    state,
                    "publication",
                    "ready",
                    "contract",
                )
            )
            self.assertEqual(calls, ["attempted"])
            self.assertIn("diagnostics_lock", state)
        finally:
            trickplay.LOG = original_log

    def test_fallback_manifest_replaces_stale_playback_source(self):
        manifest = {
            "manifest-source": {
                "320": INFO,
            }
        }
        source, width, selected = trickplay.select_trickplay(
            manifest,
            "stale-playback-source",
        )
        self.assertEqual(source, "manifest-source")
        self.assertEqual(width, 320)
        self.assertEqual(selected, INFO)

    def test_flattened_manifest_retains_known_playback_source(self):
        source, width, selected = trickplay.select_trickplay(
            {"320": INFO},
            "playback-source",
        )
        self.assertEqual(source, "playback-source")
        self.assertEqual(width, 320)
        self.assertEqual(selected, INFO)

    def test_exact_source_wins_and_invalid_exact_never_falls_back(self):
        alternate = dict(INFO, Width=640)
        manifest = {
            "wanted": {"320": INFO},
            "alternate": {"640": alternate},
        }
        source, width, selected = trickplay.select_trickplay(
            manifest,
            "wanted",
        )
        self.assertEqual((source, width), ("wanted", 320))
        self.assertEqual(selected, INFO)

        manifest["wanted"] = {"320": dict(INFO, Interval=0)}
        self.assertEqual(
            trickplay.select_trickplay(manifest, "wanted"),
            (None, None, None),
        )

    def test_stale_source_fallback_requires_one_valid_nested_source(self):
        one_valid = {
            "invalid": {"320": dict(INFO, Height=float("nan"))},
            "only-valid": {"320": INFO},
        }
        self.assertEqual(
            trickplay.select_trickplay(one_valid, "stale")[:2],
            ("only-valid", 320),
        )

        ambiguous = dict(one_valid, second={"320": INFO})
        self.assertEqual(
            trickplay.select_trickplay(ambiguous, "stale"),
            (None, None, None),
        )

    def test_numeric_source_key_is_nested_not_flattened(self):
        manifest = {
            "123": {"320": INFO},
        }
        source, width, selected = trickplay.select_trickplay(manifest, 123)
        self.assertEqual((source, width), ("123", 320))
        self.assertEqual(selected, INFO)

    def test_malformed_or_mixed_manifests_are_unavailable(self):
        invalid_values = (0, -1, 1.5, float("nan"), float("inf"), True)
        for value in invalid_values:
            with self.subTest(interval=value):
                manifest = {
                    "source": {"320": dict(INFO, Interval=value)},
                }
                self.assertEqual(
                    trickplay.select_trickplay(manifest, "source"),
                    (None, None, None),
                )

        width_mismatch = {"source": {"640": INFO}}
        self.assertEqual(
            trickplay.select_trickplay(width_mismatch, "source"),
            (None, None, None),
        )
        mixed = {
            "320": INFO,
            "source": {"320": INFO},
        }
        self.assertEqual(
            trickplay.select_trickplay(mixed, "stale"),
            (None, None, None),
        )

    def test_lifecycle_carries_selected_source_into_runtime_state(self):
        with tempfile.TemporaryDirectory() as root:
            item = {
                "Id": "item-1",
                "MediaSourceId": "stale-playback-source",
                "Server": object(),
            }
            metadata = {
                "Trickplay": {
                    "manifest-source": {"320": INFO},
                }
            }
            abort = threading.Event()
            selected_sources = []
            manager = trickplay.TrickplayPreviewManager(None)
            manager._load_metadata = lambda _item, _abort: metadata

            def new_state(_item, _client, source, *_args):
                selected_sources.append(source)
                abort.set()
                return {}

            manager._new_state = new_state
            manager._close_state = lambda _state: None
            manager._clear_if_owned = lambda _state: None
            manager._run(item, 3, "playback-1", abort, root)
            self.assertEqual(selected_sources, ["manifest-source"])

    def test_missing_manifest_diagnostic_is_structured_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            item = {
                "Id": "private-item-id",
                "MediaSourceId": "private-source-id",
                "Type": "Episode",
                "Server": object(),
            }
            abort = threading.Event()
            messages = []
            trickplay.LOG = types.SimpleNamespace(
                warning=lambda *_args: None,
                info=lambda message, *args: messages.append(
                    message % args if args else message
                ),
            )
            manager = trickplay.TrickplayPreviewManager(None)
            manager._load_metadata = lambda _item, _abort: {"Trickplay": {}}

            def new_state(*_args):
                abort.set()
                return {}

            manager._new_state = new_state
            manager._close_state = lambda _state: None
            manager._clear_if_owned = lambda _state: None
            manager._run(item, 3, "private-playback", abort, root)

            diagnostics = "\n".join(messages)
            self.assertIn(
                "stage=metadata outcome=unavailable reason=no-manifest "
                "media=Episode",
                diagnostics,
            )
            for private_value in (
                "private-item-id",
                "private-source-id",
                "private-playback",
            ):
                self.assertNotIn(private_value, diagnostics)

    def test_metadata_startup_retries_then_recovers(self):
        metadata = {"Trickplay": {"source": {"320": INFO}}}
        outcomes = [RuntimeError("offline"), RuntimeError("warming"), metadata]
        calls = []

        def get_item(item_id):
            calls.append(item_id)
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        item = {
            "Id": "item-1",
            "Server": types.SimpleNamespace(
                jellyfin=types.SimpleNamespace(get_item=get_item)
            ),
        }
        warnings = []
        infos = []
        trickplay.LOG = types.SimpleNamespace(
            warning=lambda *args: warnings.append(args),
            info=lambda *args: infos.append(args),
        )
        trickplay.METADATA_TRANSIENT_RETRY_DELAYS = (0,)
        result = trickplay.TrickplayPreviewManager._load_metadata(
            item,
            threading.Event(),
        )
        self.assertIs(result, metadata)
        self.assertEqual(calls, ["item-1", "item-1", "item-1"])
        self.assertEqual(len(warnings), 1)
        self.assertEqual(len(infos), 1)

    def test_metadata_startup_abort_interrupts_long_backoff(self):
        first_failure = threading.Event()

        def get_item(_item_id):
            first_failure.set()
            raise RuntimeError("offline")

        item = {
            "Id": "item-1",
            "Server": types.SimpleNamespace(
                jellyfin=types.SimpleNamespace(get_item=get_item)
            ),
        }
        trickplay.METADATA_TRANSIENT_RETRY_DELAYS = (30,)
        abort = threading.Event()
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                trickplay.TrickplayPreviewManager._load_metadata(item, abort)
            )
        )
        worker.start()
        self.assertTrue(first_failure.wait(1))
        abort.set()
        worker.join(0.25)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [None])

    def test_persistent_metadata_failures_log_at_bounded_cadence(self):
        metadata = {"Id": "item-1"}
        remaining_failures = [5]

        def get_item(_item_id):
            if remaining_failures[0]:
                remaining_failures[0] -= 1
                raise RuntimeError("offline")
            return metadata

        item = {
            "Id": "item-1",
            "Server": types.SimpleNamespace(
                jellyfin=types.SimpleNamespace(get_item=get_item)
            ),
        }
        warnings = []
        infos = []
        trickplay.LOG = types.SimpleNamespace(
            warning=lambda *args: warnings.append(args),
            info=lambda *args: infos.append(args),
        )
        trickplay.METADATA_TRANSIENT_RETRY_DELAYS = (0,)
        times = iter((0, 10, 30, 31, 60))
        result = trickplay.TrickplayPreviewManager._load_metadata(
            item,
            threading.Event(),
            clock=lambda: next(times),
        )
        self.assertIs(result, metadata)
        self.assertEqual(len(warnings), 3)
        self.assertEqual(len(infos), 1)

    def test_terminal_metadata_http_failure_does_not_retry(self):
        class HttpError(RuntimeError):
            response = types.SimpleNamespace(status_code=404)

        calls = []

        def get_item(item_id):
            calls.append(item_id)
            raise HttpError("not found")

        item = {
            "Id": "item-1",
            "Server": types.SimpleNamespace(
                jellyfin=types.SimpleNamespace(get_item=get_item)
            ),
        }
        warnings = []
        trickplay.LOG = types.SimpleNamespace(
            warning=lambda *args: warnings.append(args),
            info=lambda *_args: None,
        )
        result = trickplay.TrickplayPreviewManager._load_metadata(
            item,
            threading.Event(),
        )
        self.assertIsNone(result)
        self.assertEqual(calls, ["item-1"])
        self.assertEqual(len(warnings), 1)
        message = warnings[0][0] % warnings[0][1:]
        self.assertEqual(
            message,
            "HTPC trickplay stage=metadata outcome=unavailable "
            "reason=http-404",
        )
        self.assertNotIn("item-1", message)
        self.assertNotIn("not found", message)

    def test_metadata_failure_finishing_after_abort_is_not_logged(self):
        started = threading.Event()
        release = threading.Event()

        def get_item(_item_id):
            started.set()
            self.assertTrue(release.wait(1))
            raise RuntimeError("offline")

        item = {
            "Id": "item-1",
            "Server": types.SimpleNamespace(
                jellyfin=types.SimpleNamespace(get_item=get_item)
            ),
        }
        warnings = []
        infos = []
        trickplay.LOG = types.SimpleNamespace(
            warning=lambda *args: warnings.append(args),
            info=lambda *args: infos.append(args),
        )
        abort = threading.Event()
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                trickplay.TrickplayPreviewManager._load_metadata(item, abort)
            )
        )
        worker.start()
        self.assertTrue(started.wait(1))
        abort.set()
        release.set()
        worker.join(0.25)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [None])
        self.assertEqual(warnings, [])
        self.assertEqual(infos, [])

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
                "consumer_nonce",
                "playback_epoch",
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

    def test_stationary_retry_wait_is_abort_aware(self):
        slot = trickplay.LatestRequestSlot()
        request = self.make_request()
        abort = threading.Event()
        slot.submit(request)
        self.assertIs(slot.take(abort), request)
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                slot.retry_after(request, 30, abort)
            )
        )
        worker.start()
        abort.set()
        worker.join(0.25)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertEqual(slot.pending_count, 0)

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
            contract = json.loads(store.values[trickplay.PREVIEW_CONTRACT])
            for key, value in latest["token"].items():
                self.assertEqual(contract[key], value)
            self.assertEqual(contract["status"], "ready")

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
            self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)
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
            self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)

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
                self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)

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
                lambda: self.activate_request(
                    store,
                    self.make_request(target="62"),
                ),
            )

            self.assertEqual(results, [False])
            self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)

    def test_sprite_cache_is_strictly_bounded(self):
        byte_cache = trickplay.ByteLruCache(5)
        self.assertTrue(byte_cache.put("a", b"aaa"))
        self.assertTrue(byte_cache.put("b", b"bbb"))
        self.assertEqual(byte_cache.keys(), ["b"])
        self.assertEqual(byte_cache.byte_size, 3)
        self.assertFalse(byte_cache.put("huge", b"123456"))
        self.assertTrue(byte_cache.remove("b"))
        self.assertEqual(byte_cache.byte_size, 0)

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
            self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)

            pending = state["request_slot"].take(threading.Event())
            self.assertIs(pending, new_request)
            self.assertTrue(
                manager._process_preview_request(
                    state,
                    pending,
                    threading.Event(),
                )
            )
            contract = json.loads(store.values[trickplay.PREVIEW_CONTRACT])
            self.assertEqual(
                contract["target_seconds"],
                new_request["token"]["target_seconds"],
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
            self.assertIn(trickplay.PREVIEW_CONTRACT, store.values)

    def test_exhausted_transient_publishes_temporary_failure(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request()
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            trickplay.PREVIEW_RETRY_BACKOFFS = ()
            trickplay.PREVIEW_STATIONARY_RETRY_SECONDS = 0
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)
            calls = []

            def resolve(_state, frame, _abort, foreground):
                calls.append((frame, foreground))
                if len(calls) == 1:
                    raise trickplay.PreviewFailure("temporary", True)
                return source

            manager._resolve_frame_path = resolve
            self.assertFalse(
                manager._process_preview_request(
                    state,
                    request,
                    threading.Event(),
                )
            )
            self.assertEqual(state["request_slot"].pending_count, 0)
            contract = json.loads(store.values[trickplay.PREVIEW_CONTRACT])
            self.assertEqual(contract["status"], "temporarily-failed")
            self.assertEqual(calls, [(6, True)])

    def test_stationary_retry_never_replaces_newer_target(self):
        slot = trickplay.LatestRequestSlot()
        failed = self.make_request(target="60")
        latest = self.make_request(target="80")
        abort = threading.Event()
        started = threading.Event()
        results = []
        slot.submit(failed)
        self.assertIs(slot.take(abort), failed)

        def retry():
            started.set()
            results.append(slot.retry_after(failed, 30, abort))

        worker = threading.Thread(target=retry)
        worker.start()
        self.assertTrue(started.wait(1))
        slot.submit(latest)
        worker.join(0.25)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [False])
        self.assertIs(slot.take(abort), latest)

    def test_preview_failure_diagnostics_are_rate_limited(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request()
            state = self.make_process_state(root, request)
            warnings = []
            trickplay.LOG = types.SimpleNamespace(
                warning=lambda *args: warnings.append(args)
            )
            error = trickplay.PreviewFailure("offline", True)
            manager = trickplay.TrickplayPreviewManager(None)

            self.assertTrue(manager._log_preview_failure(state, request, error))
            self.assertFalse(manager._log_preview_failure(state, request, error))
            self.assertEqual(len(warnings), 1)
            message = warnings[0][0] % warnings[0][1:]
            self.assertEqual(
                message,
                "HTPC trickplay stage=producer outcome=unavailable "
                "reason=unavailable transient=True",
            )
            self.assertNotIn("offline", message)

            state["preview_failure_diagnostics"][("producer", "unavailable")] -= (
                trickplay.PREVIEW_DIAGNOSTIC_INTERVAL_SECONDS + 1
            )
            self.assertTrue(manager._log_preview_failure(state, request, error))
            self.assertEqual(len(warnings), 2)

            different_stage = trickplay.PreviewFailure(
                "private path detail",
                True,
                stage="decode",
                reason="invalid-image",
            )
            self.assertTrue(
                manager._log_preview_failure(state, request, different_stage)
            )
            different_message = warnings[-1][0] % warnings[-1][1:]
            self.assertIn("stage=decode", different_message)
            self.assertNotIn("private path detail", different_message)

    def test_published_preview_has_complete_token_and_commit_order(self):
        with tempfile.TemporaryDirectory() as root:
            request = self.make_request(target="65")
            source = self.write_file(os.path.join(root, "frame"), b"frame")
            store = PropertyStore()
            self.activate_request(store, request)
            trickplay.window = store.window
            state = self.make_process_state(root, request)
            manager = trickplay.TrickplayPreviewManager(None)
            manager._resolve_frame_path = lambda *_args, **_kwargs: source

            self.assertTrue(
                manager._process_preview_request(
                    state,
                    request,
                    threading.Event(),
                )
            )
            token = json.loads(store.values[trickplay.PREVIEW_CONTRACT])
            for key, value in request["token"].items():
                self.assertEqual(token[key], value)
            self.assertEqual(token["status"], "ready")
            preview_keys = (trickplay.PREVIEW_CONTRACT,)
            published = tuple(
                key
                for key, value, clear in store.events
                if value is not None and not clear
            )
            self.assertEqual(published, preview_keys)
            self.assertEqual(
                set(store.values),
                {trickplay.SEEK_REQUEST}
                | set(preview_keys),
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
                abort.set()
            publisher.join(1)
            self.assertEqual(results, [False])
            self.assertNotIn(trickplay.PREVIEW_CONTRACT, store.values)

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

    def test_tile_request_uses_selected_manifest_media_source(self):
        calls = []
        manager = trickplay.TrickplayPreviewManager(None)
        manager._download = lambda _client, handler, params, session=None: (
            calls.append((handler, params, session)) or b"sprite"
        )
        exact_session = object()
        state = {
            "item_id": "item-1",
            "media_source_id": "manifest-source",
            "width": 320,
            "client": object(),
            "sprite_cache": trickplay.ByteLruCache(1024),
            "sprite_condition": threading.Condition(threading.RLock()),
            "inflight_sprites": set(),
            "exact_session": exact_session,
            "prefetch_session": object(),
        }
        result = manager._download_sprite_once(
            state,
            2,
            threading.Event(),
            foreground=True,
        )
        self.assertEqual(result, b"sprite")
        self.assertEqual(
            calls,
            [
                (
                    "Videos/item-1/Trickplay/320/2.jpg",
                    {"MediaSourceId": "manifest-source"},
                    exact_session,
                )
            ],
        )

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
