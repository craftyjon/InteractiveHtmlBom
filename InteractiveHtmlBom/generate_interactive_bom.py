#!/usr/bin/python3
from __future__ import absolute_import

import argparse
import os
import sys
# Add ../ to the path
# Works if this script is executed without installing the module
script_dir = os.path.dirname(os.path.abspath(os.path.realpath(__file__)))
sys.path.insert(0, os.path.dirname(script_dir))
# Pretend we are part of a module
# Avoids: ImportError: attempted relative import with no known parent package
__package__ = os.path.basename(script_dir)
__import__(__package__)


# python 2 and 3 compatibility hack
def to_utf(s):
    if isinstance(s, bytes):
        return s.decode('utf-8')
    else:
        return s


def main():
    create_wx_app = 'INTERACTIVE_HTML_BOM_NO_DISPLAY' not in os.environ

    import wx

    if create_wx_app:
        app = wx.App()
        if hasattr(wx, "APP_ASSERT_SUPPRESS"):
            app.SetAssertMode(wx.APP_ASSERT_SUPPRESS)
    elif hasattr(wx, "DisableAsserts"):
        wx.DisableAsserts()

    from .core import ibom
    from .core.config import Config
    from .ecad import get_parser_by_extension
    from .version import version
    from .errors import (ExitCodes, ParsingException, exit_error)

    parser = argparse.ArgumentParser(
            description='KiCad InteractiveHtmlBom plugin CLI.',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('file', nargs='?',
                        type=lambda s: to_utf(s),
                        help="KiCad PCB file")

    Config.add_options(parser, version)
    parser.add_argument('--kicad-ipc', action='store_true',
                        help="Connect to KiCad IPC API instead of loading board from a file")
    args = parser.parse_args()
    logger = ibom.Logger(cli=True)

    if args.kicad_ipc:
        from kipy import KiCad
        from kipy.errors import ConnectionError
        from .ecad.kicad_ipc import KiCadIPCParser

        try:
            kicad = KiCad(timeout_ms=250)
            board = kicad.get_board()
            config = Config(version, kicad.get_project(board.document).path)
            parser = KiCadIPCParser(board, config, logger)
        except ConnectionError as e:
            exit_error(logger, ExitCodes.ERROR_KICAD_IPC_NOT_CONNECTED,
                       f"KiCad IPC server is not running or not accessible: {e}")
    else:
        if not args.file:
            exit_error(logger, ExitCodes.ERROR_FILE_NOT_FOUND, "File argument is required.")

        if not os.path.isfile(args.file):
            exit_error(logger, ExitCodes.ERROR_FILE_NOT_FOUND,
                    "File %s does not exist." % args.file)

        print("Loading %s" % args.file)

        config = Config(version, os.path.dirname(os.path.abspath(args.file)))

        parser = get_parser_by_extension(
            os.path.abspath(args.file), config, logger)

    if args.show_dialog:
        if not create_wx_app:
            exit_error(logger, ExitCodes.ERROR_NO_DISPLAY,
                       "Can not show dialog when "
                       "INTERACTIVE_HTML_BOM_NO_DISPLAY is set.")
        try:
            ibom.run_with_dialog(parser, config, logger)
        except ParsingException as e:
            exit_error(logger, ExitCodes.ERROR_PARSE, e)
    else:
        config.set_from_args(args)
        try:
            ibom.main(parser, config, logger)
        except ParsingException as e:
            exit_error(logger, ExitCodes.ERROR_PARSE, str(e))
    return 0


if __name__ == "__main__":
    main()
