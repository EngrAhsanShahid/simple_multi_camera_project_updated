# This local 'livekit/' folder shadows the installed livekit SDK.
# We load the real SDK's 'rtc' subpackage directly from site-packages
# so that `from livekit import rtc` works inside publisher.py.
import importlib.util
import sys
from pathlib import Path

_local = Path(__file__).parent.resolve()

for _entry in sys.path:
    if not _entry:
        continue
    _rtc_init = (Path(_entry) / "livekit" / "rtc" / "__init__.py").resolve()
    if _rtc_init.exists() and _rtc_init.parents[1] != _local:
        _spec = importlib.util.spec_from_file_location(
            "livekit.rtc",
            str(_rtc_init),
            submodule_search_locations=[str(_rtc_init.parent)],
        )
        rtc = importlib.util.module_from_spec(_spec)
        sys.modules["livekit.rtc"] = rtc
        _spec.loader.exec_module(rtc)
        break
