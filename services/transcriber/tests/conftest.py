import shutil
import subprocess
from pathlib import Path

import pytest

# A tone stands in for speech: 3 s of tone then 1 s of silence, repeated for 20 s,
# so the pauses sit at 3-4, 7-8, 11-12, 15-16 and 19-20 s.
SPEECH_LIKE = "aevalsrc='if(lt(mod(t,4),3),0.5*sin(2*PI*440*t),0)':s=16000:d=20"


@pytest.fixture
def meeting_wav(tmp_path: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")
    path = tmp_path / "meeting.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", SPEECH_LIKE, path],
        check=True,
    )
    return path
