"""共用元件的小工具：重建時移除並銷毀舊元件（不建立真正的 GTK 元件）。"""
import unittest

from sdm.ui.widgets import discard


class FakeWidget:
    def __init__(self): self.destroyed = False
    def destroy(self): self.destroyed = True


class FakeContainer:
    def __init__(self, children): self.children = list(children)
    def remove(self, child): self.children.remove(child)


class Discard(unittest.TestCase):
    def test_removed_widgets_are_destroyed(self):
        old = [FakeWidget(), FakeWidget()]
        keep = FakeWidget()
        container = FakeContainer(old + [keep])
        discard(container, old)
        self.assertEqual(container.children, [keep])
        self.assertTrue(all(widget.destroyed for widget in old))
        self.assertFalse(keep.destroyed)


if __name__ == "__main__":
    unittest.main()
