from ui import window_position


class _Parent:
    def update_idletasks(self):
        pass

    def winfo_rootx(self):
        return 1900

    def winfo_rooty(self):
        return 200

    def winfo_width(self):
        return 1200

    def winfo_height(self):
        return 800


class _Window:
    def geometry(self, value):
        self.value = value


def test_popup_is_centered_and_clamped_to_owner_monitor(monkeypatch):
    monkeypatch.setattr(window_position, "_work_area",
                        lambda parent: (1920, 0, 3840, 1080))
    window = _Window()
    assert window_position.center_popup(window, _Parent(), 520, 130) == (2240, 535)
    assert window.value == "520x130+2240+535"


def test_popup_larger_than_work_area_is_clamped(monkeypatch):
    monkeypatch.setattr(window_position, "_work_area",
                        lambda parent: (1920, 0, 2500, 600))
    window = _Window()
    assert window_position.center_popup(window, _Parent(), 900, 800) == (1920, 0)
    assert window.value == "580x600+1920+0"
