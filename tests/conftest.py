import faulthandler
import sys

# If anything hangs (e.g. on the Windows build machine), print where and stop instead of waiting forever.
faulthandler.dump_traceback_later(240, exit=True, file=sys.stderr)
