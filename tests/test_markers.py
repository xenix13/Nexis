import os
import unittest

from seance.markers import from_chapters


def chaps(*spec):
    out, t = [], 0.0
    for title, length in spec:
        out.append({"start": t, "end": t + length, "title": title})
        t += length
    return out, t


class MarkersTest(unittest.TestCase):
    def intro(self, *spec):
        c, d = chaps(*spec)
        m = from_chapters(c, d)
        return (m["intro"]["start"] // 1000, m["intro"]["end"] // 1000) if "intro" in m else None

    def test_op_wins_over_intro(self):
        self.assertEqual(self.intro(("Intro", 95), ("OP", 90), ("Part A", 600), ("ED", 90), ("Preview", 15)),
                         (95, 185))
        self.assertEqual(self.intro(("Intro", 40), ("Opening", 89), ("Partie A", 600), ("Ending", 90)), (40, 129))

    def test_short_intro_then_unnamed_song(self):
        self.assertEqual(self.intro(("Intro", 150), ("Chapter 2", 90), ("Chapter 3", 600), ("Chapter 4", 90)),
                         (150, 240))
        self.assertEqual(self.intro(("Prologue", 120), ("Chapitre 2", 88), ("Chapitre 3", 700)), (120, 208))

    def test_intro_is_the_song(self):
        self.assertEqual(self.intro(("Intro", 90), ("Part A", 600), ("Credits", 90)), (0, 90))

    def test_western_title_sequence(self):
        self.assertEqual(self.intro(("Recap", 60), ("Intro", 30), ("Act 1", 900), ("Credits", 60)), (60, 90))

    def test_credits_prefers_song_length(self):
        c, d = chaps(("OP", 90), ("Part A", 600), ("Part B", 600), ("ED", 90), ("Credits", 20))
        self.assertEqual(from_chapters(c, d)["credits"]["start"], 1290000)

    def test_unnamed(self):
        c, d = chaps(("", 120), ("", 90), ("", 600), ("", 500), ("", 90), ("", 20))
        m = from_chapters(c, d)
        self.assertEqual((m["intro"]["start"], m["credits"]["start"]), (120000, 1310000))


if __name__ == "__main__":
    unittest.main()


class ChapterImagesTest(unittest.TestCase):
    """Chapitres annoncés après la fin réelle de la vidéo : pas d'erreur, et rien à refaire au passage suivant."""

    def test_chapter_past_end_of_video(self):
        import json
        import subprocess
        import tempfile
        import types
        from seance.chapters import ChapterImages
        from seance import media
        d = tempfile.mkdtemp()
        meta = os.path.join(d, "meta.txt")
        with open(meta, "w") as f:
            f.write(";FFMETADATA1\n")
            for s, e in ((0, 2000), (2000, 4000), (4000, 30000), (30000, 60000)):
                f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={s}\nEND={e}\ntitle=C{s}\n")
        raw, video = os.path.join(d, "raw.mkv"), os.path.join(d, "v.mkv")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=24", "-t", "5",
                        "-c:v", "libx264", "-preset", "ultrafast", raw], check=True)
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-i", meta, "-map", "0", "-map_chapters", "1",
                        "-c", "copy", video], check=True)
        probe = media.probe(video)
        self.assertGreater(probe["chapters"][-1]["start"], probe["duration"])  # chapitre après la fin
        item = {"id": 1, "key": video, "probe": json.dumps(probe), "file_size": os.path.getsize(video),
                "file_mtime": 1, "duration_ms": 5000}
        ci = ChapterImages(types.SimpleNamespace(data_dir=d))
        made = ci.extract_all(item)
        self.assertGreaterEqual(made, 3)
        self.assertTrue(ci.has_all(item))
        self.assertEqual(ci.extract_all(item), 0)  # tout est fait (ou noté impossible)
        self.assertTrue(os.path.isfile(ci.get(item, 0)))
