from __future__ import absolute_import, division, print_function

from input_quarantine import InputQuarantine, canonical_physical_key
from seek_controller import RepeatGuard


class KodiCommands(object):
    def __init__(self, builtin):
        self.builtin = builtin

    def toggle_play(self):
        self.builtin("PlayerControl(Play)")

    def stop(self):
        self.builtin("PlayerControl(Stop)")

    def osd_action(self, action):
        self.builtin("Action(%s,videoosd)" % action)


class InputRouter(object):
    """Explicit source-aware routing; never infer focus after delivery."""

    PENDING_TRANSITIONS = frozenset(
        ("transport", "timeline", "timeline-up", "top")
    )

    def __init__(
        self,
        controller,
        player,
        presenter,
        chapters,
        commands,
        repeat_guard=None,
        input_quarantine=None,
    ):
        self.controller = controller
        self.player = player
        self.presenter = presenter
        self.chapters = chapters
        self.commands = commands
        self.repeat_guard = repeat_guard or RepeatGuard()
        self.input_quarantine = (
            InputQuarantine()
            if input_quarantine is None
            else input_quarantine
        )
        self.pending_transition = None

    def reset(self):
        """Discard navigation while preserving physical train state."""
        self.pending_transition = None

    def on_playback_boundary(self, timestamp, watermark=None):
        self.pending_transition = None
        self.input_quarantine.on_playback_boundary(timestamp, watermark)

    def clear(self):
        """Discard all transient input state during full shutdown or tests."""
        self.reset()
        self.repeat_guard.reset()
        self.input_quarantine.clear()

    def handle(self, action, timestamp, payload=None):
        payload = payload or {}
        physical_key = canonical_physical_key(action, payload)
        if self.input_quarantine.should_suppress(physical_key, timestamp):
            return True

        if action in ("left", "right"):
            direction = -1 if action == "left" else 1
            if self.controller.hidden_step(direction, timestamp):
                self.presenter.emphasize_timeline()
            return True

        if action in ("timeline-left", "timeline-right"):
            direction = -1 if action.endswith("left") else 1
            if self.controller.timeline_step(direction, timestamp):
                self.presenter.emphasize_timeline()
            return True

        if action == "timeline-focus":
            return True

        if action == "timeline-blur":
            return True

        if action == "timeline-confirm":
            if self.controller.manual:
                self.controller.confirm(timestamp)
                self._defer_transition("transport")
            else:
                self.controller.end_optimistic_skip(timestamp)
                self.commands.toggle_play()
            return True

        if action in ("timeline-back", "timeline-cancel"):
            if self.controller.active:
                self.controller.cancel(timestamp)
                self._defer_transition("transport")
            return True

        if action in ("timeline-up", "chapter-open"):
            return self._timeline_up()

        if action == "timeline-down":
            if self.controller.manual:
                return True
            self.controller.end_optimistic_skip()
            self.commands.osd_action("Down")
            return True

        if action == "chapter-select":
            # The dialog consumed this Select. Arm the shared train guard
            # before it closes so a held OK cannot become OSD Select.
            self.repeat_guard.arm("select", timestamp)
            return self._select_chapter(payload)

        if action == "chapter-focus":
            return self._focus_chapter(payload)

        if action == "chapter-exit":
            destination = payload.get("destination", "back")
            if destination == "back" and payload.get("arm_back"):
                # Physical Back was consumed by ChapterRail. Synthetic
                # contract-loss exits deliberately omit this marker.
                self.repeat_guard.arm("back", timestamp)
            if (
                self.controller.manual
                and self.controller.source == "chapter"
            ):
                self.controller.cancel(timestamp)
            # Preserve the requested layer destination throughout the
            # asynchronous owned-pause resume. Up goes to the top bar;
            # Down/Back return to the timeline.
            self._defer_transition(
                "top" if destination == "top" else "timeline"
            )
            return True

        if action == "primary":
            if not self.repeat_guard.accept("select", timestamp):
                return True
            if self.controller.manual:
                self.controller.confirm(timestamp)
                self._defer_transition("transport")
                return True
            self.controller.end_optimistic_skip(timestamp)
            try:
                pause_confirmed = bool(self.player.pause_for_osd())
            except Exception:
                pause_confirmed = False
            video_active = False
            if not pause_confirmed:
                try:
                    video_active = bool(self.player.video_active())
                except Exception:
                    pass
            if pause_confirmed or video_active:
                self.presenter.show_transport()
            return True

        if action == "osd-primary":
            if not self.repeat_guard.accept("select", timestamp):
                return True
            if self.controller.manual:
                self.controller.confirm(timestamp)
                self._defer_transition("transport")
                return True
            self.controller.end_optimistic_skip(timestamp)
            self.commands.osd_action("Select")
            return True

        if action == "osd-back":
            if not self.repeat_guard.accept("back", timestamp):
                return True
            if self.chapters.is_open:
                self.chapters.close()
                if self.controller.manual:
                    self.controller.cancel(timestamp)
                self._defer_transition("timeline")
            elif self.controller.active:
                dismiss_osd = bool(
                    getattr(self.controller, "back_dismisses_osd", False)
                )
                self.controller.cancel(timestamp)
                if dismiss_osd:
                    self.presenter.close_osd()
                else:
                    self._defer_transition("transport")
            else:
                self.presenter.close_osd()
            return True

        if action == "fullscreen-back":
            if not self.repeat_guard.accept("back", timestamp):
                return True
            if self.chapters.is_open:
                self.chapters.close()
                if self.controller.manual:
                    self.controller.cancel(timestamp)
                self._defer_transition("timeline")
            elif self.controller.active:
                dismiss_osd = bool(
                    getattr(self.controller, "back_dismisses_osd", False)
                )
                self.controller.cancel(timestamp)
                if dismiss_osd:
                    self.presenter.close_osd()
                else:
                    self._defer_transition("transport")
            elif self.presenter.osd_active():
                self.presenter.close_osd()
            else:
                self.commands.stop()
            return True

        if action == "osd-show":
            self.presenter.show_osd()
            return True

        return False

    def tick(self):
        if self.pending_transition and not self.controller.active:
            transition = self.pending_transition
            self.pending_transition = None
            if transition == "timeline-up":
                self._timeline_up()
            elif transition == "timeline":
                self.presenter.focus_timeline()
            elif transition == "top":
                self.presenter.focus_top_bar()
            else:
                self.presenter.focus_transport()

    def _defer_transition(self, transition):
        if transition not in self.PENDING_TRANSITIONS:
            raise ValueError(
                "unsupported pending transition: %s" % transition
            )
        self.pending_transition = transition

    def _timeline_up(self):
        # Scrub is intentionally modal. It must be confirmed or cancelled
        # before arrows can leak into top-bar controls or chapter browsing.
        if self.controller.manual:
            return True

        if self.controller.active:
            self.controller.end_optimistic_skip()
            self._defer_transition("timeline-up")
            return True
        snapshot = self.player.snapshot()
        current = float(snapshot.get("current", 0.0))
        if self.chapters.available():
            opened = (
                self.controller.begin_chapter_browse()
                and self.chapters.open(current)
            )
            if not opened:
                if self.controller.manual:
                    self.controller.cancel()
                self.presenter.focus_top_bar()
        else:
            self.presenter.focus_top_bar()
        return True

    def _select_chapter(self, chapter):
        # Selection is the commit action for the already-active chapter
        # transaction. Reject selections from any other scrub source.
        if not self.controller.manual or self.controller.source != "chapter":
            return True
        token, current_chapters = self.chapters.provider.load()
        if token != chapter.get("playback_token"):
            self._reject_chapter_selection()
            return True
        try:
            requested_start = float(chapter["start_seconds"])
        except (KeyError, TypeError, ValueError):
            self._reject_chapter_selection()
            return True
        valid = any(
            item["index"] == chapter.get("index")
            and abs(item["start_seconds"] - requested_start) < 0.001
            for item in current_chapters
        )
        if valid:
            if (
                self.controller.set_target(requested_start)
                and self.controller.confirm()
            ):
                self._defer_transition("transport")
                return True
        self._reject_chapter_selection()
        return True

    def _reject_chapter_selection(self):
        if self.controller.manual and self.controller.source == "chapter":
            self.controller.cancel()
        self._defer_transition("timeline")

    def _focus_chapter(self, chapter):
        if not self.controller.manual or self.controller.source != "chapter":
            return True
        token, current_chapters = self.chapters.provider.load()
        if token != chapter.get("playback_token"):
            return True
        for item in current_chapters:
            if item["index"] == chapter.get("index"):
                self.controller.set_target(item["start_seconds"])
                break
        return True
